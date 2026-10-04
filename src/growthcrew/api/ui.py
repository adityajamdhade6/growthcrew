"""Endpoints the web app needs beyond the core workflow API."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew import budget, config
from growthcrew.agents.orchestrator import timeline
from growthcrew.agents.research import load_latest_research
from growthcrew.agents.strategist import SECTIONS, StrategistAgent, load_latest_strategy
from growthcrew.analytics.analysis import analyze
from growthcrew.analytics.ingest import SOURCES
from growthcrew.api import auth
from growthcrew.api.deps import engine_dep, guard, llm_dep
from growthcrew.brain import learning
from growthcrew.brain.models import FIELD_PATHS
from growthcrew.brain.onboarding import Questionnaire, onboard, workspace_name
from growthcrew.brain.store import (
    WORKSPACES_DIR,
    confirm_fields,
    list_versions,
    load_brain,
    update_field,
)
from growthcrew.config import AgentRole
from growthcrew.db.models import (
    CalendarItem,
    Cycle,
    Draft,
    OnboardingJob,
    PerformanceRow,
    RoleModel,
    StrategyComment,
)
from growthcrew.llm import LLM
from growthcrew.memory import playbook
from growthcrew.reports.strategy import save_strategy

router = APIRouter()


# --- workspaces and onboarding ---


def _workspace_names(root: Path = WORKSPACES_DIR) -> list[str]:
    return sorted(p.name for p in root.glob("*") if p.is_dir() and list_versions(p.name, root))


@router.get("/workspaces")
def list_workspaces(request: Request, engine: Engine = Depends(engine_dep)) -> list[dict]:
    """Workspaces the signed-in user can open."""
    user = auth.current_user(request)
    with Session(engine) as session:
        jobs = {job.workspace: job for job in session.exec(select(OnboardingJob))}
        names = sorted(set(_workspace_names()) | set(jobs))
        out = []
        for name in names:
            if user and not user.can_access(name):
                continue
            try:
                brand = load_brain(name).business.name or name
            except FileNotFoundError:
                brand = name
            pending = session.exec(
                select(func.count(Draft.id)).where(
                    Draft.workspace == name, Draft.status == "pending_approval"
                )
            ).one()
            job = jobs.get(name)
            out.append(
                {
                    "workspace": name,
                    "name": brand,
                    "pending_approvals": pending,
                    "onboarding": job.status if job else "done",
                }
            )
    return out


class OnboardIn(BaseModel):
    url: str
    answers: Questionnaire = Questionnaire()


def _run_onboarding(engine: Engine, llm: LLM, workspace: str, body: OnboardIn) -> None:
    def progress(status: str, detail: str) -> None:
        with Session(engine) as session:
            session.merge(
                OnboardingJob(
                    workspace=workspace,
                    url=body.url,
                    status=status,
                    detail=detail,
                    updated_at=datetime.now(UTC),
                )  # fmt: skip
            )
            session.commit()

    try:
        onboard(body.url, body.answers, llm, workspace=workspace, engine=engine, progress=progress)
        progress("done", "The brain is drafted and ready for review")
    except Exception as exc:
        progress("failed", f"{type(exc).__name__}: {exc}"[:400])


@router.post("/onboarding", status_code=202)
def start_onboarding(
    body: OnboardIn,
    request: Request,
    background: BackgroundTasks,
    engine: Engine = Depends(engine_dep),
    llm: LLM = Depends(llm_dep),
) -> dict:
    """Crawl a website and draft its brain in the background."""
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Enter the full website address, starting with https://")
    workspace = workspace_name(body.url)
    user = auth.current_user(request)
    if user:
        if workspace in _workspace_names() and not user.can_access(workspace):
            raise HTTPException(403, "That workspace already exists and belongs to someone else")
        auth.grant(engine, user, workspace)
    with Session(engine) as session:
        session.merge(OnboardingJob(workspace=workspace, url=body.url, detail="Queued"))
        session.commit()
    background.add_task(_run_onboarding, engine, llm, workspace, body)
    return {"workspace": workspace}


@router.get("/workspaces/{workspace}/onboarding")
def onboarding_status(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    with Session(engine) as session:
        job = session.get(OnboardingJob, workspace)
    if job is None:
        return {"status": "done" if workspace in _workspace_names() else "none", "detail": ""}
    return {"status": job.status, "detail": job.detail, "url": job.url}


# --- brain ---


def _brain_payload(workspace: str) -> dict:
    brain = load_brain(workspace)
    return {
        "brain": brain.model_dump(mode="json"),
        "field_paths": list(FIELD_PATHS),
        "weakest": [{"path": path, "reason": reason} for path, reason in brain.weakest()],
    }


class ConfirmIn(BaseModel):
    paths: list[str]


class FieldIn(BaseModel):
    path: str
    value: object


@router.get("/workspaces/{workspace}/brain")
def get_brain(workspace: str) -> dict:
    return guard(lambda: _brain_payload(workspace))


@router.post("/workspaces/{workspace}/brain/confirm")
def confirm_brain(workspace: str, body: ConfirmIn, engine: Engine = Depends(engine_dep)) -> dict:
    guard(lambda: confirm_fields(workspace, body.paths, engine=engine))
    return _brain_payload(workspace)


@router.put("/workspaces/{workspace}/brain/field")
def edit_brain_field(workspace: str, body: FieldIn, engine: Engine = Depends(engine_dep)) -> dict:
    """Set a field's value. A human edit also marks the field confirmed."""
    guard(lambda: update_field(workspace, body.path, body.value, engine=engine))
    return _brain_payload(workspace)


