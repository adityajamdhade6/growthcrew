"""Endpoints the web app needs beyond the core workflow API."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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
from growthcrew.connectors import google as google_connector
from growthcrew.connectors import store as connector_store
from growthcrew.connectors.mcp_source import check_url
from growthcrew.tools.fetch import BlockedAddress
from growthcrew.connectors.sync import sync_workspace
from growthcrew.creative import agent as creative
from growthcrew.creative.images import generator as image_generator
from growthcrew.creative.kit import save_brand_image
from growthcrew.creative.landing import export_variants, landing_html
from growthcrew.creative.render import SIZES, renderer
from growthcrew.db.models import (
    CalendarItem,
    Creative,
    Cycle,
    Draft,
    OnboardingJob,
    PanelRun,
    PerformanceRow,
    RoleModel,
    Signal,
    StrategyComment,
)
from growthcrew.llm import LLM
from growthcrew.memory import playbook
from growthcrew.monitor import signals as monitor_signals
from growthcrew.monitor.run import run_monitors
from growthcrew.monitor.seo import ranking_history
from growthcrew.panel import calibration as panel_calibration
from growthcrew.panel import personas as panel_personas
from growthcrew.reports.strategy import save_strategy
from growthcrew.workflow import require_approved

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


# --- signals inbox ---


@router.get("/workspaces/{workspace}/signals")
def list_signals(
    workspace: str, status: str = "new", engine: Engine = Depends(engine_dep)
) -> list[dict]:
    """Monitor findings with this status, most important first."""
    if status not in ("new", "sent", "dismissed"):
        raise HTTPException(400, "status must be new, sent or dismissed")
    return monitor_signals.inbox(engine, workspace, status)


class SignalDecisionIn(BaseModel):
    action: str


@router.post("/signals/{signal_id}")
def decide_signal(
    signal_id: int, body: SignalDecisionIn, request: Request, engine: Engine = Depends(engine_dep)
) -> dict:
    """Send a signal to the strategist or dismiss it, as the signed-in person."""
    user = auth.current_user(request)
    person = user.email if user else "unknown"
    with Session(engine) as session:
        signal = session.get(Signal, signal_id)
        if signal is None:
            raise HTTPException(404, "Signal not found")
        workspace = signal.workspace
    return guard(lambda: monitor_signals.decide(engine, workspace, signal_id, body.action, person))


@router.post("/workspaces/{workspace}/monitor", status_code=202)
def start_monitoring(
    workspace: str,
    background: BackgroundTasks,
    engine: Engine = Depends(engine_dep),
    llm: LLM = Depends(llm_dep),
) -> dict:
    """Run the competitor, SEO and social monitors now. Findings arrive in the inbox."""
    guard(lambda: load_brain(workspace))
    guard(lambda: budget.check(engine, workspace))
    background.add_task(run_monitors, llm, engine, workspace, WORKSPACES_DIR)
    return {"workspace": workspace, "status": "started"}


@router.get("/workspaces/{workspace}/rankings")
def keyword_ranking(workspace: str, keyword: str, engine: Engine = Depends(engine_dep)) -> list:
    """A keyword's position over time, from Search Console exports."""
    return ranking_history(engine, workspace, keyword)


# --- ad images and landing pages ---

# Progress of ad-image jobs started from the review panel, by draft id. One process only;
# the job queue in Phase 9 replaces this.
CREATIVE_JOBS: dict[int, dict] = {}


def _make_creatives(llm: LLM, engine: Engine, draft_id: int, root: Path) -> None:
    CREATIVE_JOBS[draft_id] = {"status": "running", "detail": ""}
    try:
        with Session(engine) as session:
            draft = session.get(Draft, draft_id)
        brand = load_brain(draft.workspace, root=root)
        with renderer() as browser:
            agent = creative.CreativeAgent(llm, engine, browser, root, image_generator())
            result = agent.run(draft, brand)
        rounds = len(result.rounds)
        CREATIVE_JOBS[draft_id] = {
            "status": "done",
            "detail": f"{'Passed' if result.passed else 'Did not pass'} the vision critic "
            f"after {rounds} round{'s' if rounds != 1 else ''}",
        }
    except Exception as exc:  # noqa: BLE001 — the panel shows the reason
        CREATIVE_JOBS[draft_id] = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}


