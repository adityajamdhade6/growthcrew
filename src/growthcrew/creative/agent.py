"""Ad images in three sizes, reviewed by a vision critic, at most three rounds.

Round by round: the model fills the template's slots from the draft's copy, the slots go
through the same guardrails as the copy, each size is rendered and measured, and the vision
critic scores what it sees. Checks measured in code cap the critic's scores, so it cannot pass
an image whose text is too small, clipped or low-contrast. Fixes go back to the slot filler.
Creatives that never pass are kept and marked, for the reviewer, like copy that never passes.
"""

import base64
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew import guardrails
from growthcrew.brain.context import BRAIN_NOTE, render_brain
from growthcrew.brain.models import Brain
from growthcrew.config import AgentRole
from growthcrew.creative.access import Finding, alt_text_problem, check_render
from growthcrew.creative.images import ImageGenerator, background_prompt
from growthcrew.creative.kit import Kit, load_kit
from growthcrew.creative.render import RATIOS, SIZES, Renderer
from growthcrew.db.models import Creative, Draft
from growthcrew.llm import LLM

MAX_ROUNDS = 3
PASS_SCORE = 8
CRITERIA = ("readability", "hierarchy", "brand_consistency", "thumb_stopping", "platform_rules")
# A failed code check holds the score it caps to this.
CAPPED = 5
SCALE_RANGE = (0.75, 1.3)
# Base text sizes in px per size: headline, subcopy, call to action.
BASE_PX = {"square": (84, 40, 38), "portrait": (92, 44, 40), "story": (104, 48, 44)}
Size = Literal["square", "portrait", "story"]

templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)

FILL_SYSTEM = f"""You turn an ad's copy into the words on an ad image, for a small business. \
The layout is a fixed brand template; you only fill its slots:
- headline: at most 8 words, the hook of the ad;
- subcopy: at most 18 words, or empty;
- cta: the button, at most 4 words;
- alt_text: one sentence describing the photo for someone who cannot see it (what is in it,
  not what it means), under 125 characters;
- scales: a text scale per size (square, portrait, story) between 0.75 and 1.3, 1.0 by default.
Use only claims, numbers and names that are in the ad copy. When you are given fixes from \
the reviewer, apply each one and change nothing else.

{BRAIN_NOTE}"""

CRITIC_SYSTEM = """You are the creative director reviewing ad images before a person approves \
them. You see the same ad rendered at 1:1 (square), 4:5 (portrait) and 9:16 (story), in that \
order, at about the size a phone shows them. Score each size from 1 to 10 on:
- readability: can the text be read at a glance on a phone, at thumbnail size?
- hierarchy: does the eye go headline, then image, then button?
- brand_consistency: does it follow the brand kit's colours, fonts and style rules?
- thumb_stopping: would it stop a scrolling thumb in a feed?
- platform_rules: not text-heavy, nothing where the platform's buttons sit, nothing a \
platform would reject.
For anything under 8, give a fix aimed at one slot (headline, subcopy, cta, alt_text or \
layout), saying what is wrong and exactly what to change. The measurements you are given \
were taken from the rendered page in code; trust them over your impression."""


class SizeScale(BaseModel):
    size: Size
    text_scale: float


class Slots(BaseModel):
    headline: str
    subcopy: str
    cta: str
    alt_text: str
    scales: list[SizeScale]

    def scale(self, size: str) -> float:
        value = next((s.text_scale for s in self.scales if s.size == size), 1.0)
        return min(SCALE_RANGE[1], max(SCALE_RANGE[0], value))

    def lines(self) -> list[str]:
        return [line for line in (self.headline, self.subcopy, self.cta) if line]


class Fix(BaseModel):
    target: Literal["headline", "subcopy", "cta", "alt_text", "layout"]
    problem: str
    instruction: str