# --- research and strategy ---


@router.get("/workspaces/{workspace}/research")
def get_research(workspace: str) -> dict:
    return guard(lambda: load_latest_research(workspace).model_dump(mode="json"))


@router.get("/workspaces/{workspace}/strategy")
def get_strategy(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    doc = guard(lambda: load_latest_strategy(workspace))
    with Session(engine) as session:
        comments = session.exec(
            select(StrategyComment)
            .where(StrategyComment.workspace == workspace)
            .order_by(StrategyComment.id.desc())
        ).all()
    return {
        "strategy": doc.model_dump(mode="json", exclude={"first_draft"}),
        "sections": list(SECTIONS),
        "comments": [json.loads(comment.model_dump_json()) for comment in comments],
    }


class CommentIn(BaseModel):
    section: str
    comment: str


def _revise(engine: Engine, llm: LLM, comment_id: int) -> None:
    with Session(engine, expire_on_commit=False) as session:
        comment = session.get(StrategyComment, comment_id)
    try:
        doc = load_latest_strategy(comment.workspace)
        new, note = StrategistAgent(llm).revise_section(doc, comment.section, comment.comment)
        save_strategy(new, WORKSPACES_DIR)
        status, response = "revised", note
    except Exception as exc:
        status, response = "failed", f"{type(exc).__name__}: {exc}"[:400]
    with Session(engine) as session:
        row = session.get(StrategyComment, comment_id)
        row.status, row.response = status, response
        session.add(row)
        session.commit()


@router.post("/workspaces/{workspace}/strategy/comments", status_code=202)
def comment_on_strategy(
    workspace: str,
    body: CommentIn,
    request: Request,
    background: BackgroundTasks,
    engine: Engine = Depends(engine_dep),
    llm: LLM = Depends(llm_dep),
) -> StrategyComment:
    """Ask the strategist to revise one section. The revision runs in the background."""
    if body.section not in SECTIONS or not body.comment.strip():
        raise HTTPException(400, "Choose a section and write a comment")
    guard(lambda: load_latest_strategy(workspace))
    user = auth.current_user(request)
    with Session(engine, expire_on_commit=False) as session:
        comment = StrategyComment(
            workspace=workspace, section=body.section, comment=body.comment.strip(),
            author=user.email if user else "reviewer",
        )  # fmt: skip
        session.add(comment)
        session.commit()
    background.add_task(_revise, engine, llm, comment.id)
    return comment


# --- mission control ---


@router.get("/workspaces/{workspace}/mission")
def mission(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """The latest cycle's timeline plus budget: everything the team board shows."""
    with Session(engine) as session:
        cycle = session.exec(
            select(Cycle).where(Cycle.workspace == workspace).order_by(Cycle.id.desc())
        ).first()
    return {
        "timeline": timeline(engine, cycle.id) if cycle else None,
        "budget": {
            "weekly_limit_usd": budget.weekly_limit(engine, workspace),
            "spent_this_week_usd": round(budget.weekly_spend(engine, workspace), 4),
        },
    }


# --- calendar board and review ---


def _scores(history_json: str) -> dict:
    history = json.loads(history_json or "[]")
    critique = history[-1].get("critique") if history else None
    if not critique:
        return {}
    names = ("voice", "clarity", "persuasion", "accuracy", "channel_fit", "ai_cliche")
    return {name: critique[name]["score"] for name in names}


def _base_id(draft: Draft) -> str:
    return draft.piece_id.removesuffix(f"-{draft.angle}") if draft.angle else draft.piece_id


@router.get("/workspaces/{workspace}/board")
def board(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """Every draft with the date it is planned for, for the content calendar."""
    with Session(engine) as session:
        rows = session.exec(
            select(Draft, Cycle, CalendarItem)
            .join(Cycle, Cycle.id == Draft.cycle_id)
            .join(CalendarItem, CalendarItem.draft_id == Draft.id, isouter=True)
            .where(Draft.workspace == workspace)
            .order_by(Draft.id)
        ).all()
    drafts = []
    for draft, cycle, item in rows:
        planned = (
            item.scheduled_for if item else cycle.week_start + timedelta(days=(draft.day or 1) - 1)
        )
        drafts.append(
            {
                "id": draft.id,
                "piece": draft.piece_id,
                "group": f"{draft.cycle_id}:{_base_id(draft)}",
                "content_type": draft.content_type,
                "angle": draft.angle,
                "date": planned.date().isoformat(),
                "status": item.status if item else draft.status,
                "lowest_score": draft.min_score,
                "passed_critic": draft.passed_critic,
                "preview": draft.text[:140],
            }
        )
    return {"drafts": drafts}


class ScheduleIn(BaseModel):
    date: date


@router.patch("/drafts/{draft_id}/schedule")
def reschedule(draft_id: int, body: ScheduleIn, engine: Engine = Depends(engine_dep)) -> dict:
    """Move a piece to another day. Published pieces stay where they are."""
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
        cycle = session.get(Cycle, draft.cycle_id)
        item = session.exec(select(CalendarItem).where(CalendarItem.draft_id == draft_id)).first()
        if item and item.status != "scheduled":
            raise HTTPException(409, "This piece is already published, so it cannot be moved")
        if draft.status == "rejected":
            raise HTTPException(409, "A rejected piece cannot be scheduled")
        target = datetime(body.date.year, body.date.month, body.date.day)
        if item:
            item.scheduled_for = target
            session.add(item)
        draft.day = (target - cycle.week_start.replace(tzinfo=None)).days + 1
        session.add(draft)
        session.commit()
    return {"id": draft_id, "date": body.date.isoformat()}


@router.get("/drafts/{draft_id}/review")
def review(draft_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    """Everything the review panel shows for one draft."""
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
        siblings = session.exec(
            select(Draft).where(Draft.cycle_id == draft.cycle_id, Draft.id != draft_id)
        ).all()
        item = session.exec(select(CalendarItem).where(CalendarItem.draft_id == draft_id)).first()
    history = json.loads(draft.history_json or "[]")
    final = history[-1] if history else {}
    return {
        "id": draft.id,
        "piece": draft.piece_id,
        "content_type": draft.content_type,
        "angle": draft.angle,
        "status": draft.status,
        "calendar": json.loads(item.model_dump_json()) if item else None,
        "text": draft.text,
        "edited": draft.text != draft.original_text,
        "metadata": json.loads(draft.metadata_json or "{}"),
        "scores": _scores(draft.history_json),
        "rounds": len(history),
        "edits": (final.get("critique") or {}).get("edits", []),
        "violations": final.get("guardrail_violations", []),
        "first_draft": history[0]["text"] if len(history) > 1 else None,
        "tracking_key": draft.tracking_key,
        # The past winners and playbook rules the writer was shown.
        "memory": json.loads(draft.memory_json or "{}"),
        "prompt_version": draft.prompt_version,
        "strategy_version": draft.strategy_version,
        "variants": [
            {"id": other.id, "angle": other.angle, "status": other.status,
             "lowest_score": other.min_score}
            for other in siblings
            if _base_id(other) == _base_id(draft) and other.angle
        ],
    }  # fmt: skip


# --- results ---


@router.get("/workspaces/{workspace}/analysis")
def analysis(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """This week's numbers, computed on request from the uploaded data. No model call."""
    return analyze(engine, workspace).model_dump()


@router.get("/workspaces/{workspace}/sources")
def sources(workspace: str, engine: Engine = Depends(engine_dep)) -> list[dict]:
    """Connected data sources: what has been uploaded and how much of it matched a piece."""
    with Session(engine) as session:
        rows = session.exec(
            select(
                PerformanceRow.source,
                func.count(PerformanceRow.id),
                func.count(PerformanceRow.draft_id),
                func.max(PerformanceRow.uploaded_at),
            )
            .where(PerformanceRow.workspace == workspace)
            .group_by(PerformanceRow.source)
        ).all()
    found = {source: (total, matched, latest) for source, total, matched, latest in rows}
    return [
        {
            "source": source,
            "rows": found[source][0] if source in found else 0,
            "matched": found[source][1] if source in found else 0,
            "last_upload": found[source][2].isoformat() if source in found else None,
        }
        for source in SOURCES
    ]


# --- settings: model choices ---


class ModelIn(BaseModel):
    role: AgentRole
    model: str


def _models(engine: Engine) -> dict:
    with Session(engine) as session:
        chosen = {row.role: row.model for row in session.exec(select(RoleModel))}
    return {
        "options": [
            {"model": model, "input_per_mtok": price.input, "output_per_mtok": price.output}
            for model, price in config.PRICING.items()
        ],
        "roles": [
            {
                "role": role.value,
                "model": chosen.get(role.value, cfg.model),
                "default": cfg.model,
            }
            for role, cfg in config.AGENT_MODELS.items()
        ],
    }


@router.get("/settings/models")
def get_models(engine: Engine = Depends(engine_dep)) -> dict:
    return _models(engine)


@router.put("/settings/models")
def set_model(body: ModelIn, engine: Engine = Depends(engine_dep)) -> dict:
    """Choose the model for one agent role. Applies to every workspace."""
    if body.model not in config.PRICING:
        raise HTTPException(400, f"Unknown model. Choose one of: {', '.join(config.PRICING)}")
    with Session(engine) as session:
        session.merge(RoleModel(role=body.role.value, model=body.model))
        session.commit()
    return _models(engine)


# --- playbook and voice-rule proposals ---


@router.get("/workspaces/{workspace}/playbook")
def get_playbook(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """Active rules with their evidence and history, and what was learned in 90 days."""
    return playbook.summary(engine, workspace)


class ProposalIn(BaseModel):
    accept: bool


@router.get("/workspaces/{workspace}/voice-proposals")
def voice_proposals(workspace: str, engine: Engine = Depends(engine_dep)) -> list[dict]:
    """Voice rules proposed from recurring human edits, waiting for a decision."""
    return learning.proposals(engine, workspace)


@router.post("/workspaces/{workspace}/voice-proposals/{proposal_id}")
def decide_proposal(
    workspace: str, proposal_id: int, body: ProposalIn, engine: Engine = Depends(engine_dep)
) -> dict:
    outcome = guard(lambda: learning.resolve_proposal(engine, workspace, proposal_id, body.accept))
    return {"id": proposal_id, "status": outcome}
