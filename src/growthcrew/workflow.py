"""Human-in-the-loop workflow: decisions, scheduling, publishing, measuring.

Every path from a draft to the outside world goes through `require_approved`.
"""

import difflib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew import audit, guardrails
from growthcrew.brain.learning import record_edit
from growthcrew.brain.store import WORKSPACES_DIR, load_brain
from growthcrew.db.models import Approval, CalendarItem, Cycle, Draft
from growthcrew.integrations import PUBLISHERS

Decision = Literal["approved", "rejected", "edited"]

# Stages in order. Everything after `critic` needs a human action to reach.
STAGES = (
    "research", "strategy_check", "content_plan", "drafting", "critic",
    "awaiting_approval", "scheduled", "published", "measured",
)  # fmt: skip
CHANNELS = {
    "linkedin_post": "LinkedIn",
    "x_thread": "X",
    "blog_article": "Blog",
    "cold_email_sequence": "Email outreach",
    "ad": "Paid ads",
    "landing_hero": "Website",
    "newsletter": "Newsletter",
}


class WorkflowError(RuntimeError):
    """The action is not allowed in the current state."""


class ApprovalRequired(WorkflowError):
    pass


def require_approved(draft: Draft) -> None:
    if draft.status != "approved":
        raise ApprovalRequired(
            f"Draft {draft.id} is '{draft.status}'. A human must approve it first."
        )


def _get(session: Session, model, id: int):
    row = session.get(model, id)
    if row is None:
        raise LookupError(f"{model.__name__} {id} not found")
    return row


def refresh_stage(session: Session, cycle_id: int) -> None:
    """Move a cycle through its human-driven stages according to what has been done."""
    cycle = _get(session, Cycle, cycle_id)
    if STAGES.index(cycle.stage) < STAGES.index("awaiting_approval"):
        return
    drafts = session.exec(select(Draft).where(Draft.cycle_id == cycle_id)).all()
    items = session.exec(select(CalendarItem).where(CalendarItem.cycle_id == cycle_id)).all()
    if any(draft.status == "pending_approval" for draft in drafts):
        cycle.stage = "awaiting_approval"
    elif not items:
        cycle.stage, cycle.halted_reason = "awaiting_approval", "every draft was rejected"
    elif all(item.status == "measured" for item in items):
        cycle.stage = "measured"
    elif all(item.status in ("published", "measured") for item in items):
        cycle.stage = "published"
    else:
        cycle.stage = "scheduled"
    cycle.updated_at = datetime.now(UTC)
    session.add(cycle)


def decide(
    engine: Engine,
    draft_id: int,
    decision: Decision,
    reviewer: str,
    comment: str = "",
    edited_text: str | None = None,
    root: Path = WORKSPACES_DIR,
) -> tuple[Approval, list[str]]:
    """Record a human decision. Returns it with any voice rules learned from the edit."""
    if not reviewer.strip():
        raise WorkflowError("A decision needs a named human reviewer")
    learned: list[str] = []
    with Session(engine, expire_on_commit=False) as session:
        draft = _get(session, Draft, draft_id)
        if draft.status not in ("pending_approval", "blocked"):
            raise WorkflowError(f"Draft {draft_id} was already {draft.status}")
        if draft.status == "blocked" and decision == "approved":
            raise WorkflowError(
                f"Draft {draft_id} was blocked by a guardrail. Reject it, or edit out the "
                "problem and submit it as 'edited'."
            )
        diff = ""
        if decision == "edited":
            if not edited_text or edited_text == draft.text:
                raise WorkflowError("An 'edited' decision needs edited_text that differs")
            try:
                brand = load_brain(draft.workspace, root=root)
            except FileNotFoundError:
                brand = None
            violations = guardrails.check(edited_text.splitlines(), brand)
            if violations:
                guardrails.log_blocks(engine, draft.workspace, draft.piece_id, "edit", violations)
                raise WorkflowError(
                    "The edited text still breaks a guardrail: "
                    + "; ".join(f"line {v.line}: {v.reason} ('{v.excerpt}')" for v in violations)
                )
            diff = "\n".join(
                difflib.unified_diff(
                    draft.text.splitlines(),
                    edited_text.splitlines(),
                    "draft",
                    "edited",
                    lineterm="",
                )
            )
            original, draft.text = draft.text, edited_text
        draft.status = "rejected" if decision == "rejected" else "approved"
        approval = Approval(
            draft_id=draft_id, decision=decision, reviewer=reviewer, comment=comment, diff=diff
        )
        session.add_all([draft, approval])
        if draft.status == "approved":
            cycle = _get(session, Cycle, draft.cycle_id)
            session.add(
                CalendarItem(
                    draft_id=draft_id,
                    cycle_id=draft.cycle_id,
                    workspace=draft.workspace,
                    scheduled_for=cycle.week_start + timedelta(days=(draft.day or 1) - 1),
                    channel=CHANNELS.get(draft.content_type, draft.content_type),
                )
            )
        session.flush()
        refresh_stage(session, draft.cycle_id)
        session.commit()
        workspace = draft.workspace
    audit.record(engine, workspace, reviewer, f"draft.{decision}", f"draft:{draft_id}",
                 piece=draft.piece_id, comment=comment, diff=diff)  # fmt: skip
    if decision == "edited":
        learned = record_edit(engine, workspace, original, edited_text, root)
    return approval, learned


def publish(
    engine: Engine,
    item_id: int,
    published_by: str,
    via: str = "manual",
    confirm: bool = False,
    url: str | None = None,
) -> CalendarItem:
    """Mark an item published by hand, or publish it through an integration.

    Requires an approved draft and an explicit confirmation from a named human.
    """
    if not confirm or not published_by.strip():
        raise WorkflowError("Publishing needs confirm=true and the name of the person publishing")
    with Session(engine, expire_on_commit=False) as session:
        item = _get(session, CalendarItem, item_id)
        draft = _get(session, Draft, item.draft_id)
        require_approved(draft)
        if item.status != "scheduled":
            raise WorkflowError(f"Calendar item {item_id} is already {item.status}")
        if via != "manual":
            if via not in PUBLISHERS:
                raise WorkflowError(
                    f"No '{via}' integration is installed. Export the piece and publish it "
                    "yourself, then record it with via='manual'."
                )
            PUBLISHERS[via].publish(draft, item)
        item.status, item.published_via, item.published_by = "published", via, published_by
        item.published_at, item.published_url = datetime.now(UTC), url
        session.add(item)
        session.flush()
        refresh_stage(session, item.cycle_id)
        session.commit()
    audit.record(engine, item.workspace, published_by, "draft.published", f"draft:{draft.id}",
                 via=via, url=url, external_id=item.external_id)  # fmt: skip
    return item


def record_metrics(engine: Engine, item_id: int, metrics: dict[str, float]) -> CalendarItem:
    with Session(engine, expire_on_commit=False) as session:
        item = _get(session, CalendarItem, item_id)
        if item.status == "scheduled":
            raise WorkflowError("This item has not been published yet")
        item.status, item.metrics_json = "measured", json.dumps(metrics)
        session.add(item)
        session.flush()
        refresh_stage(session, item.cycle_id)
        session.commit()
        return item
