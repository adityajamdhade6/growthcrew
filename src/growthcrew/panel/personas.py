"""Personas from the ideal customer and voice-of-customer research, each citing its evidence."""

import json

from pydantic import BaseModel
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew import config
from growthcrew.agents.research_models import ResearchReport
from growthcrew.agents.strategist import build_evidence, render_evidence
from growthcrew.brain.context import BRAIN_NOTE
from growthcrew.brain.models import Brain
from growthcrew.brain.voice import squash
from growthcrew.config import AgentRole
from growthcrew.db.models import Persona
from growthcrew.llm import LLM
from growthcrew.tools.untrusted import DATA_RULE, wrap

SYSTEM = f"""You build a panel of synthetic respondents for a small business's marketing \
team. Each persona is one plausible member of the business's audience, drawn from the evidence \
list. Spread the panel across the segments, pains and objections the evidence shows, \
including sceptics and people who would not buy; a panel of fans predicts nothing.

For each persona give: a first name, a one-sentence summary, the segment, demographics or \
firmographics, pains, objections, media habits, and phrases they would use. Phrases must be \
copied exactly from the customer quotes in the evidence; leave the list empty rather than \
inventing one. `support` lists the evidence IDs the persona rests on, exactly as written.

{BRAIN_NOTE}

{DATA_RULE}"""


class PersonaDraft(BaseModel):
    name: str
    summary: str
    segment: str
    demographics: list[str]
    pains: list[str]
    objections: list[str]
    media_habits: list[str]
    phrases: list[str]
    support: list[str]


class PersonaBatch(BaseModel):
    personas: list[PersonaDraft]


def latest_generation(engine: Engine, workspace: str) -> int:
    with Session(engine) as session:
        value = session.exec(
            select(func.max(Persona.generation)).where(Persona.workspace == workspace)
        ).one()
    return value or 0


def panel(engine: Engine, workspace: str, generation: int | None = None) -> list[Persona]:
    generation = generation or latest_generation(engine, workspace)
    with Session(engine) as session:
        return list(
            session.exec(
                select(Persona).where(
                    Persona.workspace == workspace, Persona.generation == generation
                )
            )
        )


def check(draft: PersonaDraft, known: set[str], quotes: list[str]) -> PersonaDraft | None:
    """Keep only known evidence IDs and verbatim phrases. A persona with no support is dropped."""
    support = [item for item in draft.support if item in known]
    if not support:
        return None
    haystack = [squash(quote) for quote in quotes]
    phrases = [p for p in draft.phrases if squash(p) and any(squash(p) in q for q in haystack)]
    return draft.model_copy(update={"support": support, "phrases": phrases})


def generate(
    llm: LLM,
    engine: Engine,
    brand: Brain,
    research: ResearchReport,
    size: int | None = None,
) -> list[Persona]:
    """Write a new generation of personas. Earlier generations are kept for the record."""
    size = size or config.PANEL_SIZE
    evidence = build_evidence(brand, research)
    known = {item.id for item in evidence}
    quotes = [q.text for theme in research.voice_of_customer.themes for q in theme.quotes]
    listing = wrap(render_evidence(evidence), "brand brain and research")
    kept: list[PersonaDraft] = []
    names: set[str] = set()
    while len(kept) < size:
        want = min(config.PANEL_BATCH, size - len(kept))
        so_far = ", ".join(f"{p.name} ({p.segment})" for p in kept) or "none yet"
        batch = llm.call(
            AgentRole.PANEL,
            system=SYSTEM,
            user=f"Evidence:\n{listing}\n\nPersonas so far: {so_far}\n\nWrite {want} more, "
            "different from those so far.",
            output_model=PersonaBatch,
            workspace=brand.workspace,
            tag="panel|personas",
        )
        added = 0
        for draft in batch.personas[:want]:
            checked = check(draft, known, quotes)
            if checked and checked.name.lower() not in names:
                kept.append(checked)
                names.add(checked.name.lower())
                added += 1
        if added == 0:
            break  # the model has nothing new to add; a smaller panel is better than padding
    generation = latest_generation(engine, brand.workspace) + 1
    rows = [
        Persona(
            workspace=brand.workspace,
            generation=generation,
            name=draft.name,
            profile=draft.model_dump_json(exclude={"name", "support"}),
            support=json.dumps(draft.support),
        )
        for draft in kept
    ]
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(rows)
        session.commit()
    return rows


def describe(persona: Persona) -> str:
    profile = json.loads(persona.profile)
    lines = [
        f"You are {persona.name}. {profile['summary']}",
        f"Segment: {profile['segment']}",
        f"About you: {'; '.join(profile['demographics'])}",
        f"Your problems: {'; '.join(profile['pains'])}",
        f"What makes you hesitate: {'; '.join(profile['objections'])}",
        f"Where you spend time: {'; '.join(profile['media_habits'])}",
    ]
    if profile["phrases"]:
        lines.append(
            "Things people like you have said:\n"
            + wrap("\n".join(profile["phrases"]), "customer reviews")
        )
    return "\n".join(lines)
