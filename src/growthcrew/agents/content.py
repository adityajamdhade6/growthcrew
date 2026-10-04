"""Content agent: StrategyDoc + brain + request -> drafts, revised through the critic loop."""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, create_model
from sqlmodel import Session

from growthcrew import guardrails
from growthcrew.agents.content_models import (
    Batch,
    BatchPlan,
    PieceRecord,
    Version,
)
from growthcrew.agents.critic import CriticAgent
from growthcrew.agents.strategy_models import StrategyDoc
from growthcrew.brain.context import BRAIN_NOTE, render_brain
from growthcrew.brain.models import Brain
from growthcrew.config import AgentRole
from growthcrew.content.checks import allowed_facts
from growthcrew.content.templates import TEMPLATES
from growthcrew.content.types import (
    ANGLES,
    Angle,
    BlogArticle,
    ContentRequest,
    Outline,
    PieceMetadata,
    SEOBrief,
)
from growthcrew.db.models import ContentRevision
from growthcrew.llm import LLM
from growthcrew.memory import playbook
from growthcrew.versions import prompt_version

WORKSPACES_DIR = Path("workspaces")
MAX_ROUNDS = 3

SYSTEM = """You are the content writer on a small-business marketing team. You write one \
piece at a time from the brand's strategy, in the brand's voice. An editor scores each draft \
and the business owner approves the final version; nothing you write is published directly.

- Voice: follow the brand voice guide in the brain: the tone sliders, the sentence length \
rule, the do and don't words, and the guide's rules. The example passages show the target.
- Proof: use a statistic, customer name or testimonial only if it appears under proof in the \
brain, worded as it appears there. If the brain has no proof for a point, make the point \
without one. Never invent a number, a customer or a quote.
- Be specific. Say what the product does and for whom, in plain words. Cut any sentence that \
would be equally true of a competitor.
- Avoid stock marketing phrasing such as "unlock", "game-changer", "seamless", "elevate" or \
"in today's fast-paced world", and every phrase the brand has banned.
- metadata: name the messaging pillar the piece serves, the persona it targets, its single \
call to action, and the hypothesis it tests, written as "If we <approach>, then <metric> will \
<move> because <reason>". Tie the hypothesis to one of the strategy's experiments where one \
fits."""

ANGLE_BRIEFS: dict[Angle, str] = {
    "pain": "Angle: pain. Open with the problem the customer has today, in their words.",
    "outcome": "Angle: outcome. Open with what the customer's situation looks like once the "
    "problem is solved.",
    "social_proof": "Angle: social proof. Open with a real customer result or testimonial "
    "from the brain's proof, quoted exactly.",
}

PLAN_PROMPT = """Plan a {weeks}-week content batch ({days} days) for this brand: at most \
{max_items} items. Follow the strategy's 90-day channel plan (use only channels it funds, \
weighted roughly by budget share), cover each messaging pillar at least once, and spread items \
across funnel stages in proportion to the plan. For each item give the day (1 to {days}), the \
request, and a one-line rationale tied to the strategy. `pillar` must be the exact message of \
one of the three messaging pillars. Use content type "ad" for paid placements, with the \
platform set."""


@lru_cache
def draft_model(body_model: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{body_model.__name__}Draft", metadata=(PieceMetadata, ...), body=(body_model, ...)
    )


def strategy_context(doc: StrategyDoc) -> str:
    house = doc.messaging_house
    lines = [
        f"Positioning: {doc.positioning.positioning_statement}",
        f"Core message: {house.core_message.text}",
        "Messaging pillars:",
    ]
    for pillar in house.pillars:
        lines.append(f"- {pillar.message}")
        lines += [f"    proof point: {point.text}" for point in pillar.proof_points]
    lines += ["ICP priorities:", *(f"- {i.segment}: {i.rationale}" for i in doc.icp_priorities)]
    lines += ["Content pillars:", *(f"- {p.name}: {p.description}" for p in doc.content_pillars)]
    lines += ["Experiments:", *(f"- {e.name}: {e.hypothesis}" for e in doc.experiments)]
    lines.append("90-day channel plan:")
    for stage in doc.channel_plan.stages:
        for play in stage.channels:
            lines.append(f"- {stage.stage}: {play.channel}, {play.tactic} ({play.budget_pct}%)")
    return "\n".join(lines)


