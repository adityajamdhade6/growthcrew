"""Learn brand voice rules from the edits a human reviewer keeps making."""

import re
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.brain.store import WORKSPACES_DIR, load_brain, save_brain
from growthcrew.db.models import EditPattern

# An edit pattern becomes a voice rule once it has been seen this many times.
RECURRING = 3
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
_STOPWORDS = frozenset(
    "that this with from have your will they them their what when which were been more than "
    "into about just also only over such very some most other then there here would could "
    "should these those while where after before because through".split()
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", text.lower()) if w not in _STOPWORDS}


def detect(original: str, edited: str) -> dict[str, str]:
    """Pattern key -> voice rule, for each kind of change this edit made."""
    found: dict[str, str] = {}
    if original.count("!") > edited.count("!"):
        found["no_exclamation"] = "Do not use exclamation marks."
    if len(_EMOJI.findall(original)) > len(_EMOJI.findall(edited)):
        found["no_emoji"] = "Do not use emoji."
    if original.count("#") > edited.count("#"):
        found["fewer_hashtags"] = "Use fewer hashtags."
    if len(original.split()) >= 20 and len(edited.split()) <= 0.75 * len(original.split()):
        found["shorter"] = "Keep pieces shorter; the reviewer routinely cuts a quarter or more."
    for word in sorted(_words(original) - _words(edited)):
        found[f"word:{word}"] = word
    return found


def record_edit(
    engine: Engine, workspace: str, original: str, edited: str, root: Path = WORKSPACES_DIR
) -> list[str]:
    """Count this edit's patterns. A pattern seen often enough becomes a proposal for a person
    to accept or reject; nothing is written into the brand voice here.

    Returns the rules newly proposed by this edit.
    """
    proposed = []
    with Session(engine) as session:
        for key, rule in detect(original, edited).items():
            pattern = session.exec(
                select(EditPattern).where(
                    EditPattern.workspace == workspace, EditPattern.key == key
                )
            ).first() or EditPattern(workspace=workspace, key=key, rule=rule)
            pattern.count += 1
            if pattern.count >= RECURRING and pattern.status == "counting":
                pattern.status = "proposed"
                proposed.append(_label(key, rule))
            session.add(pattern)
        session.commit()
    return proposed


def _label(key: str, rule: str) -> str:
    return f"Ban the word '{rule}'" if key.startswith("word:") else rule


def proposals(engine: Engine, workspace: str) -> list[dict]:
    """Voice rules waiting for a person's decision, with how often the edit was made."""
    with Session(engine) as session:
        rows = session.exec(
            select(EditPattern).where(
                EditPattern.workspace == workspace, EditPattern.status == "proposed"
            )
        ).all()
    return [
        {"id": row.id, "rule": _label(row.key, row.rule), "times_seen": row.count} for row in rows
    ]


def resolve_proposal(
    engine: Engine, workspace: str, proposal_id: int, accept: bool, root: Path = WORKSPACES_DIR
) -> str:
    """Accept a proposal (write it into the brand voice as a new brain version) or reject it."""
    with Session(engine) as session:
        pattern = session.get(EditPattern, proposal_id)
        if pattern is None or pattern.workspace != workspace or pattern.status != "proposed":
            raise LookupError("No such proposal is waiting")
        pattern.status = "accepted" if accept else "rejected"
        pattern.applied = accept
        key, rule = pattern.key, pattern.rule
        session.add(pattern)
        session.commit()
    if not accept:
        return "rejected"
    brain = load_brain(workspace, root=root)
    guide = brain.voice.guide
    target = guide.banned_phrases if key.startswith("word:") else guide.rules
    if rule not in target:
        target.append(rule)
        save_brain(
            brain,
            note=f"learned from reviewer edits: {_label(key, rule)}",
            root=root,
            engine=engine,
        )
    return "accepted"
