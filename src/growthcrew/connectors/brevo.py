"""Brevo: send an approved newsletter to a consented list, and read its results.

Only newsletters are sent, only to a Brevo contact list (people who opted in to it), and only
through `workflow.publish`, which needs an approved draft and a named person. Every email
carries Brevo's unsubscribe link and the business's postal address, as email laws require.
Cold emails to individual prospects are never sent from here: they are exported for a person
to send, one by one, from their own mailbox.
"""

import html
import re

import httpx
import markdown
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.connectors import store
from growthcrew.db.models import CalendarItem, Draft
from growthcrew.db.session import get_engine

API = "https://api.brevo.com/v3"
SENDABLE = {"newsletter"}


def _headers(api_key: str) -> dict:
    return {"api-key": api_key, "accept": "application/json"}


def parse_newsletter(text: str) -> tuple[str, str, str]:
    """(subject, preview, body markdown) from a newsletter draft's text."""
    subject = preview = ""
    body: list[str] = []
    for line in text.splitlines():
        if not subject and line.startswith("Subject:"):
            subject = line.removeprefix("Subject:").strip()
        elif not preview and line.startswith("Preview:"):
            preview = line.removeprefix("Preview:").strip()
        else:
            body.append(line)
    if not subject:
        raise ValueError("The newsletter has no subject line")
    return subject, preview, "\n".join(body).strip()


def render_html(body_md: str, preview: str, postal_address: str, tracking_key: str) -> str:
    content = markdown.markdown(html.escape(body_md, quote=False))
    # Links carry the tracking key, so clicks can be tied back to this piece in GA4.
    content = re.sub(
        r'href="(https?://[^"]+)"',
        lambda m: (
            f'href="{m.group(1)}{"&" if "?" in m.group(1) else "?"}utm_content={tracking_key}"'
        ),
        content,
    )
    hidden = f'<div style="display:none;max-height:0;overflow:hidden">{html.escape(preview)}</div>'
    footer = (
        '<hr><p style="font-size:12px;color:#666">'
        f"{html.escape(postal_address)}<br>"
        'You are receiving this because you subscribed. <a href="{{ unsubscribe }}">Unsubscribe</a>'
        "</p>"
    )
    return f"<html><body>{hidden}{content}{footer}</body></html>"


class BrevoPublisher:
    """Registered in integrations.PUBLISHERS; only ever called from workflow.publish."""

    def __init__(self, engine: Engine | None = None, client: httpx.Client | None = None) -> None:
        self._engine = engine
        self.client = client or httpx.Client(timeout=30.0)

    @property
    def engine(self) -> Engine:
        return self._engine or get_engine()

    def publish(self, draft: Draft, item: CalendarItem) -> None:
        if draft.content_type not in SENDABLE:
            raise ValueError(
                "Only newsletters are sent through Brevo. Cold emails are sent by a person from "
                "their own mailbox."
            )
        loaded = store.load(self.engine, draft.workspace, "brevo")
        if loaded is None:
            raise LookupError("Brevo is not connected for this workspace")
        secret, settings = loaded
        missing = [k for k in ("list_id", "sender_email", "sender_name", "postal_address")
                   if not settings.get(k)]  # fmt: skip
        if missing:
            raise ValueError(f"Set these Brevo settings first: {', '.join(missing)}")
        subject, preview, body = parse_newsletter(draft.text)
        created = self.client.post(
            f"{API}/emailCampaigns",
            headers=_headers(secret["api_key"]),
            json={
                "name": f"GrowthCrew {draft.tracking_key}",
                "subject": subject,
                "previewText": preview,
                "sender": {"name": settings["sender_name"], "email": settings["sender_email"]},
                "htmlContent": render_html(
                    body, preview, settings["postal_address"], draft.tracking_key
                ),  # fmt: skip
                "recipients": {"listIds": [int(settings["list_id"])]},
                "tag": draft.tracking_key,
            },
        )
        created.raise_for_status()
        campaign = created.json()["id"]
        sent = self.client.post(
            f"{API}/emailCampaigns/{campaign}/sendNow", headers=_headers(secret["api_key"])
        )
        sent.raise_for_status()
        item.external_id = f"brevo:{campaign}"


def stats(engine: Engine, workspace: str, client: httpx.Client) -> list[dict]:
    """Lifetime results of every newsletter this workspace sent through Brevo."""
    secret, _ = store.load(engine, workspace, "brevo")
    with Session(engine) as session:
        sent = session.exec(
            select(CalendarItem, Draft)
            .join(Draft, Draft.id == CalendarItem.draft_id)
            .where(
                CalendarItem.workspace == workspace, CalendarItem.external_id.startswith("brevo:")
            )
        ).all()
    rows = []
    for item, draft in sent:
        campaign = item.external_id.removeprefix("brevo:")
        response = client.get(
            f"{API}/emailCampaigns/{campaign}",
            params={"statistics": "globalStats"},
            headers=_headers(secret["api_key"]),
        )
        response.raise_for_status()
        numbers = response.json().get("statistics", {}).get("globalStats", {})
        rows.append(
            {
                "refs": [draft.tracking_key],
                "date": None,  # lifetime totals
                "sends": float(numbers.get("delivered", numbers.get("sent", 0)) or 0),
                "opens": float(numbers.get("uniqueViews", 0) or 0),
                "clicks": float(numbers.get("uniqueClicks", 0) or 0),
                "unsubscribes": float(numbers.get("unsubscriptions", 0) or 0),
            }
        )
    return rows