class SizeReview(BaseModel):
    size: Size
    readability: int
    hierarchy: int
    brand_consistency: int
    thumb_stopping: int
    platform_rules: int
    fixes: list[Fix]


class VisionReview(BaseModel):
    reviews: list[SizeReview]


class RoundResult(BaseModel):
    round: int
    slots: Slots
    passed: bool
    scores: dict[str, dict[str, int]]
    findings: dict[str, list[Finding]]
    fixes: list[Fix]
    blocked: list[str] = []


class CreativeResult(BaseModel):
    draft_id: int
    passed: bool
    rounds: list[RoundResult]
    ai_generated: bool


# The critic sees each size this wide: about what a phone shows, and a third of the tokens.
CRITIC_WIDTH = 540


def shrink(png: bytes, width: int = CRITIC_WIDTH) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    if image.width <= width:
        return png
    height = round(image.height * width / image.width)
    out = io.BytesIO()
    image.resize((width, height), Image.LANCZOS).save(out, format="PNG", optimize=True)
    return out.getvalue()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def brain_facts(brand: Brain) -> str:
    proof = brand.proof
    items = [*proof.case_studies, *proof.testimonials, *proof.stats]
    parts = [f"{item.summary} {item.quote} {item.attribution}" for item in items]
    parts += [f"{p.name} {p.description} {p.price}" for p in brand.products]
    parts += [brand.business.pricing, brand.business.what_they_sell, brand.business.one_liner]
    return " ".join(parts)


def render_html(kit: Kit, slots: Slots, size: str, brand_name: str) -> str:
    width, height, top, bottom = SIZES[size]
    headline, subcopy, cta = (round(px * slots.scale(size)) for px in BASE_PX[size])
    return templates.get_template("ad.html.j2").render(
        kit=kit,
        slots=slots,
        width=width,
        height=height,
        pad_top=top,
        pad_bottom=bottom,
        sizes={"headline": headline, "subcopy": subcopy, "cta": cta},
        brand_name=brand_name,
        show_image=True,
    )


def describe_kit(kit: Kit) -> str:
    lines = [
        f"Colours: background {kit.background}, text {kit.text}, primary {kit.primary}, "
        f"accent {kit.accent}",
        f"Heading font: {kit.heading_font}. Body font: {kit.body_font}",
    ]
    if kit.style_rules:
        lines.append("Image style rules: " + "; ".join(kit.style_rules))
    if kit.do_examples:
        lines.append("Do: " + "; ".join(kit.do_examples))
    if kit.dont_examples:
        lines.append("Don't: " + "; ".join(kit.dont_examples))
    if not kit.from_brain:
        lines.append("(No brand kit has been set, so neutral defaults are used.)")
    return "\n".join(lines)


