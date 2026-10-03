"""Versioned brain storage: JSON files under workspaces/<brand>/brain/ plus a database row."""

from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.brain.models import FIELD_PATHS, Brain, FieldMeta
from growthcrew.db.models import BrainVersion
from growthcrew.db.session import get_engine

WORKSPACES_DIR = Path("workspaces")


def brain_dir(workspace: str, root: Path = WORKSPACES_DIR) -> Path:
    return root / workspace / "brain"


def list_versions(workspace: str, root: Path = WORKSPACES_DIR) -> list[int]:
    return sorted(int(path.stem[1:]) for path in brain_dir(workspace, root).glob("v[0-9]*.json"))


def load_brain(workspace: str, version: int | None = None, root: Path = WORKSPACES_DIR) -> Brain:
    versions = list_versions(workspace, root)
    if not versions:
        raise FileNotFoundError(f"No brain found for workspace '{workspace}'")
    path = brain_dir(workspace, root) / f"v{version or versions[-1]:04d}.json"
    return Brain.model_validate_json(path.read_text())


def changed_fields(old: Brain | None, new: Brain) -> list[str]:
    if old is None:
        return list(FIELD_PATHS)
    return [
        path
        for path in FIELD_PATHS
        if old.get(path) != new.get(path) or old.fields.get(path) != new.fields.get(path)
    ]


def save_brain(
    brain: Brain, note: str, root: Path = WORKSPACES_DIR, engine: Engine | None = None
) -> Brain:
    """Write `brain` as the next version. Earlier versions are never modified."""
    versions = list_versions(brain.workspace, root)
    previous = load_brain(brain.workspace, root=root) if versions else None
    brain = brain.model_copy(update={"version": (versions[-1] if versions else 0) + 1})
    data = brain.model_dump_json(indent=2)

    directory = brain_dir(brain.workspace, root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"v{brain.version:04d}.json").write_text(data)

    with Session(engine or get_engine()) as session:
        session.add(
            BrainVersion(
                workspace=brain.workspace,
                version=brain.version,
                note=note,
                changed_fields=",".join(changed_fields(previous, brain)),
                data=data,
            )
        )
        session.commit()
    return brain


def confirm_fields(
    workspace: str, paths: list[str], root: Path = WORKSPACES_DIR, engine: Engine | None = None
) -> Brain:
    """Record that a human has checked these fields, as a new version."""
    unknown = [path for path in paths if path not in FIELD_PATHS]
    if unknown:
        raise ValueError(f"Unknown fields: {unknown}")
    brain = load_brain(workspace, root=root)
    fields = dict(brain.fields)
    for path in paths:
        fields[path] = fields.get(path, FieldMeta()).model_copy(update={"status": "confirmed"})
    return save_brain(
        brain.model_copy(update={"fields": fields}),
        note=f"confirmed: {', '.join(paths)}",
        root=root,
        engine=engine,
    )


def update_field(
    workspace: str,
    path: str,
    value: object,
    root: Path = WORKSPACES_DIR,
    engine: Engine | None = None,
) -> Brain:
    """A human sets a field's value, which also confirms it. Saved as a new version."""
    if path not in FIELD_PATHS:
        raise ValueError(f"Unknown field: {path}")
    brain = load_brain(workspace, root=root)
    data = brain.model_dump(mode="json")
    node = data
    *parents, leaf = path.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value
    data["fields"][path] = {
        "status": "confirmed", "confidence": "high", "source_urls": [], "note": "set by a human",
    }  # fmt: skip
    return save_brain(Brain.model_validate(data), note=f"edited: {path}", root=root, engine=engine)
