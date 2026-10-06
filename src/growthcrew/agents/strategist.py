"""Strategist: brain + research -> StrategyDoc, with one devil's-advocate revision."""

import json
from pathlib import Path

from pydantic import BaseModel, create_model

from growthcrew.agents.learning_models import ProposedChange, Ruling, Rulings, WeeklyLearnings
from growthcrew.agents.research_models import ResearchReport
from growthcrew.agents.strategy_models import (
    Change,
    Critique,
    EvidenceItem,
    Priorities,
    RevisionLog,
    StrategyCore,
    StrategyDoc,
)
from growthcrew.brain.context import BRAIN_NOTE
from growthcrew.brain.models import FIELD_PATHS, Brain
from growthcrew.config import AgentRole
from growthcrew.frameworks import FRAMEWORKS
from growthcrew.frameworks.funnel import FunnelPlan
from growthcrew.frameworks.jtbd import JobsToBeDone
from growthcrew.frameworks.messaging_house import MessagingHouse
from growthcrew.frameworks.positioning import Positioning
from growthcrew.frameworks.test_and_learn import TestPlan
from growthcrew.llm import LLM, Conversation
from growthcrew.memory import playbook
from growthcrew.monitor import signals as monitor_signals

WORKSPACES_DIR = Path("workspaces")
EXPERIMENTS = 5
PILLARS = 3

SYSTEM = """You are the marketing strategist on a small-business marketing team. You turn a \
brand brain and a research report into a strategy that an owner with limited time and budget \
can act on in 90 days. A human approves it before anything is done with it.

You will be given a numbered evidence list, then asked to apply one framework at a time. \
Later frameworks build on your earlier answers.

Every recommendation has a `support` field. Put in it the IDs of the evidence items that \
justify the recommendation, exactly as written in the list (for example "learning:2" or \
"brain:icp.pains"). A recommendation with no support is flagged to the reviewer, so if the \
evidence does not justify something, leave it out or make it an experiment instead. Never \
invent an ID, a statistic, a customer or a competitor fact.

Commit to choices. A strategy that keeps every option open is not useful to a business that \
can only afford to do three things."""

PRIORITIES_PROMPT = """Now complete the strategy with the three parts no framework covers:
- icp_priorities: the customer segments to pursue, in priority order, each with the reason.
- content_pillars: 3 to 5 themes for content, each tied to a messaging pillar or a customer \
job, with a few example topics.
- kpis: the 4 to 6 numbers the owner should watch over 90 days. Give the baseline if the \
evidence contains one; otherwise write "unknown, measure in days 1-30" and express the target \
relative to it. Do not invent a baseline."""

DEVIL_SYSTEM = """You are the devil's advocate on a marketing team. A strategist has drafted \
a strategy for a small business and your job is to find where it would fail, before the owner \
spends money on it. You are given the same evidence the strategist had, the quality criteria \
for each framework, and the draft.

Look for:
- weak_assumptions: recommendations that rest on inferred or low-confidence evidence, on a \
single source, or on support that does not actually say what the recommendation needs it to \
say. Check the cited evidence items against the claim.
- missing_risks: things the strategy ignores, such as a competitor response, a channel the \
business cannot staff, a budget split that starves a stage, experiments too small to reach a \
decision, or KPI targets with no baseline.

Be specific: name the section, say what is wrong and why it matters to this business, and \
propose a fix. Skip generic advice that would apply to any strategy. If a part of the draft \
is sound, do not manufacture a criticism of it."""

REVISE_PROMPT = """A devil's advocate has reviewed your draft. Their critique:

{critique}

Automated checks on the draft found:
{issues}

Decide how to respond to each critique point: accept it, partly accept it, or reject it with \
a reason. Rejecting a point is fine when the evidence is on your side. Then you will be asked \
for each section again. First, write the revision log."""