class CreativeAgent:
    def __init__(
        self,
        llm: LLM,
        engine: Engine,
        renderer: Renderer,
        root: Path,
        images: ImageGenerator | None = None,
    ) -> None:
        self.llm = llm
        self.engine = engine
        self.renderer = renderer
        self.root = root
        self.images = images

    def fill(self, draft: Draft, brand: Brain, previous: Slots | None, fixes: list[str]) -> Slots:
        if previous is None:
            user = f"{render_brain(brand)}\n\nAd copy:\n{draft.text}"
        else:
            user = (
                f"Ad copy:\n{draft.text}\n\nCurrent slots:\n{previous.model_dump_json(indent=2)}"
                "\n\nFixes to apply:\n" + "\n".join(f"- {fix}" for fix in fixes)
            )
        return self.llm.call(
            AgentRole.CREATIVE,
            system=FILL_SYSTEM,
            user=user,
            output_model=Slots,
            workspace=draft.workspace,
            tag=f"creative|{draft.piece_id}",
        )

    def _background(self, kit: Kit, brand: Brain, folder: Path) -> tuple[Kit, dict]:
        """A generated background when there is no product photo and AI imagery is on."""
        if kit.product_image or self.images is None:
            return kit, {}
        image = self.images.generate(background_prompt(brand.business.what_they_sell,
                                                        kit.style_rules))  # fmt: skip
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"ai-{text_hash(image.prompt)}.png"
        path.write_bytes(image.png)
        meta = image.model_dump(exclude={"png"}) | {"file": path.name}
        (folder / f"{path.stem}.json").write_text(json.dumps(meta, indent=2))
        uri = "data:image/png;base64," + base64.b64encode(image.png).decode()
        return replace(kit, product_image=uri, product_image_name=path.name,
                       product_image_alt=""), meta  # fmt: skip

    def run(self, draft: Draft, brand: Brain, max_rounds: int = MAX_ROUNDS) -> CreativeResult:
        if draft.content_type != "ad":
            raise ValueError("Ad images are made from ad drafts only")
        folder = self.root / draft.workspace / "creative" / str(draft.id)
        kit, image_meta = self._background(load_kit(brand, self.root), brand, folder)
        facts = f"{draft.text} {brain_facts(brand)}"
        name = brand.business.name or brand.workspace
        rounds: list[RoundResult] = []
        slots = self.fill(draft, brand, None, [])
        for number in range(1, max_rounds + 1):
            if kit.product_image_alt:
                # The owner's own description of their photo wins over the model's.
                slots.alt_text = kit.product_image_alt
            result = self._round(number, draft, brand, kit, slots, facts, name, folder, image_meta)
            rounds.append(result)
            if result.passed or number == max_rounds:
                break
            todo = result.blocked + [
                f"[{fix.target}] {fix.problem}: {fix.instruction}" for fix in result.fixes
            ]
            todo += [f"[{size}] {f.message}" for size, items in result.findings.items()
                     for f in items]  # fmt: skip
            slots = self.fill(draft, brand, slots, todo)
        return CreativeResult(
            draft_id=draft.id,
            passed=rounds[-1].passed,
            rounds=rounds,
            ai_generated=bool(image_meta),
        )

    def _round(
        self,
        number: int,
        draft: Draft,
        brand: Brain,
        kit: Kit,
        slots: Slots,
        facts: str,
        name: str,
        folder: Path,
        image_meta: dict,
    ) -> RoundResult:
        violations = guardrails.check(slots.lines(), brand, facts)
        if violations:
            guardrails.log_blocks(self.engine, draft.workspace, draft.piece_id, "creative",
                                  violations)  # fmt: skip
            # The words on the image break a guardrail: nothing is rendered or reviewed.
            return RoundResult(
                round=number, slots=slots, passed=False, scores={}, findings={}, fixes=[],
                blocked=[f"[guardrail] {v.reason}: '{v.excerpt}'" for v in violations],
            )  # fmt: skip

        colours = {
            "headline": (kit.text, kit.background),
            "subcopy": (kit.text, kit.background),
            "cta": (kit.cta_text, kit.primary),
        }
        if not slots.subcopy:
            colours.pop("subcopy")
        pngs: dict[str, bytes] = {}
        findings: dict[str, list[Finding]] = {}
        for size in SIZES:
            width, height, _, _ = SIZES[size]
            png, measure = self.renderer.render(render_html(kit, slots, size, name), width, height)
            pngs[size] = png
            findings[size] = check_render(measure, colours, RATIOS[size])
        blocked = []
        if kit.product_image and (
            problem := alt_text_problem(slots.alt_text, kit.product_image_name)
        ):
            blocked.append(f"[alt_text] {problem}")

        notes = "\n".join(
            f"{RATIOS[size]} ({size}): "
            + ("; ".join(f.message for f in items) if items else "all measured checks pass")
            for size, items in findings.items()
        )
        review = self.llm.call(
            AgentRole.VISION_CRITIC,
            system=CRITIC_SYSTEM,
            user=f"Brand kit:\n{describe_kit(kit)}\n\nAd copy the image is made from:\n"
            f"{draft.text}\n\nWords on the image: {slots.model_dump_json()}\n\n"
            f"Measured in code:\n{notes}",
            output_model=VisionReview,
            images=[shrink(pngs[size]) for size in SIZES],
            workspace=draft.workspace,
            tag=f"vision|{draft.piece_id}",
        )
        by_size = {r.size: r for r in review.reviews}
        scores: dict[str, dict[str, int]] = {}
        fixes: list[Fix] = []
        for size in SIZES:
            reviewed = by_size.get(size)
            if reviewed is None:
                # A size the critic did not review cannot pass.
                scores[size] = {criterion: 0 for criterion in CRITERIA}
                continue
            row = {
                criterion: max(1, min(10, getattr(reviewed, criterion))) for criterion in CRITERIA
            }
            for finding in findings[size]:
                if finding.caps in row:
                    row[finding.caps] = min(row[finding.caps], CAPPED)
            scores[size] = row
            fixes += reviewed.fixes
        passed = not blocked and all(min(row.values()) >= PASS_SCORE for row in scores.values())

        folder.mkdir(parents=True, exist_ok=True)
        with Session(self.engine) as session:
            for size in SIZES:
                path = folder / f"r{number}-{size}.png"
                path.write_bytes(pngs[size])
                session.add(
                    Creative(
                        workspace=draft.workspace,
                        draft_id=draft.id,
                        text_hash=text_hash(draft.text),
                        size=size,
                        round=number,
                        path=str(path.relative_to(self.root)),
                        slots=slots.model_dump_json(),
                        scores=json.dumps(scores[size]),
                        fixes=json.dumps(
                            [fix.model_dump() for fix in by_size[size].fixes]
                            if size in by_size
                            else []
                        ),  # fmt: skip
                        checks=json.dumps(
                            [f.model_dump() for f in findings[size]]
                            + [{"check": "alt_text", "caps": "none", "message": b} for b in blocked]
                        ),  # fmt: skip
                        passed=passed,
                        ai_generated=bool(image_meta),
                        image_meta=json.dumps(image_meta),
                    )
                )
            session.commit()
        return RoundResult(round=number, slots=slots, passed=passed, scores=scores,
                           findings=findings, fixes=fixes, blocked=blocked)  # fmt: skip


