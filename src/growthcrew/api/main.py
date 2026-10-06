"""HTTP API. Every route needs a bearer token from /auth/login; see api/auth.py."""

import json
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew import budget, jobs, observability, safety, workflow
from growthcrew.agents.analyst import AnalystAgent
from growthcrew.agents.learning_models import WeeklyLearnings
from growthcrew.agents.orchestrator import Orchestrator, recover_interrupted, timeline
from growthcrew.agents.strategist import load_latest_strategy
from growthcrew.analytics.ingest import IngestResult, ingest
from growthcrew.api.auth import authorize, current_user
from growthcrew.api.auth import router as auth_router
from growthcrew.api.deps import (  # noqa: F401  (re-exported for dependency overrides)
    analyst_dep,
    engine_dep,
    guard,
    llm_dep,
    orchestrator_dep,
    templates,
)
from growthcrew.api.ui import router as ui_router
from growthcrew.db.models import (
    Alert,
    Approval,
    CalendarItem,
    Cycle,
    Draft,
    GuardrailBlock,
    Job,
    LLMCall,
)
from growthcrew.integrations.export import calendar_csv, email_draft
from growthcrew.reports.costs import cost_summary
from growthcrew.reports.learning_log import learning_log


# Every route requires a signed-in user with access to the workspace involved.
@asynccontextmanager
async def lifespan(_: FastAPI):
    safety.install_scrubber()
    observability.init_error_tracking()
    engine = engine_dep()
    recover_interrupted(engine)
    if os.getenv("GROWTHCREW_DEMO") == "1":
        # A public demo starts with the sample brand loaded. See evals/demo_seed.py.
        from evals.demo_seed import main as seed_demo

        seed_demo()
    yield


app = FastAPI(title="GrowthCrew", dependencies=[Depends(authorize)], lifespan=lifespan)
app.include_router(auth_router)
app.include_router(ui_router)
_templates = templates
_guard = guard


def _draft(engine: Engine, draft_id: int) -> Draft:
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(404, f"Draft {draft_id} not found")
    return draft


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected", "edited"]
    reviewer: str = ""
    comment: str = ""
    edited_text: str | None = None


class PublishIn(BaseModel):
    published_by: str
    via: str = "manual"
    confirm: bool = False
    # Where the piece went live, so analytics rows can be matched to it.
    url: str | None = None


class BudgetIn(BaseModel):
    weekly_limit_usd: float


@app.get("/health")
def health(engine: Engine = Depends(engine_dep)) -> dict:
    """Liveness and readiness: the database answers, and how many jobs are waiting."""
    try:
        with Session(engine) as session:
            queued = session.exec(select(func.count(Job.id)).where(Job.status == "queued")).one()
            dead = session.exec(select(func.count(Job.id)).where(Job.status == "dead")).one()
    except Exception as exc:  # noqa: BLE001 — reported as unhealthy, not raised
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
    return {"status": "ok", "database": engine.dialect.name, "jobs_queued": queued,
            "jobs_dead": dead}  # fmt: skip


@app.get("/costs")
def costs(engine: Engine = Depends(engine_dep)) -> list[dict]:
    """Calls, tokens and spend per agent."""
    query = select(
        LLMCall.agent,
        func.count(LLMCall.id),
        func.sum(LLMCall.input_tokens),
        func.sum(LLMCall.output_tokens),
        func.sum(LLMCall.cost_usd),
    ).group_by(LLMCall.agent)
    with Session(engine) as session:
        return [
            {
                "agent": agent,
                "calls": calls,
                "input_tokens": inp,
                "output_tokens": out,
                "cost_usd": round(cost or 0, 6),
            }
            for agent, calls, inp, out, cost in session.exec(query)
        ]


# --- cycles ---


@app.post("/workspaces/{workspace}/cycles", status_code=202)
def start_cycle(
    workspace: str,
    background: BackgroundTasks,
    orchestrator: Orchestrator = Depends(orchestrator_dep),
) -> Cycle:
    """Start this week's cycle. It runs in the background up to the approval stage.

    With GROWTHCREW_QUEUE=1 it goes to the durable job queue, where a worker runs it and a
    crash resumes it; otherwise it runs inside the API process (development).
    """
    cycle = orchestrator.start_cycle(workspace)
    if os.getenv("GROWTHCREW_QUEUE") == "1":
        jobs.enqueue(orchestrator.engine, "weekly_cycle", f"cycle:{cycle.id}", workspace,
                     {"cycle_id": cycle.id})  # fmt: skip
    else:
        background.add_task(orchestrator.run, cycle.id)
    return cycle