def build_evidence(
    brand: Brain,
    research: ResearchReport,
    rules: list | None = None,
    signals: list | None = None,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for path in FIELD_PATHS:
        if brand.is_empty(path):
            continue
        value = brand.get(path)
        if isinstance(value, list):
            text = "; ".join(v if isinstance(v, str) else v.model_dump_json() for v in value)
        else:
            text = value if isinstance(value, str) else value.model_dump_json()
        meta = brand.fields.get(path)
        quality = meta.status if meta else "inferred"
        if meta and meta.status == "inferred":
            quality += f", {meta.confidence} confidence"
        items.append(EvidenceItem(id=f"brain:{path}", text=text, quality=quality))
    for number, learning in enumerate(research.brief.learnings, 1):
        items.append(
            EvidenceItem(
                id=f"learning:{number}",
                text=f"{learning.insight} ({learning.why_it_matters})",
                source_url=", ".join(learning.source_urls),
                quality=f"{learning.confidence} confidence",
            )
        )
    for number, claim in enumerate(research.claims(), 1):
        items.append(
            EvidenceItem(id=f"claim:{number}", text=claim.statement, source_url=claim.source_url)
        )
    for number, theme in enumerate(research.voice_of_customer.themes, 1):
        quotes = " | ".join(f'"{quote.text}"' for quote in theme.quotes)
        items.append(
            EvidenceItem(
                id=f"voc:{number}",
                text=f"Customer theme '{theme.theme}' ({theme.frequency} quotes): {quotes}",
                source_url=theme.quotes[0].source_url if theme.quotes else "",
            )
        )
    # Patterns found in this brand's own published results.
    for rule in rules or []:
        items.append(
            EvidenceItem(
                id=f"rule:{rule.id}", text=playbook.describe(rule), quality="playbook rule, active"
            )
        )
    # Monitor findings a person sent to the strategist from the Signals inbox.
    for signal in signals or []:
        sources = json.loads(signal.sources)
        items.append(
            EvidenceItem(
                id=f"signal:{signal.id}",
                text=f"{signal.title}: {signal.summary}",
                source_url=", ".join(source["url"] for source in sources),
                quality=f"monitor finding, {sources[0]['date'] if sources else 'undated'}",
            )
        )
    return items


def render_evidence(items: list[EvidenceItem]) -> str:
    return "\n".join(
        f"[{item.id}]{f' ({item.quality})' if item.quality else ''} {item.text}"
        + (f" <{item.source_url}>" if item.source_url else "")
        for item in items
    )


def check_support(node: object, known: set[str], label: str = "") -> list[str]:
    """Strip unknown evidence IDs and return the labels of recommendations left unsupported."""
    unsupported: list[str] = []
    if isinstance(node, list):
        for index, item in enumerate(node, 1):
            unsupported += check_support(item, known, f"{label}[{index}]")
    elif isinstance(node, BaseModel):
        fields = type(node).model_fields
        if "support" in fields:
            node.support = [ref for ref in node.support if ref in known]
            if not node.support:
                unsupported.append(label)
        for name in fields:
            if name != "support":
                child = f"{label}.{name}" if label else name
                unsupported += check_support(getattr(node, name), known, child)
    return unsupported


def check(core: StrategyCore, known: set[str]) -> list[str]:
    """Code checks on a strategy. Also ranks experiments by ICE."""
    issues = [f"No valid supporting evidence: {label}" for label in check_support(core, known)]
    plays = [play for stage in core.channel_plan.stages for play in stage.channels]
    total = sum(play.budget_pct for play in plays)
    if total != 100:
        issues.append(f"Channel plan budget split sums to {total}%, not 100%")
    core.experiments.sort(key=lambda experiment: -experiment.ice)
    if len(core.experiments) != EXPERIMENTS:
        issues.append(f"Expected {EXPERIMENTS} experiments, got {len(core.experiments)}")
    if len(core.messaging_house.pillars) != PILLARS:
        issues.append(f"Expected {PILLARS} pillars, got {len(core.messaging_house.pillars)}")
    return issues


REVIEW_SYSTEM = """You are the marketing strategist on a small-business marketing team. \
The analyst has proposed changes for next week based on last week's results. Rule on each \
one, with a reason the business owner can read later in the learning log.

How to rule:
- A change backed by a significant A/B test whose winner is the angle being shifted to is \
accepted by default. The test is the evidence; do not reject it because the result is \
surprising or because the variants are already being tested.
- If you see a specific risk in acting on it, state it in `risk`: for example, the result \
rests on one week of data, or the winning variant makes a claim the brand cannot yet prove. \
A stated risk turns the change into a partial shift that is confirmed against next week's \
data. It does not turn it into a rejection. Leave `risk` empty when there is none.
- A change marked blocked has been ruled out by a statistical check and must be rejected.
- Any other change (keep a test running, gather more data): accept it if it is sensible, \
reject it with a reason if it is not.

Give a ruling for every change ID."""

# A partial shift moves this fraction of what was proposed, and never less than MIN_SHARE.
PARTIAL_FRACTION = 0.5
MIN_SHARE = 10


def winning_readout(change: ProposedChange, learnings: WeeklyLearnings):
    """The pre-registered test, if any, in which the change's angle was called the winner."""
    if change.prefer_angle == "none":
        return None
    readouts = {readout.id: readout for readout in learnings.analysis.readouts}
    return next(
        (
            readouts[ref]
            for ref in change.evidence
            if ref in readouts
            and readouts[ref].status == "significant"
            and readouts[ref].winner == change.prefer_angle
        ),
        None,
    )


def decide_rulings(
    learnings: WeeklyLearnings, proposed: list[Ruling]
) -> tuple[list[Ruling], list[ProposedChange]]:
    """Apply the decision rule to the strategist's rulings.

    A called winner is accepted unless a specific risk is stated, in which case a partial
    shift is accepted and flagged for confirmation next week. A winner that damages a
    guardrail metric is held for a person. Returns the final rulings and the changes as they
    will be applied (a partial shift has a smaller share).
    """
    by_id = {ruling.change_id: ruling for ruling in proposed}
    rulings, changes = [], []
    for change in learnings.changes:
        raw = by_id.get(change.id)
        readout = winning_readout(change, learnings)
        if change.blocked_reason:
            final = Ruling(
                change_id=change.id, decision="rejected",
                reason=f"Overruled by the statistical check: {change.blocked_reason}",
            )  # fmt: skip
        elif readout is None:
            final = raw or Ruling(
                change_id=change.id, decision="rejected", reason="The strategist gave no ruling"
            )
        elif readout.guardrail_flags:
            final = Ruling(
                change_id=change.id, decision="held",
                reason=f"Held for the owner: {readout.winner} won {readout.title or readout.name} "
                f"on {readout.metric}, but it hurts a guardrail. "
                + " ".join(readout.guardrail_flags),
            )  # fmt: skip
        else:
            sure = readout.uncertainty.prob_best[readout.winner]
            likely = "over 99.9%" if sure > 0.999 else f"{sure:.1%}"
            won = (
                f"{readout.winner} won {readout.title or readout.name} "
                f"(+{readout.lift_pct:.0f}% over the runner-up, {likely} likely to be best)"
            )
            risk = raw.risk.strip() if raw else ""
            said = f" The strategist's view: {raw.reason}" if raw and raw.reason else ""
            if risk:
                share = max(MIN_SHARE, round(change.share_pct * PARTIAL_FRACTION))
                change = change.model_copy(update={"share_pct": share})
                final = Ruling(
                    change_id=change.id, decision="accepted_partial", risk=risk,
                    confirm_next_week=True,
                    reason=f"Accepted in part: {won}. Risk: {risk}. Shifting {share}% now and "
                    f"confirming against next week's data before going further.{said}",
                )  # fmt: skip
            elif raw is None or raw.decision == "rejected":
                final = Ruling(
                    change_id=change.id, decision="accepted",
                    reason=f"Accepted by default: {won}, and no specific risk was stated.{said}",
                )  # fmt: skip
            else:
                final = raw.model_copy(update={"decision": "accepted", "confirm_next_week": False})
        rulings.append(final)
        changes.append(change)
    return rulings, changes


# Sections a reviewer can comment on, and the schema each is rewritten in.
SECTIONS: dict[str, type[BaseModel]] = {
    "jobs": JobsToBeDone,
    "positioning": Positioning,
    "messaging_house": MessagingHouse,
    "channel_plan": FunnelPlan,
    "experiments": TestPlan,
    "priorities": Priorities,
}

COMMENT_PROMPT = """The business owner has read the strategy and commented on the \
"{section}" section:

<comment>
{comment}
</comment>

Revise that section only. Take the comment seriously: the owner knows the business better \
than the evidence does. If the comment asks for something the evidence contradicts, make the \
change that fits the evidence and explain why in what_changed. Keep every recommendation's \
support IDs valid. In what_changed, say in one or two sentences what you changed and why."""


class StrategyInput(BaseModel):
    brand: Brain
    research: ResearchReport


class StrategistAgent:
    role = AgentRole.STRATEGIST

    def __init__(
        self, llm: LLM, root: Path = WORKSPACES_DIR, mixlab=None, budget_total: float = 0.0
    ) -> None:
        self.llm = llm
        self.root = root
        # Optional MixLab client (integrations.mixlab). Its split is advice: a planned share
        # outside its interval becomes an issue to explain or fix, never an automatic change.
        self.mixlab, self.budget_total = mixlab, budget_total

    def _mix_issues(self, core: StrategyCore, workspace: str) -> list[str]:
        if self.mixlab is None or self.budget_total <= 0:
            return []
        from growthcrew.integrations import mixlab

        advice = self.mixlab.optimize_budget(workspace, self.budget_total)
        return mixlab.review_split(mixlab.plan_split(core), advice)

    def _write(self, chat: Conversation, lead: str = "", revised: bool = False) -> StrategyCore:
        """Apply each framework in turn, one structured output per framework."""
        prefix = (
            "Give the revised version of this section, applying the decisions in your "
            "revision log. Return it unchanged if no decision affects it.\n\n"
            if revised
            else ""
        )
        parts: dict[str, BaseModel] = {}
        for index, framework in enumerate(FRAMEWORKS):
            prompt = (lead if index == 0 else "") + prefix + framework.prompt()
            parts[framework.key] = chat.extract(prompt, framework.output_model)
        priorities = chat.extract(prefix + PRIORITIES_PROMPT, Priorities)
        test_plan: TestPlan = parts["test_and_learn"]
        return StrategyCore(
            jobs=parts["jtbd"],
            positioning=parts["positioning"],
            messaging_house=parts["messaging_house"],
            channel_plan=parts["funnel"],
            experiments=test_plan.experiments,
            icp_priorities=priorities.icp_priorities,
            content_pillars=priorities.content_pillars,
            kpis=priorities.kpis,
        )

    def critique(self, draft: StrategyCore, evidence: str, workspace: str | None) -> Critique:
        """The devil's advocate: a separate call that has not seen the strategist's reasoning."""
        criteria = "\n".join(
            f"{fw.name}:\n" + "\n".join(f"- {c}" for c in fw.quality_criteria) for fw in FRAMEWORKS
        )
        return self.llm.call(
            AgentRole.CRITIC,
            system=DEVIL_SYSTEM,
            user=f"Evidence:\n{evidence}\n\nQuality criteria:\n{criteria}\n\n"
            f"Draft strategy:\n{draft.model_dump_json(indent=2)}",
            output_model=Critique,
            workspace=workspace,
        )

    def review_changes(
        self, learnings: WeeklyLearnings, strategy: StrategyDoc, workspace: str | None = None
    ) -> tuple[list[Ruling], list[ProposedChange]]:
        """Rule on each proposed change. The decision rule in `decide_rulings` has the last
        word, so a confident winner cannot be turned down without a stated risk."""
        context = (
            f"Positioning: {strategy.positioning.positioning_statement}\n"
            f"Core message: {strategy.messaging_house.core_message.text}\n"
            "Experiments:\n"
            + "\n".join(f"- {e.name}: {e.hypothesis}" for e in strategy.experiments)
        )
        proposed = [change.model_dump() for change in learnings.changes]
        result = self.llm.call(
            self.role,
            system=REVIEW_SYSTEM,
            user=f"Strategy:\n{context}\n\n"
            f"Readouts:\n{[r.model_dump() for r in learnings.analysis.readouts]}\n\n"
            f"Proposed changes:\n{proposed}",
            output_model=Rulings,
            workspace=workspace,
        )
        return decide_rulings(learnings, result.rulings)

    def revise_section(
        self, doc: StrategyDoc, section: str, comment: str, workspace: str | None = None
    ) -> tuple[StrategyDoc, str]:
        """Rewrite one section in response to a reviewer's comment. Returns the new doc and
        the strategist's note on what changed."""
        if section not in SECTIONS:
            raise ValueError(f"Unknown section '{section}'. Use one of: {', '.join(SECTIONS)}")
        model = create_model(
            f"{section.title().replace('_', '')}Revision",
            section=(SECTIONS[section], ...),
            what_changed=(str, ...),
        )
        core = StrategyCore(**doc.model_dump(include=set(StrategyCore.model_fields)))
        result = self.llm.call(
            self.role,
            system=f"{SYSTEM}\n\n{BRAIN_NOTE}",
            user=f"Evidence:\n{render_evidence(doc.evidence)}\n\n"
            f"Current strategy:\n{core.model_dump_json(indent=2)}\n\n"
            + COMMENT_PROMPT.format(section=section, comment=comment),
            output_model=model,
            workspace=workspace or doc.workspace,
        )
        revised = result.section
        if section == "experiments":
            core.experiments = revised.experiments
        elif section == "priorities":
            core.icp_priorities = revised.icp_priorities
            core.content_pillars = revised.content_pillars
            core.kpis = revised.kpis
        else:
            setattr(core, section, revised)
        issues = check(core, {item.id for item in doc.evidence})
        log = doc.revision.model_copy(deep=True)
        log.changes.append(
            Change(
                critique_issue=f"Owner comment on {section}: {comment}",
                decision="accepted",
                change_made=result.what_changed,
                reason="Requested by the business owner",
            )
        )
        new = StrategyDoc(
            **core.model_dump(),
            workspace=doc.workspace,
            brand_name=doc.brand_name,
            evidence=doc.evidence,
            first_draft=doc.first_draft,
            critique=doc.critique,
            revision=log,
            issues=issues,
        )
        return new, result.what_changed

    def run(self, inp: StrategyInput, workspace: str | None = None) -> StrategyDoc:
        workspace = workspace or inp.brand.workspace
        rules = playbook.rules(self.llm.engine, workspace, "active")
        sent = monitor_signals.for_strategist(self.llm.engine, workspace)
        evidence = build_evidence(inp.brand, inp.research, rules, sent)
        known = {item.id for item in evidence}
        rendered = render_evidence(evidence)

        chat = self.llm.conversation(
            self.role, system=f"{SYSTEM}\n\n{BRAIN_NOTE}", workspace=workspace
        )
        draft = self._write(chat, lead=f"Evidence:\n{rendered}\n\n")
        draft_issues = check(draft, known) + self._mix_issues(draft, workspace)

        critique = self.critique(draft, rendered, workspace)
        revision = chat.extract(
            REVISE_PROMPT.format(
                critique=critique.model_dump_json(indent=2),
                issues="\n".join(f"- {issue}" for issue in draft_issues) or "- none",
            ),
            RevisionLog,
        )
        final = self._write(chat, revised=True)
        issues = check(final, known) + self._mix_issues(final, workspace)

        return StrategyDoc(
            **final.model_dump(),
            workspace=workspace,
            brand_name=inp.brand.business.name or inp.brand.workspace,
            evidence=evidence,
            first_draft=draft,
            critique=critique,
            revision=revision,
            issues=issues,
        )


def load_latest_strategy(workspace: str, root: Path = WORKSPACES_DIR) -> StrategyDoc:
    docs = sorted((root / workspace / "strategy").glob("*.json"))
    if not docs:
        raise FileNotFoundError(f"No strategy found for workspace '{workspace}'")
    return StrategyDoc.model_validate_json(docs[-1].read_text())
