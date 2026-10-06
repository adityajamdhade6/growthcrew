"""Pre-test: every persona reacts to every variant; the reactions are aggregated in code."""

import json
import random
import string

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.budget import BudgetExceeded
from growthcrew.config import AgentRole
from growthcrew.db.models import PanelRun, Persona
from growthcrew.llm import LLM
from growthcrew.panel.personas import describe

BOOTSTRAP = 2000
# How sure the panel must be that a variant is the weakest before it suggests dropping it.
DROP_PROBABILITY = {"useful": 0.9, "untested": 0.95}
# Dropping must leave a real test behind.
MIN_LEFT = 2
# A pre-test cut short (by the budget) still counts if at least this many personas answered.
MIN_RESPONDENTS = 8

SYSTEM = """You are taking part in a consumer research panel. You will be shown a few \
marketing messages, labelled with letters, as they would appear in your feed or inbox. React \
as the person described, honestly and briefly: most messages most people ignore. For each \
message give:
- stop: how likely you are to stop scrolling for it, from 1 (scroll straight past) to 7 \
(definitely stop);
- click: whether you would click or reply;
- objection: the main reason you would not act, in your own words;
- confusing: anything unclear, or an empty string.
Judge each message on its own. The order they are shown in means nothing."""


class Reaction(BaseModel):
    option: str
    stop: int = Field(ge=1, le=7)
    click: bool
    objection: str
    confusing: str


class Reactions(BaseModel):
    reactions: list[Reaction]


class Variant(BaseModel):
    label: str
    text: str
    image: bytes | None = None


class VariantStats(BaseModel):
    label: str
    respondents: int
    mean_stop: float
    stop_sd: float
    click_share: float
    prob_first: float
    prob_last: float
    top_objections: list[str]
    confusing: list[str]


class Prediction(BaseModel):
    ranking: list[str]
    variants: list[VariantStats]
    respondents: int
    # Share of personas whose own favourite was not the panel's top pick, 0 to 1.
    disagreement: float


class Recommendation(BaseModel):
    drop: list[str]
    reason: str


def ask(
    llm: LLM, persona: Persona, variants: list[Variant], rng: random.Random, workspace: str
) -> list[dict] | None:
    """One persona's reactions, mapped back to variant labels. None if the answer was unusable."""
    order = variants[:]
    rng.shuffle(order)
    letters = dict(zip(string.ascii_uppercase, order, strict=False))
    shown = "\n\n".join(f"Message {letter}:\n{variant.text}" for letter, variant in letters.items())
    images = [v.image for v in letters.values() if v.image]
    if images and len(images) == len(letters):
        shown += "\n\nThe images are shown in the same order: " + ", ".join(letters) + "."
    else:
        images = []
    answer = llm.call(
        AgentRole.PANEL,
        system=SYSTEM,
        user=f"{describe(persona)}\n\n{shown}",
        output_model=Reactions,
        images=images,
        workspace=workspace,
        tag="panel|pretest",
    )
    by_letter = {reaction.option.strip().upper()[:1]: reaction for reaction in answer.reactions}
    if set(by_letter) != set(letters):
        return None  # skipped or invented an option: not counted
    return [
        {"persona": persona.name, "label": letters[letter].label, "position": index,
         **by_letter[letter].model_dump(exclude={"option"})}
        for index, letter in enumerate(letters)
    ]  # fmt: skip


def _score(stop: np.ndarray, click: np.ndarray) -> np.ndarray:
    # Click share first; the stop score (scaled under 1) breaks ties.
    return click.mean(axis=0) + stop.mean(axis=0) / 100