@app.post("/cycles/{cycle_id}/resume", status_code=202)
def resume_cycle(
    cycle_id: int,
    background: BackgroundTasks,
    orchestrator: Orchestrator = Depends(orchestrator_dep),
) -> dict:
    """Continue a halted cycle, for example after raising the budget."""
    if os.getenv("GROWTHCREW_QUEUE") == "1":
        jobs.enqueue(
            orchestrator.engine,
            "weekly_cycle",
            f"cycle:{cycle_id}:resume:{int(time.time())}",
            "",
            {"cycle_id": cycle_id},
        )
    else:
        background.add_task(orchestrator.run, cycle_id)
    return {"cycle_id": cycle_id, "status": "resuming"}


@app.get("/workspaces/{workspace}/cycles")
def list_cycles(workspace: str, engine: Engine = Depends(engine_dep)) -> list[Cycle]:
    with Session(engine) as session:
        query = select(Cycle).where(Cycle.workspace == workspace).order_by(Cycle.id.desc())
        return list(session.exec(query))


@app.get("/cycles/{cycle_id}/timeline")
def cycle_timeline(cycle_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    return _guard(lambda: timeline(engine, cycle_id))


# --- drafts and approval ---


@app.get("/workspaces/{workspace}/drafts")
def list_drafts(
    workspace: str, status: str | None = None, engine: Engine = Depends(engine_dep)
) -> list[Draft]:
    query = select(Draft).where(Draft.workspace == workspace)
    if status:
        query = query.where(Draft.status == status)
    with Session(engine) as session:
        return list(session.exec(query.order_by(Draft.id)))


@app.get("/drafts/{draft_id}")
def get_draft(draft_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    draft = _draft(engine, draft_id)
    with Session(engine) as session:
        approvals = session.exec(select(Approval).where(Approval.draft_id == draft_id)).all()
    return {"draft": draft, "approvals": approvals}


@app.post("/drafts/{draft_id}/decision")
def decide(
    draft_id: int, body: DecisionIn, request: Request, engine: Engine = Depends(engine_dep)
) -> dict:
    """The human approval step: approve, reject, or approve with edits."""
    # The reviewer is the signed-in user, not whatever name the request claims.
    user = current_user(request)
    reviewer = user.email if user else body.reviewer
    approval, learned = _guard(
        lambda: workflow.decide(
            engine, draft_id, body.decision, reviewer, body.comment, body.edited_text
        )
    )
    return {"approval": approval, "voice_rules_proposed": learned}


@app.get("/drafts/{draft_id}/text")
def draft_text(draft_id: int, engine: Engine = Depends(engine_dep)) -> Response:
    """Plain text of an approved draft, for copy to clipboard."""
    draft = _draft(engine, draft_id)
    _guard(lambda: workflow.require_approved(draft))
    return Response(draft.text, media_type="text/plain; charset=utf-8")


@app.get("/drafts/{draft_id}/email.eml")
def draft_email(draft_id: int, step: int = 1, engine: Engine = Depends(engine_dep)) -> Response:
    """An approved email piece as an unsent .eml draft."""
    draft = _draft(engine, draft_id)
    _guard(lambda: workflow.require_approved(draft))
    eml = _guard(lambda: email_draft(draft, step))
    return Response(
        eml,
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="draft-{draft_id}-{step}.eml"'},
    )


# --- calendar, publishing, measuring ---


def _calendar(engine: Engine, workspace: str) -> list[tuple[CalendarItem, Draft]]:
    query = (
        select(CalendarItem, Draft)
        .where(CalendarItem.workspace == workspace, CalendarItem.draft_id == Draft.id)
        .order_by(CalendarItem.scheduled_for)
    )
    with Session(engine) as session:
        return list(session.exec(query))


@app.get("/workspaces/{workspace}/calendar")
def calendar(workspace: str, engine: Engine = Depends(engine_dep)) -> list[dict]:
    return [
        {**item.model_dump(), "piece": draft.piece_id, "text": draft.text}
        for item, draft in _calendar(engine, workspace)
    ]


@app.get("/workspaces/{workspace}/calendar.csv")
def calendar_export(workspace: str, engine: Engine = Depends(engine_dep)) -> Response:
    """The content calendar as CSV. Only approved pieces are ever on the calendar."""
    return Response(
        calendar_csv(_calendar(engine, workspace)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{workspace}-calendar.csv"'},
    )


@app.post("/calendar/{item_id}/publish")
def publish(
    item_id: int, body: PublishIn, request: Request, engine: Engine = Depends(engine_dep)
) -> CalendarItem:
    """The explicit publish button. Needs confirm=true and an approved draft."""
    user = current_user(request)
    published_by = user.email if user else body.published_by
    return _guard(
        lambda: workflow.publish(engine, item_id, published_by, body.via, body.confirm, body.url)
    )


@app.post("/calendar/{item_id}/metrics")
def metrics(
    item_id: int, body: dict[str, float], engine: Engine = Depends(engine_dep)
) -> CalendarItem:
    return _guard(lambda: workflow.record_metrics(engine, item_id, body))


# --- budget ---


@app.get("/workspaces/{workspace}/budget")
def get_budget(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    with Session(engine) as session:
        alerts = session.exec(
            select(Alert).where(Alert.workspace == workspace).order_by(Alert.id.desc())
        ).all()
    return {
        "weekly_limit_usd": budget.weekly_limit(engine, workspace),
        "spent_this_week_usd": round(budget.weekly_spend(engine, workspace), 4),
        "alerts": [json.loads(alert.model_dump_json()) for alert in alerts],
    }


@app.put("/workspaces/{workspace}/budget")
def put_budget(workspace: str, body: BudgetIn, engine: Engine = Depends(engine_dep)) -> dict:
    if body.weekly_limit_usd < 0:
        raise HTTPException(400, "weekly_limit_usd cannot be negative")
    budget.set_weekly_limit(engine, workspace, body.weekly_limit_usd)
    return get_budget(workspace, engine)


# --- performance data, analysis, learning log ---


@app.post("/workspaces/{workspace}/metrics/{source}")
def upload_metrics(
    workspace: str,
    source: str,
    csv_text: str = Body(media_type="text/csv"),
    engine: Engine = Depends(engine_dep),
) -> IngestResult:
    """Upload an analytics export as CSV. Source: linkedin, gsc, ga4, email or ads."""
    return _guard(lambda: ingest(engine, workspace, source, csv_text))


@app.post("/workspaces/{workspace}/analysis")
def run_analysis(
    workspace: str,
    analyst: AnalystAgent = Depends(analyst_dep),
    engine: Engine = Depends(engine_dep),
) -> WeeklyLearnings:
    """Analyse the uploaded data and save this week's learnings for the strategist to rule on."""
    try:
        strategy = load_latest_strategy(workspace)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _guard(lambda: analyst.run(workspace, strategy, engine))


@app.get("/workspaces/{workspace}/learning-log.json")
def learning_log_json(workspace: str, engine: Engine = Depends(engine_dep)) -> list[dict]:
    return learning_log(engine, workspace)


@app.get("/workspaces/{workspace}/learning-log", response_class=HTMLResponse)
def learning_log_page(workspace: str, engine: Engine = Depends(engine_dep)) -> str:
    """How the strategy evolved week by week, and why."""
    template = _templates.get_template("learning_log.html")
    return template.render(workspace=workspace, entries=learning_log(engine, workspace))


# --- guardrails and cost dashboard ---


@app.get("/workspaces/{workspace}/guardrail-blocks")
def guardrail_blocks(workspace: str, engine: Engine = Depends(engine_dep)) -> list[GuardrailBlock]:
    """Every time a guardrail stopped a draft, newest first."""
    with Session(engine) as session:
        query = select(GuardrailBlock).where(GuardrailBlock.workspace == workspace)
        return list(session.exec(query.order_by(GuardrailBlock.id.desc())))


@app.get("/costs/summary.json")
def costs_json(workspace: str | None = None, engine: Engine = Depends(engine_dep)) -> dict:
    return cost_summary(engine, workspace)


@app.get("/costs/dashboard", response_class=HTMLResponse)
def costs_page(workspace: str | None = None, engine: Engine = Depends(engine_dep)) -> str:
    """Cost per agent, per piece and per week, with a freelancer comparison."""
    return _templates.get_template("costs.html").render(data=cost_summary(engine, workspace))
