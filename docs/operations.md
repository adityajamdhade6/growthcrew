# Running GrowthCrew in production

## The pieces

| Process | Command | Does |
|---|---|---|
| API | `uvicorn growthcrew.api.main:app` | Serves the web app; enqueues weekly cycles when `GROWTHCREW_QUEUE=1` |
| Worker (2+) | `growthcrew worker` | Runs queued jobs: weekly cycles, monitors, syncs |
| Scheduler | `growthcrew scheduler --loop --analyse` | Daily metric sync, weekly analysis |
| Postgres 16 + pgvector | | Everything, including content-memory vectors |

`docker compose up --build` starts all of it locally. `render.yaml` is the same on Render.

## Database

- `GROWTHCREW_MIGRATIONS=alembic` applies Alembic migrations on start (`growthcrew db upgrade`
  does it by hand). Without it (development, SQLite) tables are created directly.
- Every model change needs a migration: `uv run alembic revision --autogenerate -m "..."`.
  A test fails if the migrations and the models disagree.
- On Postgres, content memory is searched by pgvector with an HNSW index (cosine).

## Jobs and crashes

A job is claimed with a 2-minute lease that the worker renews while it works. If the worker
dies, the lease runs out and another worker takes the job. A weekly cycle resumes from the
stage it stopped at, and inside a cycle every model response is cached by the hash of its
request, so the rerun replays what was already paid for. Failures retry with exponential
backoff (30 s doubling, at most an hour) and, after 5 attempts, the job is marked dead and an
alert is raised.

## Isolation, roles and audit

- Every request runs in its workspace's tenant scope: ORM queries are filtered to that
  workspace and writes to another workspace raise, whatever the route's own query says.
- Roles per workspace: viewer (read), approver (approve, edit, reject, publish, decide
  signals), owner (also connectors, budget, brand brain, members, MCP tokens, kill switch).
- The audit log records every decision, publish, connector change, role change, token and
  pause, chained by hash; the database refuses updates and deletes on it.
  `growthcrew db verify-audit` checks the chain.

## Spending and the kill switch

Weekly budget per workspace and an optional total cap (`budget.py`); per-agent cost and
latency limits per call with alerts (`budgets.py`). `growthcrew pause on --by <name>` (or
Settings) stops every model call at once, everywhere or for one workspace; cycles halt and
resume later.

## Secrets

Only in the environment or a secret manager: `ANTHROPIC_API_KEY`, `GROWTHCREW_SECRET`,
`GROWTHCREW_ENCRYPTION_KEY`, connector keys (stored encrypted in the database). A log filter
redacts API keys, bearer tokens and passwords from every log line and traceback, and from
Sentry events (`SENTRY_DSN`, `uv sync --extra ops`).

## Health

`GET /health` checks the database and reports queued and dead jobs; compose and Render use it.

## Load test

`uv run python -m evals.loadtest --workspaces 20 --workers 6 --database-url <postgres>` runs
one weekly cycle for each of 20 workspaces at once through the real queue, database and model
wrapper, with simulated agents (a fake model with 0.1 to 0.3 s latency per call).

Measured on 2026-10-06, Postgres 16, 6 workers, on a development container (simulated
agents):

| Measure | Value |
|---|---|
| Wall time | 13.8 s |
| Cycles completed | 20 of 20 |
| Failed or dead jobs | 0 |
| Model calls logged | 320 |
| Drafts written under the wrong workspace | 0 |

This measures the backbone, not the agents: real model latency and cost per cycle are not
measured yet. CI runs the same test on every push.
