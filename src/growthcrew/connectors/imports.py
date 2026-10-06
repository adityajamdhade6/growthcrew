"""Exports dropped into a folder are picked up by the daily sync: the fallback for sources
without API access (LinkedIn page analytics needs LinkedIn's partner approval).

Put a file named after its source (`linkedin-2026-10.csv`, `ads_meta.csv`) in
`workspaces/<brand>/imports/`. Each file is ingested once, then moved to `imports/done/`.
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from growthcrew.analytics.ingest import SOURCES, IngestResult, ingest


def source_of(path: Path) -> str | None:
    name = path.stem.lower()
    return next((source for source in SOURCES if name.startswith(source)), None)


def pick_up(engine: Engine, workspace: str, root: Path) -> list[tuple[str, IngestResult]]:
    folder = root / workspace / "imports"
    done = folder / "done"
    results = []
    for path in sorted(folder.glob("*.csv")):
        source = source_of(path)
        if source is None:
            continue  # left in place; the Settings page lists the names that are recognised
        results.append((path.name, ingest(engine, workspace, source, path.read_text())))
        done.mkdir(parents=True, exist_ok=True)
        path.rename(done / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{path.name}")
    return results