@router.post("/drafts/{draft_id}/creatives", status_code=202)
def start_creatives(
    draft_id: int,
    background: BackgroundTasks,
    engine: Engine = Depends(engine_dep),
    llm: LLM = Depends(llm_dep),
) -> dict:
    """Render this ad in three sizes and put it through the vision critic."""
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
    if draft.content_type != "ad":
        raise HTTPException(400, "Ad images are made from ad drafts only")
    if draft.status == "rejected":
        raise HTTPException(409, "This ad was rejected")
    if CREATIVE_JOBS.get(draft_id, {}).get("status") == "running":
        raise HTTPException(409, "Images for this ad are already being made")
    guard(lambda: budget.check(engine, draft.workspace))
    CREATIVE_JOBS[draft_id] = {"status": "running", "detail": ""}
    background.add_task(_make_creatives, llm, engine, draft_id, WORKSPACES_DIR)
    return {"draft_id": draft_id, "status": "running"}


@router.get("/drafts/{draft_id}/creatives")
def get_creatives(draft_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    """The latest ad images with the critic's scores and fixes and the measured checks."""
    return creative.latest(engine, draft_id) | {
        "job": CREATIVE_JOBS.get(draft_id, {"status": "idle", "detail": ""})
    }


@router.get("/drafts/{draft_id}/creatives/{size}.png")
def creative_image(
    draft_id: int,
    size: str,
    round: int | None = None,
    download: bool = False,
    engine: Engine = Depends(engine_dep),
):
    """One rendered size. Viewing is for review; downloading for use needs an approved ad."""
    if size not in SIZES:
        raise HTTPException(404, "Unknown size")
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
        query = select(Creative).where(Creative.draft_id == draft_id, Creative.size == size)
        if round is not None:
            query = query.where(Creative.round == round)
        row = session.exec(query.order_by(Creative.round.desc())).first()
    if row is None:
        raise HTTPException(404, "No image for this size yet")
    if download:
        guard(lambda: require_approved(draft))
    path = (WORKSPACES_DIR / row.path).resolve()
    if WORKSPACES_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "Image file not found")
    headers = (
        {"Content-Disposition": f'attachment; filename="{draft.piece_id}-{size}.png"'}
        if download
        else None
    )
    return FileResponse(path, media_type="image/png", headers=headers)


@router.get("/drafts/{draft_id}/landing.html", response_class=HTMLResponse)
def landing_preview(draft_id: int, engine: Engine = Depends(engine_dep)) -> HTMLResponse:
    """A landing hero rendered as HTML, for review. Scripts cannot run in it."""
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
    if draft.content_type != "landing_hero":
        raise HTTPException(400, "Only landing hero drafts have a page")
    brand = guard(lambda: load_brain(draft.workspace))
    html, problems = guard(lambda: landing_html(draft, brand, WORKSPACES_DIR))
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; "
            "style-src 'unsafe-inline'",
            "X-Accessibility-Problems": "; ".join(problems)[:500],
        },
    )


