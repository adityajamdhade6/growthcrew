"""Content memory: store every measured piece, and retrieve the best ones like a request."""

import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.analytics.analysis import primary_metric
from growthcrew.analytics.registry import RATE_METRICS
from growthcrew.db.models import CalendarItem, Draft, MemoryPiece, PerformanceRow
from growthcrew.memory.embed import Embedder, HashingEmbedder, cosine
from growthcrew.memory.features import extract

# A piece with fewer trials than this has no reliable rate, so it is not remembered yet.
MIN_TRIALS = 200


class Example(BaseModel):
    """A past piece offered to the writer as a model."""

    draft_id: int
    text: str
    angle: str | None
    pillar: str
    metric: str
    rate_pct: float
    # Its rate against the average for this content type: 1.4 is 40% above average.
    score: float
    similarity: float
    published_on: str


def sync(engine: Engine, workspace: str, embedder: Embedder | None = None) -> int:
    """Bring memory up to date with every published piece that has results. Returns how many
    pieces are remembered."""
    embedder = embedder or HashingEmbedder()
    with Session(engine) as session:
        published = session.exec(
            select(Draft, CalendarItem)
            .join(CalendarItem, CalendarItem.draft_id == Draft.id)
            .where(Draft.workspace == workspace, CalendarItem.status.in_(["published", "measured"]))
        ).all()
        rows = session.exec(
            select(PerformanceRow).where(
                PerformanceRow.workspace == workspace, PerformanceRow.draft_id.is_not(None)
            )
        ).all()
        # Looked up once per performance row, so by id, not by scanning the list.
        drafts = {draft.id: draft for draft, _ in published}
        totals: dict[int, dict[str, list[float]]] = {}
        for row in rows:
            draft = drafts.get(row.draft_id)
            if draft is None:
                continue
            metric, _, _ = primary_metric(row.source, draft.content_type)
            successes, trials = RATE_METRICS[metric]
            bucket = totals.setdefault(row.draft_id, {}).setdefault(metric, [0.0, 0.0])
            bucket[0] += getattr(row, trials)
            bucket[1] += getattr(row, successes)

        existing = {
            piece.draft_id: piece
            for piece in session.exec(select(MemoryPiece).where(MemoryPiece.workspace == workspace))
        }
        for draft, item in published:
            metrics = totals.get(draft.id)
            if not metrics:
                continue
            metric, (trials, successes) = max(metrics.items(), key=lambda kv: kv[1][0])
            if trials < MIN_TRIALS:
                continue
            meta = json.loads(draft.metadata_json or "{}")
            piece = existing.get(draft.id) or MemoryPiece(
                workspace=workspace,
                draft_id=draft.id,
                content_type=draft.content_type,
                published_on=item.published_at or item.scheduled_for,
                text=draft.text,
                metric=metric,
                trials=0,
                successes=0,
                rate=0.0,
                features=json.dumps(extract(draft.text)),
                embedding=json.dumps(embedder.embed(draft.text)),
            )
            piece.pillar = meta.get("messaging_pillar", "")
            piece.angle = draft.angle
            piece.persona = meta.get("target_persona", "")
            piece.hypothesis = meta.get("hypothesis", "")
            piece.metric, piece.trials, piece.successes = metric, int(trials), int(successes)
            piece.rate = successes / trials
            piece.prompt_version = draft.prompt_version
            piece.strategy_version = draft.strategy_version
            existing[draft.id] = piece
            session.add(piece)

        # Score each piece against the average for its content type and metric.
        groups: dict[tuple[str, str], list[MemoryPiece]] = {}
        for piece in existing.values():
            groups.setdefault((piece.content_type, piece.metric), []).append(piece)
        for pieces in groups.values():
            average = sum(p.successes for p in pieces) / max(1, sum(p.trials for p in pieces))
            for piece in pieces:
                piece.score = round(piece.rate / average, 3) if average else 1.0
                session.add(piece)
        session.commit()
        return len(existing)


def pieces(
    engine: Engine, workspace: str, content_type: str | None = None, since: datetime | None = None
) -> list[MemoryPiece]:
    query = select(MemoryPiece).where(MemoryPiece.workspace == workspace)
    if content_type:
        query = query.where(MemoryPiece.content_type == content_type)
    if since:
        query = query.where(MemoryPiece.published_on >= since)
    with Session(engine) as session:
        return list(session.exec(query.order_by(MemoryPiece.published_on)))


def best_similar(
    engine: Engine,
    workspace: str,
    content_type: str,
    query: str,
    k: int = 5,
    pool: int = 20,
    embedder: Embedder | None = None,
) -> list[Example]:
    """The `k` best-performing past pieces among the `pool` most similar to the request.

    Similar first, then best: the winners of an unrelated topic would be poor models.
    """
    embedder = embedder or HashingEmbedder()
    target = embedder.embed(query)
    scored = [
        (cosine(target, json.loads(piece.embedding)), piece)
        for piece in pieces(engine, workspace, content_type)
    ]
    nearest = sorted(scored, key=lambda pair: -pair[0])[:pool]
    winners = sorted(nearest, key=lambda pair: -pair[1].score)[:k]
    return [
        Example(
            draft_id=piece.draft_id,
            text=piece.text,
            angle=piece.angle,
            pillar=piece.pillar,
            metric=piece.metric,
            rate_pct=round(piece.rate * 100, 2),
            score=piece.score,
            similarity=round(similarity, 3),
            published_on=piece.published_on.date().isoformat(),
        )
        for similarity, piece in winners
        # Only pieces that beat the average are worth imitating.
        if piece.score > 1.0
    ]
