"""Landing page heroes as real HTML, one per angle, ready for an A/B test.

Built in code from the approved landing hero drafts and the brand kit: no model call. The
current text of each draft is used, so a reviewer's edits are what goes on the page.
"""

from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.brain.models import Brain
from growthcrew.content.types import LandingHero
from growthcrew.creative.access import alt_text_problem, contrast_ratio
from growthcrew.creative.agent import templates
from growthcrew.creative.kit import load_kit
from growthcrew.db.models import Draft
from growthcrew.workflow import require_approved

PREFIXES = {
    "Headline:": "headline",
    "Subheadline:": "subheadline",
    "CTA button:": "primary_cta",
    "Social proof:": "social_proof_line",
}


def parse_hero(text: str) -> LandingHero:
    """A landing hero draft's text ("Headline: ...", "Point: ...") back into its fields."""
    fields: dict = {"supporting_points": []}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Point:"):
            fields["supporting_points"].append(line.removeprefix("Point:").strip())
            continue
        for prefix, name in PREFIXES.items():
            if line.startswith(prefix):
                fields[name] = line.removeprefix(prefix).strip()
    for name in ("headline", "subheadline", "primary_cta"):
        if not fields.get(name):
            raise ValueError(f"The landing hero has no {name.replace('_', ' ')}")
    fields.setdefault("social_proof_line", "")
    return LandingHero(**fields)


def landing_html(draft: Draft, brand: Brain, root: Path) -> tuple[str, list[str]]:
    """The hero as a standalone HTML page, and any accessibility problems found."""
    kit = load_kit(brand, root)
    hero = parse_hero(draft.text)
    problems = []
    if contrast_ratio(kit.text, kit.background) < 4.5:
        problems.append("Body text contrast is under 4.5:1 against the background")
    if contrast_ratio(kit.cta_text, kit.primary) < 4.5:
        problems.append("Button text contrast is under 4.5:1")
    if kit.product_image and (
        issue := alt_text_problem(kit.product_image_alt, kit.product_image_name)
    ):
        problems.append(f"{issue}: add a description to the photo in the brand kit")
    html = templates.get_template("landing.html.j2").render(
        kit=kit,
        hero=hero,
        angle=draft.angle or "default",
        alt_text=kit.product_image_alt,
        brand_name=brand.business.name or brand.workspace,
    )
    return html, problems


def export_variants(engine: Engine, draft_id: int, brand: Brain, root: Path) -> list[Path]:
    """Write every approved angle of this draft's landing hero to its own HTML file.

    Exporting is a step towards going live, so only approved variants are written, and if none
    is approved nothing is.
    """
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
        if draft is None or draft.content_type != "landing_hero":
            raise LookupError(f"Landing hero draft {draft_id} not found")
        base = draft.piece_id.rsplit("-", 1)[0] if draft.angle else draft.piece_id
        siblings = session.exec(
            select(Draft).where(
                Draft.cycle_id == draft.cycle_id,
                Draft.content_type == "landing_hero",
                Draft.piece_id.startswith(base),
            )
        ).all()
    approved = [variant for variant in siblings if variant.status == "approved"]
    if not approved:
        require_approved(draft)  # raises: a person has to approve a variant first
    folder = root / brand.workspace / "creative" / "landing" / base
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for variant in approved:
        require_approved(variant)
        html, _ = landing_html(variant, brand, root)
        path = folder / f"{variant.angle or 'default'}.html"
        path.write_text(html)
        written.append(path)
    return written
