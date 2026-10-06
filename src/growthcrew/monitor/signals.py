"""The Signals inbox: store findings once, rank them, and learn from what people dismiss.

Ranking is computed in code: a finding's base importance (set by the monitor from what it
found, for example a price change outranks a copy tweak), times a weight learned per category
from people's choices, times a decay for age. Each "send to strategist" or "dismiss" moves the
weight for that kind of finding, so a category the owner keeps dismissing sinks.
"""

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.db.models import Signal
from growthcrew.memory.embed import HashingEmbedder, cosine

# Findings this alike (lexical similarity of title and summary) are treated as the same one.
DUPLICATE_SIMILARITY = 0.9
# How far back duplicates are looked for.
DEDUPE_DAYS = 90
# A finding loses half its rank every this many days.
HALF_LIFE_DAYS = 14


class Source(BaseModel):
    url: str
    date: str


class Finding(BaseModel):
    """What a monitor hands to the inbox."""

    monitor: str
    category: str
    title: str
    summary: str
    suggested_response: str = ""
    sources: list[Source]
    base_importance: float = 0.5
    warning: str = ""


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def fingerprint(finding: Finding) -> str:
    urls = ",".join(sorted(source.url for source in finding.sources))
    key = f"{finding.category}|{_normal(finding.title)}|{urls}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _embed(finding: Finding) -> list[float]:
    return HashingEmbedder().embed(f"{finding.title}\n{finding.summary}")


def known_texts(workspace: str, root: Path) -> list[str]:
    """Claims already in the latest research report: a finding that repeats one is not new."""
    reports = sorted((root / workspace / "research").glob("*.json"))
    if not reports:
        return []
    from growthcrew.agents.research_models import ResearchReport

    report = ResearchReport.model_validate_json(reports[-1].read_text())
    return [claim.statement for claim in report.claims()]


def file_findings(
    engine: Engine, workspace: str, findings: list[Finding], known: list[str] | None = None
) -> tuple[list[Signal], int]:
    """Store new findings. Returns the stored signals and how many were duplicates."""
    if not findings:
        return [], 0
    embedder = HashingEmbedder()
    known_vectors = [embedder.embed(text) for text in known or []]
    now = datetime.now(UTC)
    stored, duplicates = [], 0
    with Session(engine, expire_on_commit=False) as session:
        recent = [
            signal
            for signal in session.exec(select(Signal).where(Signal.workspace == workspace))
            if (now - _aware(signal.created_at)).days <= DEDUPE_DAYS
        ]
        prints = {signal.fingerprint for signal in recent}
        vectors = [json.loads(signal.embedding) for signal in recent]
        for finding in findings:
            if not finding.sources:
                # Every finding must cite where it came from; one that cannot is dropped.
                continue
            mark = fingerprint(finding)
            vector = _embed(finding)
            seen = mark in prints or any(
                cosine(vector, other) >= DUPLICATE_SIMILARITY for other in vectors + known_vectors
            )
            if seen:
                duplicates += 1
                continue
            signal = Signal(
                workspace=workspace,
                monitor=finding.monitor,
                category=finding.category,
                title=finding.title,
                summary=finding.summary,
                suggested_response=finding.suggested_response,
                sources=json.dumps([source.model_dump() for source in finding.sources]),
                base_importance=min(1.0, max(0.0, finding.base_importance)),
                warning=finding.warning,
                fingerprint=mark,
                embedding=json.dumps([round(value, 5) for value in vector]),
            )
            session.add(signal)
            stored.append(signal)
            prints.add(mark)
            vectors.append(vector)
        session.commit()
    return stored, duplicates


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def category_weights(engine: Engine, workspace: str) -> dict[str, float]:
    """A weight per category from people's choices: 1.0 with no history, lower when dismissed.

    The weight is the Beta(1, 1) posterior mean of "sent rather than dismissed", times two, so
    an even record leaves a category at 1.0, and it can fall towards zero but never reaches it.
    """
    counts: dict[str, list[int]] = {}
    with Session(engine) as session:
        for signal in session.exec(
            select(Signal).where(
                Signal.workspace == workspace, Signal.status.in_(["sent", "dismissed"])
            )
        ):
            sent, dismissed = counts.setdefault(signal.category, [0, 0])
            counts[signal.category] = [sent + (signal.status == "sent"),
                                       dismissed + (signal.status == "dismissed")]  # fmt: skip
    return {
        category: 2 * (sent + 1) / (sent + dismissed + 2)
        for category, (sent, dismissed) in counts.items()
    }