def aggregate(responses: list[list[dict]], labels: list[str], seed: int = 0) -> Prediction:
    """Predicted ranking, with bootstrap probabilities over personas."""
    usable = [person for person in responses if person]
    if not usable:
        raise ValueError("No persona gave a usable answer")
    index = {label: i for i, label in enumerate(labels)}
    stop = np.zeros((len(usable), len(labels)))
    click = np.zeros((len(usable), len(labels)))
    for row, person in enumerate(usable):
        for item in person:
            stop[row, index[item["label"]]] = item["stop"]
            click[row, index[item["label"]]] = float(item["click"])
    score = _score(stop, click)
    ranking = [labels[i] for i in np.argsort(-score, kind="stable")]

    rng = np.random.default_rng(seed)
    firsts = np.zeros(len(labels))
    lasts = np.zeros(len(labels))
    for _ in range(BOOTSTRAP):
        pick = rng.integers(0, len(usable), len(usable))
        sample = _score(stop[pick], click[pick])
        firsts[np.argmax(sample)] += 1
        lasts[np.argmin(sample)] += 1

    favourite = np.argmax(click + stop / 100, axis=1)
    top = index[ranking[0]]
    stats = []
    for label in labels:
        i = index[label]
        mine = [item for person in usable for item in person if item["label"] == label]
        objections = [item["objection"] for item in mine if item["objection"]]
        stats.append(
            VariantStats(
                label=label,
                respondents=len(usable),
                mean_stop=round(float(stop[:, i].mean()), 2),
                stop_sd=round(float(stop[:, i].std()), 2),
                click_share=round(float(click[:, i].mean()), 3),
                prob_first=round(float(firsts[i] / BOOTSTRAP), 3),
                prob_last=round(float(lasts[i] / BOOTSTRAP), 3),
                top_objections=objections[:3],
                confusing=[item["confusing"] for item in mine if item["confusing"]][:3],
            )
        )
    return Prediction(
        ranking=ranking,
        variants=stats,
        respondents=len(usable),
        disagreement=round(float((favourite != top).mean()), 3),
    )


def recommend(prediction: Prediction, trust: str) -> Recommendation:
    """At most one variant to drop before launch, and only when the panel has earned it.

    The panel never picks a winner and never shrinks a test below two variants.
    """
    if trust == "low":
        return Recommendation(drop=[], reason="The panel's past predictions did not match real "
                              "results, so it makes no recommendation.")  # fmt: skip
    if len(prediction.variants) - 1 < MIN_LEFT:
        return Recommendation(drop=[], reason="Every variant goes to the real test: dropping one "
                              "would leave nothing to compare.")  # fmt: skip
    needed = DROP_PROBABILITY[trust]
    weakest = max(prediction.variants, key=lambda v: v.prob_last)
    if weakest.prob_last >= needed:
        return Recommendation(
            drop=[weakest.label],
            reason=f"'{weakest.label}' came last in {weakest.prob_last:.0%} of resamples of the "
            f"panel (the bar is {needed:.0%}). Dropping it is a suggestion for a person to make; "
            "the real test decides the winner.",
        )
    return Recommendation(drop=[], reason="No variant is clearly weaker than the others; test "
                          "them all.")  # fmt: skip


def run(
    llm: LLM,
    engine: Engine,
    workspace: str,
    cycle_id: int,
    experiment: str,
    variants: list[Variant],
    personas: list[Persona],
    trust: str,
    seed: int = 0,
) -> PanelRun:
    if len(variants) < 2:
        raise ValueError("A pre-test needs at least two variants")
    if not personas:
        raise ValueError("The panel has no personas yet")
    rng = random.Random(seed)
    responses = []
    for persona in personas:
        try:
            responses.append(ask(llm, persona, variants, rng, workspace))
        except BudgetExceeded:
            # Keep what was paid for when enough personas answered; otherwise give up.
            if sum(1 for r in responses if r) < MIN_RESPONDENTS:
                raise
            break
    labels = [variant.label for variant in variants]
    prediction = aggregate(responses, labels, seed)
    advice = recommend(prediction, trust)
    row = PanelRun(
        workspace=workspace,
        cycle_id=cycle_id,
        experiment=experiment,
        generation=personas[0].generation,
        responses=json.dumps([person for person in responses if person]),
        prediction=prediction.model_dump_json(),
        recommendation=advice.model_dump_json(),
        trust=trust,
    )
    with Session(engine, expire_on_commit=False) as session:
        session.add(row)
        session.commit()
    return row
