"""Routes for the product UX: live mission control, replay, approval channels, gallery, evals."""

import asyncio
import json
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew import approvals, tracing
from growthcrew.api import auth
from growthcrew.api.deps import engine_dep, guard
from growthcrew.brain.store import WORKSPACES_DIR
from growthcrew.creative.agent import latest as latest_creatives
from growthcrew.db.models import Creative, Cycle, Draft, Span
from growthcrew.naming import display, piece_name

router = APIRouter()
EVALS = Path(__file__).resolve().parents[3] / "evals"
STREAM_SECONDS = 600


def _person(request: Request) -> str:
    user = auth.current_user(request)
    return user.email if user else "unknown"


# --- live mission control ---


@router.get("/workspaces/{workspace}/mission/stream")
async def mission_stream(workspace: str, request: Request, engine: Engine = Depends(engine_dep)):
    """Server-sent events: the mission view each time it changes, for up to ten minutes."""
    from growthcrew.api.ui import mission

    async def events():
        last, waited = None, 0.0
        while waited < STREAM_SECONDS and not await request.is_disconnected():
            data = await anyio.to_thread.run_sync(mission, workspace, engine)
            body = json.dumps(data, default=str)
            if body != last:
                last = body
                yield f"event: mission\ndata: {body}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.5)
            waited += 1.5

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/workspaces/{workspace}/replay")
def replay(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """The last traced cycle as timed events, for the demo's "replay a week" mode."""
    with Session(engine) as session:
        cycle = session.exec(
            select(Cycle)
            .where(Cycle.workspace == workspace, Cycle.trace_id != "")
            .order_by(Cycle.id.desc())
        ).first()
        if cycle is None:
            return {"cycle_id": None, "events": []}
        spans = session.exec(
            select(Span).where(Span.trace_id == cycle.trace_id).order_by(Span.started_at)
        ).all()
        drafts = session.exec(select(Draft).where(Draft.cycle_id == cycle.id)).all()
    if not spans:
        return {"cycle_id": cycle.id, "events": []}
    start = spans[0].started_at
    events = []
    for row in spans:
        if row.kind not in ("stage", "llm"):
            continue
        attrs = json.loads(row.attributes)
        events.append(
            {
                "at": (row.started_at - start).total_seconds(),
                "kind": row.kind,
                "name": display(row.name),
                "duration": row.duration_ms / 1000,
                "cost_usd": attrs.get("cost_usd") or 0,
                "detail": attrs.get("detail", ""),
            }
        )
    end = max((e["at"] + e["duration"] for e in events), default=0)
    events += [
        {
            "at": end,
            "kind": "draft",
            "name": piece_name(d.piece_id),
            "duration": 0,
            "cost_usd": 0,
            "detail": display(d.status),
        }
        for d in drafts
    ]
    return {"cycle_id": cycle.id, "seconds": end, "events": events}


# --- approval channels ---


class SlackSecretIn(BaseModel):
    webhook_url: str
    signing_secret: str
    channel: str = ""


@router.put("/workspaces/{workspace}/connectors/slack/secrets")
def connect_slack(
    workspace: str, body: SlackSecretIn, request: Request, engine: Engine = Depends(engine_dep)
) -> dict:
    from growthcrew import audit
    from growthcrew.connectors import store

    if not body.webhook_url.startswith("https://hooks.slack.com/"):
        raise HTTPException(400, "Use the incoming webhook URL Slack gave you (hooks.slack.com)")
    store.save(
        engine,
        workspace,
        "slack",
        _person(request),
        secret={"webhook_url": body.webhook_url, "signing_secret": body.signing_secret},
        settings={"channel": body.channel},
    )
    audit.record(engine, workspace, _person(request), "connector.saved", "slack")
    return {"connected": True}


class SlackMemberIn(BaseModel):
    email: str
    slack_user_id: str


@router.put("/workspaces/{workspace}/members/slack")
def link_slack(workspace: str, body: SlackMemberIn, engine: Engine = Depends(engine_dep)) -> dict:
    guard(lambda: approvals.link_slack_user(engine, workspace, body.email, body.slack_user_id))
    return {"linked": body.email}


@router.post("/workspaces/{workspace}/slack/notify")
def notify_slack(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """Post the drafts waiting for approval to Slack, each with Approve and Reject buttons."""
    return {"posted": guard(lambda: approvals.post_to_slack(engine, workspace))}


@router.post("/integrations/slack/actions")
async def slack_actions(request: Request, engine: Engine = Depends(engine_dep)) -> dict:
    """Slack calls this when someone presses a button. Verified with the signing secret."""
    from urllib.parse import parse_qs

    body = await request.body()
    payload = json.loads(parse_qs(body.decode()).get("payload", ["{}"])[0])
    try:
        workspace = json.loads(payload["actions"][0]["value"])["w"]
        approvals.verify_slack(
            engine,
            workspace,
            request.headers.get("x-slack-request-timestamp", ""),
            body,
            request.headers.get("x-slack-signature", ""),
        )
        done = await anyio.to_thread.run_sync(approvals.slack_action, engine, payload)
    except (KeyError, IndexError, ValueError) as exc:
        raise HTTPException(400, "Malformed Slack action") from exc
    except approvals.ApprovalRefused as exc:
        return {"response_type": "ephemeral", "text": str(exc)}
    except Exception as exc:  # noqa: BLE001 — e.g. already decided; shown to the presser
        return {"response_type": "ephemeral", "text": f"Not done: {exc}"}
    return {"response_type": "in_channel", "replace_original": False, "text": done}


@router.post("/workspaces/{workspace}/digest/send")
def send_digest(workspace: str, engine: Engine = Depends(engine_dep)) -> dict:
    """Email each approver this week's pending drafts with one-time approve and reject links."""
    return {"sent_to": guard(lambda: approvals.send_digest(engine, workspace, WORKSPACES_DIR))}


@router.get("/approve/{token}", response_class=HTMLResponse)
def approve_page(token: str, engine: Engine = Depends(engine_dep)) -> HTMLResponse:
    try:
        return HTMLResponse(approvals.confirm_page(engine, token))
    except approvals.ApprovalRefused as exc:
        return HTMLResponse(f"<p>{exc}</p>", status_code=410)


@router.post("/approve/{token}", response_class=HTMLResponse)
def approve_confirm(token: str, engine: Engine = Depends(engine_dep)) -> HTMLResponse:
    try:
        done = approvals.use_link(engine, token)
    except approvals.ApprovalRefused as exc:
        return HTMLResponse(f"<p>{exc}</p>", status_code=410)
    except Exception as exc:  # noqa: BLE001 — e.g. the draft was already decided in the app
        return HTMLResponse(f"<p>Not done: {exc}</p>", status_code=409)
    return HTMLResponse(f"<p>Done: {done}. You can close this page.</p>")


# --- creative gallery and evals ---


@router.get("/workspaces/{workspace}/creatives")
def gallery(workspace: str, engine: Engine = Depends(engine_dep)) -> list[dict]:
    """Every ad with images, newest first, with its latest round and the draft's status."""
    with Session(engine) as session:
        ids = sorted(
            {
                row.draft_id
                for row in session.exec(select(Creative).where(Creative.workspace == workspace))
            },
            reverse=True,
        )
        drafts = {d.id: d for d in session.exec(select(Draft).where(Draft.id.in_(ids)))}
    return [
        {
            "draft_id": i,
            "piece": piece_name(drafts[i].piece_id),
            "status": drafts[i].status,
            **latest_creatives(engine, i),
        }
        for i in ids
        if i in drafts
    ]


@router.get("/evals/scorecard")
def scorecard() -> dict:
    """The latest eval scorecard; the committed baseline when no run has happened here."""
    for path, source in (
        (EVALS / "results" / "scorecard.json", "latest run"),
        (EVALS / "baseline.json", "committed baseline"),
    ):
        if path.exists():
            return json.loads(path.read_text()) | {"source": source}
    return {"results": [], "source": "none"}


@router.get("/cycles/{cycle_id}/trace/summary")
def trace_summary(cycle_id: int, engine: Engine = Depends(engine_dep)) -> dict:
    """A cycle's trace with totals, for the Evals page."""
    with Session(engine) as session:
        cycle = session.get(Cycle, cycle_id)
    spans = tracing.spans(engine, cycle.trace_id) if cycle.trace_id else []
    calls = [json.loads(s.attributes) for s in spans if s.kind == "llm"]
    return {
        "cycle_id": cycle_id,
        "trace_id": cycle.trace_id,
        "model_calls": len(calls),
        "tool_calls": sum(s.kind == "tool" for s in spans),
        "cost_usd": round(sum(c.get("cost_usd") or 0 for c in calls), 4),
        "tree": tracing.tree(engine, cycle.trace_id) if cycle.trace_id else [],
    }
