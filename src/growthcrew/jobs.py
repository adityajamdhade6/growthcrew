"""A durable job queue in the database (SQLite in development, Postgres in production).

A worker claims a job with a lease and keeps it alive with heartbeats. If the worker dies,
the lease runs out and another worker claims the job. Failures retry with exponential backoff
until `max_attempts`, then the job is dead and an alert is raised. Handlers must be safe to
run again: the weekly cycle resumes from the stage it stopped at, and model responses already
paid for come from the cache (`llm.cache_scope`), so a crash costs time, not money.
"""

import json
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, and_, or_, update
from sqlmodel import Session, select

from growthcrew.db.models import Alert, Job

logger = logging.getLogger(__name__)

LEASE_SECONDS = 120
BACKOFF_BASE = 30
BACKOFF_MAX = 3600

Handler = Callable[[Engine, Job], str | None]


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    engine: Engine,
    kind: str,
    key: str,
    workspace: str = "",
    payload: dict | None = None,
    max_attempts: int = 5,
) -> Job:
    """Add a job, or return the existing one with the same idempotency key."""
    with Session(engine, expire_on_commit=False) as session:
        existing = session.exec(select(Job).where(Job.idempotency_key == key)).first()
        if existing:
            return existing
        job = Job(kind=kind, workspace=workspace, payload=json.dumps(payload or {}),
                  idempotency_key=key, max_attempts=max_attempts)  # fmt: skip
        session.add(job)
        session.commit()
        return job


def claim(engine: Engine, worker: str, kinds: list[str] | None = None) -> Job | None:
    """Take the oldest due job, or one whose worker's lease has run out. Atomic per job."""
    now = _now()
    due = or_(
        and_(Job.status == "queued", Job.run_after <= now),
        and_(Job.status == "running", Job.lease_until < now),
    )
    with Session(engine, expire_on_commit=False) as session:
        query = select(Job.id).where(due).order_by(Job.run_after, Job.id).limit(5)
        if kinds:
            query = query.where(Job.kind.in_(kinds))
        if engine.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        for job_id in session.exec(query).all():
            # The conditional update is the lock: only one worker's update matches the row.
            claimed = session.exec(
                update(Job)
                .where(Job.id == job_id, due)
                .values(status="running", worker=worker, attempts=Job.attempts + 1,
                        lease_until=now + timedelta(seconds=LEASE_SECONDS), updated_at=now)
            )  # fmt: skip
            if claimed.rowcount == 1:
                session.commit()
                return session.get(Job, job_id)
        session.commit()
    return None


def heartbeat(engine: Engine, job_id: int, worker: str) -> bool:
    """Extend the lease. False if another worker has taken the job over."""
    with Session(engine) as session:
        result = session.exec(
            update(Job)
            .where(Job.id == job_id, Job.worker == worker, Job.status == "running")
            .values(lease_until=_now() + timedelta(seconds=LEASE_SECONDS))
        )
        session.commit()
        return result.rowcount == 1


def finish(engine: Engine, job_id: int, worker: str, result: str = "") -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job.worker != worker:
            return  # taken over after this worker's lease ran out; the new owner finishes it
        job.status, job.result, job.lease_until, job.updated_at = "done", result, None, _now()
        session.add(job)
        session.commit()


def fail(engine: Engine, job_id: int, worker: str, error: str) -> Job:
    """Record a failure: retry later with backoff, or give up and alert."""
    with Session(engine, expire_on_commit=False) as session:
        job = session.get(Job, job_id)
        if job.worker != worker:
            return job
        job.last_error, job.lease_until, job.updated_at = error[:1000], None, _now()
        if job.attempts >= job.max_attempts:
            job.status = "dead"
            session.add(Alert(workspace=job.workspace or "*", kind="job_dead",
                              message=f"{job.kind} job {job.id} gave up after "
                              f"{job.attempts} attempts: {error[:300]}"))  # fmt: skip
        else:
            job.status = "queued"
            delay = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (job.attempts - 1))
            job.run_after = _now() + timedelta(seconds=delay)
        session.add(job)
        session.commit()
        return job


def run_one(engine: Engine, handlers: dict[str, Handler], worker: str) -> Job | None:
    """Claim and run one job. Returns it, or None when nothing is due."""
    job = claim(engine, worker, list(handlers))
    if job is None:
        return None
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(LEASE_SECONDS / 3):
            if not heartbeat(engine, job.id, worker):
                return

    pulse = threading.Thread(target=beat, daemon=True)
    pulse.start()
    try:
        result = handlers[job.kind](engine, job) or ""
        finish(engine, job.id, worker, result)
    except Exception as exc:  # noqa: BLE001 — recorded on the job and retried
        logger.exception("Job %s (%s) failed", job.id, job.kind)
        fail(engine, job.id, worker, f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()
    with Session(engine, expire_on_commit=False) as session:
        return session.get(Job, job.id)


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def run_worker(
    engine: Engine,
    handlers: dict[str, Handler],
    idle_seconds: float = 5.0,
    stop: threading.Event | None = None,
    max_jobs: int | None = None,
) -> int:
    """Process jobs until stopped (or `max_jobs` is reached). Returns how many ran."""
    name, done = worker_name(), 0
    while not (stop and stop.is_set()) and (max_jobs is None or done < max_jobs):
        if run_one(engine, handlers, name) is None:
            if max_jobs is not None:
                break
            time.sleep(idle_seconds)
            continue
        done += 1
    return done


def default_handlers(llm=None, root=None) -> dict[str, Handler]:
    """What the production worker runs. Each handler is safe to run twice."""
    from pathlib import Path

    from growthcrew.brain.store import WORKSPACES_DIR
    from growthcrew.connectors.sync import sync_workspace

    root = root or WORKSPACES_DIR

    def weekly_cycle(engine: Engine, job: Job) -> str:
        from growthcrew.agents.orchestrator import Orchestrator

        cycle_id = json.loads(job.payload)["cycle_id"]
        cycle = Orchestrator(llm, engine=engine, root=Path(root)).run(cycle_id)
        if cycle.halted_reason:
            raise RuntimeError(cycle.halted_reason)
        return f"cycle {cycle_id} at {cycle.stage}"

    def monitor(engine: Engine, job: Job) -> str:
        from growthcrew.monitor.run import run_monitors

        result = run_monitors(llm, engine, job.workspace, Path(root))
        return f"{result.stored} new signals"

    def sync(engine: Engine, job: Job) -> str:
        runs = sync_workspace(engine, job.workspace, Path(root))
        return f"{len(runs)} sources synced"

    handlers: dict[str, Handler] = {"sync": sync}
    if llm is not None:
        handlers |= {"weekly_cycle": weekly_cycle, "monitor": monitor}
    return handlers
