"""Approving from Slack and from the weekly email digest.

Both end in `workflow.decide`, on behalf of a named GrowthCrew user with the approver role,
and both are recorded in the audit log with the channel they came through.

- Slack: an interactive message per pending draft. The button press is verified with the
  workspace's Slack signing secret (HMAC over the timestamp and body, rejected after 5
  minutes), and the Slack user must be linked to a GrowthCrew user.
- Email: each approver gets the week's pending drafts with one link per decision. A link is
  signed, names the user, the draft and the decision, expires in 72 hours and works once. It
  opens a confirmation page; only the button there decides, so a mail scanner that follows
  links cannot approve anything.
"""

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path

import httpx
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from growthcrew import audit, keys, workflow
from growthcrew.connectors import store
from growthcrew.db.models import ApprovalTokenUse, ChannelIdentity, Draft, User
from growthcrew.naming import display, piece_name

LINK_TTL = 72 * 3600
SLACK_MAX_AGE = 300
DECISIONS = ("approved", "rejected")


class ApprovalRefused(PermissionError):
    pass


def _public_url() -> str:
    return os.getenv("GROWTHCREW_PUBLIC_URL", "http://localhost:3000").rstrip("/")


def pending(engine: Engine, workspace: str) -> list[Draft]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(Draft)
                .where(Draft.workspace == workspace, Draft.status == "pending_approval")
                .order_by(Draft.id)
            )
        )


def _decide(engine: Engine, user: User, draft_id: int, decision: str, channel: str) -> str:
    if decision not in DECISIONS:
        raise ApprovalRefused("Unknown decision")
    with Session(engine) as session:
        draft = session.get(Draft, draft_id)
    if draft is None:
        raise ApprovalRefused("That draft no longer exists")
    role = user.role_in(draft.workspace)
    if role not in ("approver", "owner"):
        raise ApprovalRefused(f"{user.email} cannot approve in this workspace")
    workflow.decide(engine, draft_id, decision, user.email, comment=f"via {channel}")
    audit.record(
        engine,
        draft.workspace,
        user.email,
        "approval.channel",
        f"draft:{draft_id}",
        channel=channel,
        decision=decision,
    )
    return f"{piece_name(draft.piece_id)} {decision} by {user.email}"


# --- email digest ---


def approval_link(user: User, draft_id: int, decision: str) -> str:
    token = keys.sign(
        {
            "purpose": "email-approval",
            "user": user.id,
            "draft": draft_id,
            "decision": decision,
            "nonce": secrets.token_hex(12),
        },
        LINK_TTL,
    )
    return f"{_public_url()}/api/approve/{token}"


def read_link(token: str) -> dict:
    payload = keys.verify(token)
    if not payload or payload.get("purpose") != "email-approval":
        raise ApprovalRefused("This link has expired or is not valid; open GrowthCrew instead")
    return payload


def confirm_page(engine: Engine, token: str) -> str:
    """The page a link opens: what will happen, and one button that does it."""
    payload = read_link(token)
    with Session(engine) as session:
        draft = session.get(Draft, payload["draft"])
    if draft is None:
        raise ApprovalRefused("That draft no longer exists")
    verb = "Approve" if payload["decision"] == "approved" else "Reject"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{verb} draft</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:2rem auto;padding:0 1rem;
