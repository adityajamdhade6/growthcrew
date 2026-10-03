"""Read analytics exports (CSV) and tie each row to a content piece.

Header names differ between tools and change over time, so each field has a list of aliases.
Add an alias here when an export uses a header that is not recognised.
"""

import csv
import io
import re
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.brain.voice import squash
from growthcrew.db.models import CalendarItem, Draft, PerformanceRow

SOURCES = ("linkedin", "gsc", "ga4", "email", "ads")

ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "day", "created date", "send date", "sent date", "reporting starts"),
    "impressions": ("impressions", "impr.", "impr", "views"),
    "clicks": ("clicks", "link clicks", "unique clicks", "clicks (all)"),
    "engagements": ("engagements", "reactions", "likes", "total engagements"),
    "sessions": ("sessions", "users", "total users", "active users"),
    "conversions": ("conversions", "key events", "results", "purchases", "leads"),
    "sends": ("sent", "sends", "recipients", "delivered", "emails sent"),
    "opens": ("opens", "unique opens", "opened"),
    "replies": ("replies", "replied", "responses"),
    "spend": ("spend", "cost", "amount spent", "amount spent (usd)"),
}
# Columns that identify the item, in the order they are tried.
REF_ALIASES = (
    "piece_id", "tracking_key", "utm_content", "session manual ad content", "ad name", "ad",
    "campaign", "campaign name", "subject", "post link", "post url", "url", "page", "top pages",
    "landing page", "page path and screen class", "post title", "post text", "title",
)  # fmt: skip
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%d %b %Y", "%Y%m%d")


class IngestResult(BaseModel):
    source: str
    rows: int
    matched: int
    unmatched_refs: list[str]


def _number(value: str) -> float:
    cleaned = re.sub(r"[^\d.\-]", "", value or "")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _date(value: str) -> datetime | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_csv(text: str) -> list[dict]:
    """Normalise an export into dicts with `refs`, `date` and the numeric fields."""
    rows = []
    for raw in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        refs = [row[name] for name in REF_ALIASES if row.get(name)]
        if not refs:
            continue
        parsed: dict = {"refs": refs, "date": None}
        for field, aliases in ALIASES.items():
            value = next((row[name] for name in aliases if row.get(name)), "")
            parsed[field] = _date(value) if field == "date" else _number(value)
        rows.append(parsed)
    return rows


def _url_key(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.split("?")[0].split("#")[0]).rstrip("/").lower()


def match_draft(refs: list[str], drafts: list[tuple[Draft, str | None]]) -> int | None:
    """Find the piece a row belongs to: by tracking key, then published URL, then its text."""
    lowered = [ref.lower() for ref in refs]
    for draft, _ in drafts:
        if any(draft.tracking_key.lower() in ref for ref in lowered):
            return draft.id
    for draft, url in drafts:
        if url and any(_url_key(url) == _url_key(ref) for ref in refs):
            return draft.id
    for draft, _ in drafts:
        opening = squash(draft.text)[:50]
        if len(opening) >= 20 and any(squash(ref).startswith(opening) for ref in refs):
            return draft.id
    return None


def ingest(engine: Engine, workspace: str, source: str, text: str) -> IngestResult:
    """Store an export. A row with the same source, item and date replaces the earlier one."""
    if source not in SOURCES:
        raise ValueError(f"Unknown source '{source}'. Use one of: {', '.join(SOURCES)}")
    parsed = parse_csv(text)
    unmatched: list[str] = []
    with Session(engine) as session:
        drafts = [
            (draft, session.exec(
                select(CalendarItem.published_url).where(CalendarItem.draft_id == draft.id)
            ).first())
            for draft in session.exec(
                select(Draft).where(Draft.workspace == workspace, Draft.status == "approved")
            )
        ]  # fmt: skip
        for row in parsed:
            refs = row.pop("refs")
            draft_id = match_draft(refs, drafts)
            if draft_id is None:
                unmatched.append(refs[0])
            existing = session.exec(
                select(PerformanceRow).where(
                    PerformanceRow.workspace == workspace,
                    PerformanceRow.source == source,
                    PerformanceRow.ref == refs[0],
                    PerformanceRow.date == row["date"],
                )
            ).first()
            if existing:
                session.delete(existing)
            session.add(
                PerformanceRow(
                    workspace=workspace, source=source, ref=refs[0], draft_id=draft_id, **row
                )
            )
        session.commit()
    return IngestResult(
        source=source,
        rows=len(parsed),
        matched=len(parsed) - len(unmatched),
        unmatched_refs=sorted(set(unmatched))[:20],
    )