def score(signal: Signal, weights: dict[str, float], now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    age = max(0.0, (now - _aware(signal.created_at)).total_seconds() / 86400)
    decay = math.pow(0.5, age / HALF_LIFE_DAYS)
    return signal.base_importance * weights.get(signal.category, 1.0) * decay


def _as_dict(signal: Signal, rank: float) -> dict:
    return {
        "id": signal.id,
        "created_at": signal.created_at.isoformat(),
        "monitor": signal.monitor,
        "category": signal.category,
        "title": signal.title,
        "summary": signal.summary,
        "suggested_response": signal.suggested_response,
        "sources": json.loads(signal.sources),
        "warning": signal.warning,
        "status": signal.status,
        "decided_by": signal.decided_by,
        "score": round(rank, 3),
    }


def inbox(engine: Engine, workspace: str, status: str = "new", limit: int = 100) -> list[dict]:
    """Signals with this status, most important first."""
    weights = category_weights(engine, workspace)
    with Session(engine) as session:
        signals = session.exec(
            select(Signal).where(Signal.workspace == workspace, Signal.status == status)
        ).all()
    ranked = sorted(((score(s, weights), s) for s in signals), key=lambda pair: -pair[0])
    return [_as_dict(signal, rank) for rank, signal in ranked[:limit]]


def decide(engine: Engine, workspace: str, signal_id: int, action: str, person: str) -> dict:
    """Send a signal to the strategist, or dismiss it. Recorded against the signed-in person."""
    if action not in ("send", "dismiss"):
        raise ValueError("The action must be 'send' or 'dismiss'")
    with Session(engine, expire_on_commit=False) as session:
        signal = session.get(Signal, signal_id)
        if signal is None or signal.workspace != workspace:
            raise LookupError(f"Signal {signal_id} not found")
        if signal.status != "new":
            raise ValueError(f"This signal was already {signal.status}")
        signal.status = "sent" if action == "send" else "dismissed"
        signal.decided_by = person
        signal.decided_at = datetime.now(UTC)
        session.add(signal)
        session.commit()
    return _as_dict(signal, 0.0)


def for_strategist(engine: Engine, workspace: str, limit: int = 20) -> list[Signal]:
    """Signals a person sent to the strategist, newest first."""
    with Session(engine) as session:
        return list(
            session.exec(
                select(Signal)
                .where(Signal.workspace == workspace, Signal.status == "sent")
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        )


def digest(engine: Engine, workspace: str, limit: int = 10) -> str:
    """This week's digest as Markdown: the top new signals, each with its sources and dates."""
    top = inbox(engine, workspace, "new", limit)
    lines = [f"# Signals digest: {workspace}", "", f"{datetime.now(UTC):%d %B %Y}", ""]
    if not top:
        lines.append("Nothing new this week.")
        return "\n".join(lines)
    for number, item in enumerate(top, 1):
        lines.append(f"## {number}. {item['title']}")
        lines.append(item["summary"])
        if item["suggested_response"]:
            lines.append(f"\nSuggested response: {item['suggested_response']}")
        if item["warning"]:
            lines.append(f"\nWarning: {item['warning']}")
        lines.append("")
        lines += [f"- <{source['url']}> ({source['date']})" for source in item["sources"]]
        lines.append("")
    return "\n".join(lines)
