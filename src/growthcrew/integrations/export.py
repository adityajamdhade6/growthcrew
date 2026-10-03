"""Exports: the content calendar as CSV and email drafts as .eml files."""

import csv
import io
import json
from email.message import EmailMessage

from growthcrew.db.models import CalendarItem, Draft

CSV_COLUMNS = (
    "date", "channel", "content_type", "angle", "status", "pillar", "cta", "tracking_key", "text",
)  # fmt: skip


def calendar_csv(rows: list[tuple[CalendarItem, Draft]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for item, draft in sorted(rows, key=lambda row: row[0].scheduled_for):
        meta = json.loads(draft.metadata_json)
        writer.writerow(
            [
                item.scheduled_for.date().isoformat(),
                item.channel,
                draft.content_type,
                draft.angle or "",
                item.status,
                meta.get("messaging_pillar", ""),
                meta.get("cta", ""),
                draft.tracking_key,
                draft.text,
            ]
        )
    return out.getvalue()


def email_draft(draft: Draft, step: int = 1) -> str:
    """An unsent .eml a mail client opens as a draft. Only for email content types."""
    body = json.loads(draft.body_json)
    if draft.content_type == "newsletter":
        subject, text = body["subject"], body["body_markdown"]
    elif draft.content_type == "cold_email_sequence":
        emails = body["emails"]
        if not 1 <= step <= len(emails):
            raise ValueError(f"This sequence has {len(emails)} emails; step {step} does not exist")
        subject, text = emails[step - 1]["subject"], emails[step - 1]["body"]
    else:
        raise ValueError(f"{draft.content_type} is not an email content type")
    message = EmailMessage()
    message["Subject"] = subject
    message["X-Unsent"] = "1"
    message.set_content(text)
    return message.as_string()
