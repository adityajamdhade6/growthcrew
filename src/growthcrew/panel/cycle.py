"""The panel's place in the weekly cycle: pre-test every new experiment before launch."""

from collections import defaultdict
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.agents.research import load_latest_research
from growthcrew.brain.store import load_brain
from growthcrew.creative.agent import shrink
from growthcrew.db.models import Creative, Draft, PanelRun
from growthcrew.llm import LLM
from growthcrew.panel import calibration, personas, pretest


def _base(draft: Draft) -> str:
    return draft.piece_id.removesuffix(f"-{draft.angle}") if draft.angle else draft.piece_id


def ensure_personas(llm: LLM, engine: Engine, workspace: str, root: Path):
    """The current panel, written afresh when there is none or the research is newer."""
    research = load_latest_research(workspace, root)
    current = personas.panel(engine, workspace)
    made = min((p.created_at for p in current), default=None)
    stale = made is not None and made.replace(tzinfo=None) < research.created_at.replace(
        tzinfo=None
    )
    if not current or stale:
        brand = load_brain(workspace, root=root)
        current = personas.generate(llm, engine, brand, research)
    return current


def _image(engine: Engine, draft_id: int, root: Path) -> bytes | None:
    with Session(engine) as session:
        row = session.exec(
            select(Creative)
            .where(Creative.draft_id == draft_id, Creative.size == "square")
            .order_by(Creative.round.desc())
        ).first()
    path = root / row.path if row else None
    return shrink(path.read_bytes()) if path and path.exists() else None


def pretest_cycle(llm: LLM, engine: Engine, cycle_id: int, root: Path) -> list[str]:
    """Pre-test every experiment drafted in this cycle that has not been pre-tested yet."""
    with Session(engine) as session:
        drafts = session.exec(select(Draft).where(Draft.cycle_id == cycle_id)).all()
        done = {
            run.experiment
            for run in session.exec(select(PanelRun).where(PanelRun.cycle_id == cycle_id))
        }
    tests: dict[str, list[Draft]] = defaultdict(list)
    for draft in drafts:
        if draft.angle and draft.status != "blocked":
            tests[_base(draft)].append(draft)
    tests = {name: group for name, group in tests.items() if len(group) >= 2 and name not in done}
    if not tests:
        return []
    workspace = drafts[0].workspace
    people = ensure_personas(llm, engine, workspace, root)
    trust = calibration.accuracy(engine, workspace)["trust"]
    notes = []
    for name, group in tests.items():
        variants = [
            pretest.Variant(label=d.angle, text=d.text, image=_image(engine, d.id, root))
            for d in group
        ]
        run = pretest.run(llm, engine, workspace, cycle_id, name, variants, people, trust)
        prediction = pretest.Prediction.model_validate_json(run.prediction)
        advice = pretest.Recommendation.model_validate_json(run.recommendation)
        note = f"panel predicts {' > '.join(prediction.ranking)} for {name}"
        if advice.drop:
            note += f" (suggests dropping {', '.join(advice.drop)})"
        notes.append(note)
    return notes
