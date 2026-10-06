"""Pull fresh metrics for a workspace from every connected source."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.analytics.ingest import store_rows
from growthcrew.connectors import brevo, google, hubspot, imports, mcp_source, store
from growthcrew.db.models import SyncRun

# Days re-read on each sync: Search Console and GA4 revise their last few days.
WINDOW_DAYS = 7


def _record(engine: Engine, workspace: str, provider: str, job: Callable[[], tuple[int, int]]):
    run = SyncRun(workspace=workspace, provider=provider)
    try:
        run.rows, run.matched = job()
        run.status = "ok"
    except Exception as exc:  # noqa: BLE001 — one source failing does not stop the others
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
    run.finished_at = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        session.add(run)
        session.commit()
    return run


def sync_workspace(
    engine: Engine,
    workspace: str,
    root: Path,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> list[SyncRun]:
    client = client or httpx.Client(timeout=60.0)
    end = today or datetime.now(UTC).date()
    sources = set(store.connected(engine, workspace))
    runs = []

    def stored(source: str, rows: list[dict]) -> tuple[int, int]:
        result = store_rows(engine, workspace, source, rows)
        return result.rows, result.matched

    if "google" in sources:
        _, settings = store.load(engine, workspace, "google")
        if settings.get("site_url"):

            def gsc() -> tuple[int, int]:
                rows = google.search_console(engine, workspace, client, end, WINDOW_DAYS)
                return stored("gsc", rows)

            runs.append(_record(engine, workspace, "google:search_console", gsc))
        if settings.get("ga4_property"):

            def ga4() -> tuple[int, int]:
                return stored("ga4", google.ga4(engine, workspace, client, end, WINDOW_DAYS))

            runs.append(_record(engine, workspace, "google:ga4", ga4))
    if "brevo" in sources:
        runs.append(_record(engine, workspace, "brevo", lambda: stored(
            "email", brevo.stats(engine, workspace, client))))  # fmt: skip
    if "hubspot" in sources:

        def crm() -> tuple[int, int]:
            snap = hubspot.snapshot(engine, workspace, client)
            return snap.new_contacts + snap.open_deals, 0

        runs.append(_record(engine, workspace, "hubspot", crm))
    if "mcp" in sources:
        _, settings = store.load(engine, workspace, "mcp")
        source = settings.get("source", "ga4")
        runs.append(_record(engine, workspace, "mcp", lambda: stored(
            source, mcp_source.fetch_rows(settings))))  # fmt: skip

    def folder() -> tuple[int, int]:
        picked = imports.pick_up(engine, workspace, root)
        return sum(r.rows for _, r in picked), sum(r.matched for _, r in picked)

    if (root / workspace / "imports").exists():
        runs.append(_record(engine, workspace, "imports", folder))
    return runs
