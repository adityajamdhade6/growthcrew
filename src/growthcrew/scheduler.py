"""The scheduler: fresh metrics every day, the analyst every week.

`tick` does whatever is due and returns what it did; run it from cron (`growthcrew scheduler`)
or keep it running (`growthcrew scheduler --loop`). The weekly analysis writes learnings for
the strategist to rule on in the next cycle; it changes nothing on its own.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.connectors import store
from growthcrew.connectors.sync import sync_workspace
from growthcrew.db.models import Learnings, SyncRun

logger = logging.getLogger(__name__)

SYNC_EVERY = timedelta(hours=20)
# The analyst runs on this weekday (Monday = 0), once.
ANALYSIS_WEEKDAY = 0


def _workspaces(root: Path) -> list[str]:
    return sorted(p.name for p in root.glob("*") if (p / "brain").is_dir())


def _last_sync(engine: Engine, workspace: str) -> datetime | None:
    with Session(engine) as session:
        run = session.exec(
            select(SyncRun)
            .where(SyncRun.workspace == workspace)
            .order_by(SyncRun.started_at.desc())
        ).first()
    return run.started_at.replace(tzinfo=UTC) if run else None


def _analysed_this_week(engine: Engine, workspace: str, now: datetime) -> bool:
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0,
                                                              microsecond=0)  # fmt: skip
    with Session(engine) as session:
        latest = session.exec(
            select(Learnings).where(Learnings.workspace == workspace).order_by(Learnings.id.desc())
        ).first()
    return bool(latest and latest.created_at.replace(tzinfo=UTC) >= monday)


def tick(
    engine: Engine, root: Path, now: datetime | None = None, analyst=None, client=None
) -> list[str]:
    now = now or datetime.now(UTC)
    done = []
    for workspace in _workspaces(root):
        has_sources = store.connected(engine, workspace) or (root / workspace / "imports").exists()
        last = _last_sync(engine, workspace)
        if has_sources and (last is None or now - last >= SYNC_EVERY):
            runs = sync_workspace(engine, workspace, root, client, now.date())
            failed = [r.provider for r in runs if r.status == "failed"]
            done.append(f"{workspace}: synced {len(runs)} source(s)"
                        + (f", failed: {', '.join(failed)}" if failed else ""))  # fmt: skip
        if (analyst is not None and now.weekday() == ANALYSIS_WEEKDAY
                and not _analysed_this_week(engine, workspace, now)):  # fmt: skip
            from growthcrew.agents.strategist import load_latest_strategy

            try:
                analyst.run(workspace, load_latest_strategy(workspace, root), engine)
                done.append(f"{workspace}: weekly learnings written")
            except (ValueError, FileNotFoundError) as exc:
                done.append(f"{workspace}: no analysis ({exc})")
            except Exception:  # noqa: BLE001 — one workspace failing does not stop the rest
                logger.exception("Weekly analysis failed for %s", workspace)
                done.append(f"{workspace}: analysis failed")
    return done