@router.post("/drafts/{draft_id}/landing/export")
def export_landing(draft_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    """Write each approved angle of this landing hero to its own HTML file for an A/B test."""
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
    brand = guard(lambda: load_brain(draft.workspace))
    paths = guard(lambda: export_variants(engine, draft_id, brand, WORKSPACES_DIR))
    return {"files": [str(path.relative_to(WORKSPACES_DIR)) for path in paths]}


@router.post("/workspaces/{workspace}/brand/images")
async def upload_brand_image(
    workspace: str,
    request: Request,
    name: str,
    alt: str = "",
    logo: bool = False,
    engine: Engine = Depends(engine_dep),
) -> dict:
    """Add a logo or product photo to the brand kit. The body is the image's bytes.

    Saved as a new brain version; a person adding it confirms the brand kit field.
    """
    data = await request.body()
    stored = guard(lambda: save_brand_image(workspace, name, data, WORKSPACES_DIR))
    brain = guard(lambda: load_brain(workspace, root=WORKSPACES_DIR))
    kit = brain.brand_kit.model_dump(mode="json")
    if logo:
        kit["logo_file"] = stored
    else:
        kit["product_images"].append({"file": stored, "alt": alt.strip()})
    guard(lambda: update_field(workspace, "brand_kit", kit, root=WORKSPACES_DIR, engine=engine))
    return {"file": stored, "brand_kit": kit}


# --- synthetic panel ---


@router.get("/workspaces/{workspace}/panel")
def panel_summary(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """The panel's personas and its record against real test results."""
    people = panel_personas.panel(engine, workspace)
    return {
        "accuracy": panel_calibration.accuracy(engine, workspace),
        "generation": people[0].generation if people else 0,
        "personas": [
            {"name": p.name, **json.loads(p.profile), "support": json.loads(p.support)}
            for p in people
        ],
    }


@router.get("/drafts/{draft_id}/panel")
def draft_panel(draft_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    """The panel's pre-test of the experiment this draft belongs to, if there was one."""
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
        base = _base_id(draft)
        run = session.exec(
            select(PanelRun)
            .where(PanelRun.cycle_id == draft.cycle_id, PanelRun.experiment == base)
            .order_by(PanelRun.created_at.desc())
        ).first()
    if run is None:
        return {"experiment": base, "ran": False}
    return {
        "experiment": base,
        "ran": True,
        "trust": run.trust,
        "prediction": json.loads(run.prediction),
        "recommendation": json.loads(run.recommendation),
        "accuracy_message": panel_calibration.accuracy(engine, draft.workspace)["message"],
    }


# --- connectors and MCP approvals ---


def _person(request: Request) -> str:
    user = auth.current_user(request)
    return user.email if user else "unknown"


@router.get("/workspaces/{workspace}/connectors")
def list_connectors(workspace: str, engine: Engine = Depends(engine_dep)) -> list[dict]:
    """Which sources are connected and when they last synced. Secrets are never returned."""
    return connector_store.status(engine, workspace)


class ConnectorIn(BaseModel):
    api_key: str | None = None
    settings: dict = {}


@router.put("/workspaces/{workspace}/connectors/{provider}")
def save_connector(
    workspace: str,
    provider: str,
    body: ConnectorIn,
    request: Request,
    engine: Engine = Depends(engine_dep),
) -> list[dict]:
    """Store an API key (encrypted) and settings for a source."""
    if provider == "google" and body.api_key:
        raise HTTPException(400, "Google is connected by signing in, not with a key")
    if provider == "mcp":
        if "url" in body.settings:
            try:
                check_url(str(body.settings["url"]))
            except (ValueError, BlockedAddress) as exc:
                raise HTTPException(400, str(exc)) from exc
        if body.settings.get("source", "ga4") not in SOURCES:
            raise HTTPException(400, f"source must be one of: {', '.join(SOURCES)}")
    secret = {"api_key": body.api_key.strip()} if body.api_key else None
    if provider == "mcp" and secret is None:
        secret = {}
    guard(lambda: connector_store.save(engine, workspace, provider, _person(request),
                                       secret=secret, settings=body.settings or None))  # fmt: skip
    return connector_store.status(engine, workspace)


@router.delete("/workspaces/{workspace}/connectors/{provider}")
def delete_connector(workspace: str, provider: str, engine: Engine = Depends(engine_dep)) -> list:
    connector_store.remove(engine, workspace, provider)
    return connector_store.status(engine, workspace)


@router.post("/workspaces/{workspace}/connectors/google/start")
def start_google(workspace: str, request: Request) -> dict:
    """The Google sign-in URL for read-only Search Console and GA4 access."""
    url = guard(lambda: google_connector.authorization_url(workspace, _person(request)))
    return {"url": url}


@router.get("/connectors/google/callback")
def google_callback(
    code: str = "", state: str = "", error: str = "", engine: Engine = Depends(engine_dep)
):
    """Where Google sends the person back. The signed state says which workspace and who."""
    import httpx

    target = "/settings?connected=google#sources"
    if error or not code:
        return RedirectResponse(f"/settings?error={error or 'cancelled'}#sources")
    try:
        with httpx.Client(timeout=30.0) as client:
            google_connector.finish(engine, code, state, client)
    except Exception as exc:  # noqa: BLE001 — shown on the Settings page, not as a stack trace
        reason = str(exc).replace("\n", " ")[:200]
        return RedirectResponse(f"/settings?error={reason}#sources")
    return RedirectResponse(target)


@router.post("/workspaces/{workspace}/connectors/sync", status_code=202)
def sync_now(
    workspace: str, background: BackgroundTasks, engine: Engine = Depends(engine_dep)
) -> dict:
    """Pull fresh metrics from every connected source now."""
    background.add_task(sync_workspace, engine, workspace, WORKSPACES_DIR)
    return {"workspace": workspace, "status": "started"}


class ApprovalIn(BaseModel):
    action: str


@router.post("/workspaces/{workspace}/mcp/approvals")
def mcp_approval(workspace: str, body: ApprovalIn, request: Request) -> dict:
    """A single-use token that lets an MCP client do one action here in the next 15 minutes."""
    from growthcrew.mcp_server import TOKEN_TTL, mint_approval

    token = guard(lambda: mint_approval(workspace, body.action, _person(request)))
    return {"token": token, "expires_in": TOKEN_TTL, "action": body.action}