class ContentAgent:
    role = AgentRole.CONTENT

    def __init__(
        self,
        llm: LLM,
        critic: CriticAgent | None = None,
        root: Path = WORKSPACES_DIR,
        max_rounds: int = MAX_ROUNDS,
    ) -> None:
        self.llm = llm
        self.critic = critic or CriticAgent(llm)
        self.root = root
        self.max_rounds = max_rounds

    def _context(self, brand: Brain, strategy: StrategyDoc) -> str:
        return f"{render_brain(brand)}\n\nStrategy:\n{strategy_context(strategy)}"

    def angles(self, request: ContentRequest, brand: Brain) -> tuple[list[Angle | None], list[str]]:
        """Three angles for anything A/B tested, otherwise a single piece."""
        template = TEMPLATES[request.content_type]
        ab_test = template.ab_tested if request.ab_test is None else request.ab_test
        if not ab_test:
            return [request.angle], []
        proof = brand.proof
        if proof.case_studies or proof.testimonials or proof.stats:
            return list(ANGLES), []
        note = f"{template.name}: no social-proof variant, because the brain has no proof to quote."
        return ["pain", "outcome"], [note]

    def write_piece(
        self,
        request: ContentRequest,
        brand: Brain,
        strategy: StrategyDoc,
        piece_id: str,
        angle: Angle | None = None,
        day: int | None = None,
    ) -> PieceRecord:
        """Draft one piece, then revise it with the critic until it passes or rounds run out."""
        workspace = brand.workspace
        template = TEMPLATES[request.content_type]
        model = draft_model(template.body_model)
        record = PieceRecord(id=piece_id, day=day, request=request, angle=angle)

        # Tagging every call with the piece lets the cost dashboard price each piece.
        tag = f"{request.content_type}|{piece_id}"
        facts = allowed_facts(brand, strategy)
        system = f"{SYSTEM}\n\n{BRAIN_NOTE}"
        record.prompt_version = prompt_version(system)
        # The brand's own past winners and the playbook rules that currently hold.
        remembered, record.memory = playbook.writer_context(
            self.llm.engine, workspace, request.content_type,
            f"{request.topic} {request.pillar} {request.goal} {request.audience}",
        )  # fmt: skip
        chat = self.llm.conversation(self.role, system=system, workspace=workspace, tag=tag)
        brief = (
            f"{self._context(brand, strategy)}\n\n"
            f"Content request: {request.model_dump_json()}\n\n"
            f"{template.prompt()}\n\n"
            f"{ANGLE_BRIEFS[angle] if angle else ''}\n\n{remembered}"
        ).strip()
        if template.body_model is BlogArticle:
            record.seo_brief = chat.extract(
                f"{brief}\n\nStep 1 of 3: write the SEO brief for this article. Title tag at "
                "most 60 characters, meta description at most 160.",
                SEOBrief,
            )
            record.outline = chat.extract(
                "Step 2 of 3: write the outline: H2 sections with the points each will make.",
                Outline,
            )
            draft = chat.extract("Step 3 of 3: write the article from the outline.", model)
        else:
            draft = chat.extract(f"{brief}\n\nWrite the piece.", model)

        for round_number in range(1, self.max_rounds + 1):
            critique = self.critic.review(draft.body, request, brand, strategy, workspace, tag)
            violations = guardrails.check(draft.body.lines(), brand, facts)
            guardrails.log_blocks(self.llm.engine, workspace, piece_id, "draft", violations)
            record.versions.append(
                Version(
                    round=round_number,
                    metadata=draft.metadata,
                    body=draft.body.model_dump(),
                    text="\n".join(draft.body.lines()),
                    critique=critique,
                    guardrail_violations=violations,
                )
            )
            self._log(workspace, record)
            if critique.passed or round_number == self.max_rounds:
                break
            edits = "\n".join(
                f'- Line {edit.line} ({edit.criterion}): "{edit.original}" -> '
                f"{edit.suggestion}. Reason: {edit.reason}"
                for edit in critique.edits
            )
            scores = ", ".join(f"{name} {score}" for name, score in critique.scores().items())
            draft = chat.extract(
                f"The editor scored this draft: {scores}. Every score must reach 8.\n\n"
                f"Line edits:\n{edits or '- none given'}\n\n"
                f"Automated findings:\n"
                + ("\n".join(f"- {item}" for item in critique.code_findings) or "- none")
                + "\n\nRevise the piece. Apply the edits, fix every automated finding, and keep "
                "what already works.",
                model,
            )
        return record

    def produce(
        self,
        request: ContentRequest,
        brand: Brain,
        strategy: StrategyDoc,
        piece_id: str = "piece",
        day: int | None = None,
    ) -> tuple[list[PieceRecord], list[str]]:
        angles, notes = self.angles(request, brand)
        records = [
            self.write_piece(
                request, brand, strategy, f"{piece_id}-{angle}" if angle else piece_id, angle, day
            )
            for angle in angles
        ]
        return records, notes

    def plan_batch(
        self, brand: Brain, strategy: StrategyDoc, weeks: int = 2, max_items: int = 8
    ) -> BatchPlan:
        days = weeks * 7
        plan = self.llm.call(
            self.role,
            system=f"{SYSTEM}\n\n{BRAIN_NOTE}",
            user=f"{self._context(brand, strategy)}\n\n"
            + PLAN_PROMPT.format(weeks=weeks, days=days, max_items=max_items),
            output_model=BatchPlan,
            workspace=brand.workspace,
        )
        items = [item for item in plan.items if 1 <= item.day <= days][:max_items]
        return BatchPlan(items=sorted(items, key=lambda item: item.day))

    def run_batch(
        self, brand: Brain, strategy: StrategyDoc, weeks: int = 2, max_items: int = 8
    ) -> Batch:
        plan = self.plan_batch(brand, strategy, weeks, max_items)
        batch = Batch(workspace=brand.workspace, weeks=weeks, plan=plan.items, pieces=[])
        for number, item in enumerate(plan.items, 1):
            piece_id = f"{number:02d}-day{item.day:02d}-{item.request.content_type}"
            records, notes = self.produce(item.request, brand, strategy, piece_id, item.day)
            batch.pieces += records
            batch.notes += notes
        return batch

    def _log(self, workspace: str, record: PieceRecord) -> None:
        version = record.final
        scores = version.critique.scores()
        with Session(self.llm.engine) as session:
            session.add(
                ContentRevision(
                    workspace=workspace,
                    piece_id=record.id,
                    content_type=record.request.content_type,
                    angle=record.angle,
                    round=version.round,
                    min_score=min(scores.values()),
                    passed=version.critique.passed,
                    scores=", ".join(f"{name}={score}" for name, score in scores.items()),
                    text=version.text,
                )
            )
            session.commit()