def latest(engine: Engine, draft_id: int) -> dict:
    """The newest round of a draft's creatives, every round's scores, for the review panel."""
    from sqlmodel import select

    with Session(engine) as session:
        rows = session.exec(
            select(Creative).where(Creative.draft_id == draft_id).order_by(Creative.round)
        ).all()
        draft = session.get(Draft, draft_id)
    if not rows:
        return {"draft_id": draft_id, "rounds": 0, "sizes": [], "history": []}
    last = max(row.round for row in rows)
    current = text_hash(draft.text) if draft else ""
    sizes = [
        {
            "size": row.size,
            "ratio": RATIOS[row.size],
            "url": f"/drafts/{draft_id}/creatives/{row.size}.png?round={row.round}",
            "scores": json.loads(row.scores),
            "fixes": json.loads(row.fixes),
            "checks": json.loads(row.checks),
            "passed": row.passed,
            "ai_generated": row.ai_generated,
            "alt_text": json.loads(row.slots).get("alt_text", ""),
        }
        for row in rows
        if row.round == last
    ]
    history = [
        {"round": number, "passed": all(r.passed for r in rows if r.round == number),
         "lowest": min((min(json.loads(r.scores).values(), default=0) for r in rows
                        if r.round == number), default=0)}
        for number in sorted({row.round for row in rows})
    ]  # fmt: skip
    return {
        "draft_id": draft_id,
        "rounds": last,
        "passed": all(item["passed"] for item in sizes),
        "stale": any(row.text_hash != current for row in rows if row.round == last),
        "slots": json.loads(rows[-1].slots),
        "sizes": sizes,
        "history": history,
    }