color:#1a1a1a}}pre{{white-space:pre-wrap;background:#f4f5f7;padding:1rem;border-radius:.5rem}}
button{{font:inherit;padding:.75rem 1.25rem;border:0;border-radius:.5rem;background:#3552d1;
color:#fff;min-height:48px}}</style></head><body>
<h1>{verb} {html.escape(piece_name(draft.piece_id))}?</h1>
<p>{html.escape(display(draft.content_type))} · status: {html.escape(display(draft.status))}</p>
<pre>{html.escape(draft.text)}</pre>
<form method="post"><button type="submit">{verb} this draft</button></form>
</body></html>"""


def use_link(engine: Engine, token: str) -> str:
    payload = read_link(token)
    try:
        with Session(engine) as session:
            session.add(
                ApprovalTokenUse(
                    nonce=payload["nonce"],
                    workspace="email",
                    action=payload["decision"],
                    approved_by=str(payload["user"]),
                )
            )
            session.commit()
    except IntegrityError as exc:
        raise ApprovalRefused("This link has already been used") from exc
    with Session(engine) as session:
        user = session.get(User, payload["user"])
    if user is None:
        raise ApprovalRefused("The person this link was sent to no longer has an account")
    return _decide(engine, user, payload["draft"], payload["decision"], "email digest")


def digest_html(engine: Engine, workspace: str, user: User) -> str:
    rows = []
    for draft in pending(engine, workspace):
        approve, reject = (approval_link(user, draft.id, d) for d in DECISIONS)
        rows.append(
            f"<h3>{html.escape(piece_name(draft.piece_id))}"
            f"{'' if draft.passed_critic else ' (did not pass the editor)'}</h3>"
            f"<pre style='white-space:pre-wrap'>{html.escape(draft.text)}</pre>"
            f"<p><a href='{approve}'>Approve</a> · <a href='{reject}'>Reject</a> · "
            f"<a href='{_public_url()}/calendar?review=1'>Edit in GrowthCrew</a></p>"
        )
    body = "".join(rows) or "<p>Nothing is waiting for approval this week.</p>"
    return (
        f"<html><body><h2>{len(rows)} drafts waiting for your approval</h2>{body}"
        "<p style='color:#666;font-size:12px'>Each link opens a confirmation page and works "
        "once, for 72 hours.</p></body></html>"
    )


def send_digest(
    engine: Engine, workspace: str, root: Path, client: httpx.Client | None = None
) -> list[str]:
    """Email every approver the pending drafts. Through Brevo if connected; otherwise each
    email is written to `workspaces/<brand>/digests/` for any mail tool to send."""
    with Session(engine) as session:
        people = [
            u
            for u in session.exec(select(User))
            if not u.is_admin and u.role_in(workspace) in ("approver", "owner")
        ]
    drafts = pending(engine, workspace)
    if not drafts:
        return []
    brevo = store.load(engine, workspace, "brevo")
    sent = []
    for person in people:
        body = digest_html(engine, workspace, person)
        subject = f"{len(drafts)} drafts waiting for your approval"
        if brevo:
            secret, settings = brevo
            response = (client or httpx.Client(timeout=30.0)).post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": secret["api_key"], "accept": "application/json"},
                json={
                    "sender": {
                        "name": settings.get("sender_name", "GrowthCrew"),
                        "email": settings["sender_email"],
                    },
                    "to": [{"email": person.email}],
                    "subject": subject,
                    "htmlContent": body,
                },
            )
            response.raise_for_status()
        else:
            folder = root / workspace / "digests"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{int(time.time())}-{person.id}.eml").write_text(
                f"To: {person.email}\nSubject: {subject}\nContent-Type: text/html; charset=utf-8"
                f"\n\n{body}"
            )
        sent.append(person.email)
    audit.record(engine, workspace, "scheduler", "digest.sent", "", to=sent, drafts=len(drafts))
    return sent


# --- Slack ---


def slack_message(engine: Engine, workspace: str) -> dict:
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Drafts waiting for approval"}}
    ]
    for draft in pending(engine, workspace)[:20]:
        blocks += [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{piece_name(draft.piece_id)}*\n```{draft.text[:2500]}```",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "action_id": "approve",
                        "value": json.dumps({"w": workspace, "d": draft.id, "x": "approved"}),
                    },
                    {
                        "type": "button",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "action_id": "reject",
                        "value": json.dumps({"w": workspace, "d": draft.id, "x": "rejected"}),
                    },
                ],
            },
        ]
    return {"text": "Drafts waiting for approval", "blocks": blocks}


def post_to_slack(engine: Engine, workspace: str, client: httpx.Client | None = None) -> int:
    loaded = store.load(engine, workspace, "slack")
    if loaded is None:
        raise LookupError("Slack is not connected for this workspace")
    secret, _ = loaded
    message = slack_message(engine, workspace)
    (client or httpx.Client(timeout=30.0)).post(
        secret["webhook_url"], json=message
    ).raise_for_status()
    return len(message["blocks"]) // 2


def verify_slack(
    engine: Engine,
    workspace: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: float | None = None,
) -> None:
    loaded = store.load(engine, workspace, "slack")
    if loaded is None:
        raise ApprovalRefused("Slack is not connected for this workspace")
    secret, _ = loaded
    if abs((now or time.time()) - int(timestamp or 0)) > SLACK_MAX_AGE:
        raise ApprovalRefused("Stale Slack request")
    base = f"v0:{timestamp}:{body.decode()}".encode()
    expected = "v0=" + hmac.new(secret["signing_secret"].encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise ApprovalRefused("Bad Slack signature")


def slack_action(engine: Engine, payload: dict) -> str:
    """A verified button press: who pressed it, on which draft, deciding what."""
    action = payload["actions"][0]
    value = json.loads(action["value"])
    with Session(engine) as session:
        link = session.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.workspace == value["w"],
                ChannelIdentity.channel == "slack",
                ChannelIdentity.external_id == payload["user"]["id"],
            )
        ).first()
        user = session.get(User, link.user_id) if link else None
    if user is None:
        raise ApprovalRefused("Your Slack account is not linked to a GrowthCrew user here")
    return _decide(engine, user, value["d"], value["x"], "Slack")


def link_slack_user(engine: Engine, workspace: str, email: str, slack_user_id: str) -> None:
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email.strip().lower())).first()
        if user is None or not user.can_access(workspace):
            raise LookupError(f"No user {email} in this workspace")
        existing = session.exec(
            select(ChannelIdentity).where(
                ChannelIdentity.workspace == workspace, ChannelIdentity.external_id == slack_user_id
            )
        ).first()
        row = existing or ChannelIdentity(
            workspace=workspace, external_id=slack_user_id, user_id=user.id
        )
        row.user_id = user.id
        session.add(row)
        session.commit()
