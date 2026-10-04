"""The weekly numbers: performance by dimension, experiment readouts, anomalies.

Everything here is computed in code. The analyst model only interprets these tables.
"""

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.analytics.stats import Arm, Status, Uncertainty, compare, uncertainty
from growthcrew.content.types import ANGLES
from growthcrew.db.models import Draft, PerformanceRow
from growthcrew.naming import display, piece_name
from growthcrew.workflow import CHANNELS

ANOMALY_MIN_DAYS = 8
ANOMALY_Z = 3.5
ANOMALY_CHANGE = 0.5


def primary_metric(source: str, content_type: str | None) -> tuple[str, str, str]:
    """(name, successes field, trials field) for the metric that matters most."""
    if source == "ga4":
        return "conversion rate", "conversions", "sessions"
    if source == "email":
        if content_type == "cold_email_sequence":
            return "reply rate", "replies", "sends"
        return "email click rate", "clicks", "sends"
    return "click-through rate", "clicks", "impressions"


class DimensionRow(BaseModel):
    id: str
    dimension: str
    value: str
    metric: str
    pieces: int
    trials: int
    successes: int
    rate_pct: float


class Readout(BaseModel):
    id: str
    # ab_test: variants of one piece. observational: pooled over different pieces.
    kind: str
    name: str
    # The name as shown to people, e.g. "Day 3 ad" for `07-day03-ad`.
    title: str = ""
    metric: str
    arms: list[dict]
    # Always present, whatever the verdict.
    uncertainty: Uncertainty | None = None
    status: Status
    winner: str | None = None
    p_value: float | None = None
    lift_pct: float | None = None
    note: str = ""


class Anomaly(BaseModel):
    id: str
    series: str
    date: str
    value: float
    typical: float
    change_pct: float
    direction: str


class Analysis(BaseModel):
    window_start: str | None
    window_end: str | None
    rows_used: int
    unmatched_rows: int
    performance: list[DimensionRow]
    readouts: list[Readout]
    anomalies: list[Anomaly]


def _base_id(draft: Draft) -> str:
    return draft.piece_id.removesuffix(f"-{draft.angle}") if draft.angle else draft.piece_id


def _readout(number: int, kind: str, name: str, metric: str, arms: list[Arm], note: str) -> Readout:
    result = compare(arms)
    return Readout(
        id=f"r{number}",
        kind=kind,
        name=name,
        title=piece_name(name) if kind == "ab_test" else display(name),
        metric=metric,
        uncertainty=uncertainty(arms),
        arms=[{**arm.model_dump(), "rate_pct": round(arm.rate * 100, 2)} for arm in arms],
        status=result.status,
        winner=result.winner,
        p_value=float(f"{result.p_value:.2g}") if result.p_value is not None else None,
        lift_pct=result.lift_pct,
        note=" ".join(part for part in (result.note, note) if part),
    )


def find_anomalies(rows: list[PerformanceRow]) -> list[Anomaly]:
    """Days that sit far outside a source's usual level (median and MAD, so robust to spikes)."""
    series: dict[tuple[str, str], dict[datetime, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if row.date is None:
            continue
        _, successes, trials = primary_metric(row.source, None)
        series[(row.source, trials)][row.date] += getattr(row, trials)
        series[(row.source, successes)][row.date] += getattr(row, successes)
    found = []
    for (source, field), days in sorted(series.items()):
        if len(days) < ANOMALY_MIN_DAYS:
            continue
        values = list(days.values())
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        if median == 0 or mad == 0:
            continue
        for day, value in sorted(days.items()):
            change = (value - median) / median
            if abs(0.6745 * (value - median) / mad) > ANOMALY_Z and abs(change) >= ANOMALY_CHANGE:
                found.append(
                    Anomaly(
                        id=f"a{len(found) + 1}",
                        series=f"{source} {field}",
                        date=day.date().isoformat(),
                        value=value,
                        typical=median,
                        change_pct=round(change * 100, 1),
                        direction="spike" if change > 0 else "drop",
                    )
                )
    return found


def analyze(
    engine: Engine, workspace: str, start: datetime | None = None, end: datetime | None = None
) -> Analysis:
    """Analyse one week. Defaults to the 7 days ending at the latest dated row.

    Rows without a date (lifetime totals per item) are always included.
    """
    with Session(engine) as session:
        rows = list(
            session.exec(select(PerformanceRow).where(PerformanceRow.workspace == workspace))
        )
        drafts = {
            draft.id: draft
            for draft in session.exec(select(Draft).where(Draft.workspace == workspace))
        }
    dated = [row.date for row in rows if row.date]
    if dated and end is None:
        end = max(dated) + timedelta(days=1)
    if dated and start is None:
        start = end - timedelta(days=7)
    in_window = [row for row in rows if row.date is None or start <= row.date < end]
    matched = [row for row in in_window if row.draft_id in drafts]

    # Totals per piece and metric.
    totals: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0, 0])
    for row in matched:
        draft = drafts[row.draft_id]
        metric, successes, trials = primary_metric(row.source, draft.content_type)
        totals[(draft.id, metric)][0] += getattr(row, trials)
        totals[(draft.id, metric)][1] += getattr(row, successes)

    def dimensions(draft: Draft) -> dict[str, str]:
        return {
            "pillar": json.loads(draft.metadata_json).get("messaging_pillar", "unknown"),
            "angle": draft.angle or "none",
            "format": draft.content_type,
            "channel": CHANNELS.get(draft.content_type, draft.content_type),
        }

    grouped: dict[tuple[str, str, str], list] = defaultdict(lambda: [set(), 0, 0])
    for (draft_id, metric), (trials, successes) in totals.items():
        for dimension, value in dimensions(drafts[draft_id]).items():
            bucket = grouped[(dimension, value, metric)]
            bucket[0].add(draft_id)
            bucket[1] += trials
            bucket[2] += successes
    performance = [
        DimensionRow(
            id=f"p{number}", dimension=dimension, value=value, metric=metric, pieces=len(pieces),
            trials=int(trials), successes=int(successes),
            rate_pct=round(successes / trials * 100, 2) if trials else 0.0,
        )
        for number, ((dimension, value, metric), (pieces, trials, successes)) in enumerate(
            sorted(grouped.items()), 1
        )
    ]  # fmt: skip

    readouts: list[Readout] = []
    # A/B tests: the variants of one piece.
    groups: dict[tuple[int, str, str], list[Arm]] = defaultdict(list)
    for (draft_id, metric), (trials, successes) in totals.items():
        draft = drafts[draft_id]
        if draft.angle:
            groups[(draft.cycle_id, _base_id(draft), metric)].append(
                Arm(label=draft.angle, trials=int(trials), successes=int(successes))
            )
    for (_, base, metric), arms in sorted(groups.items()):
        if len(arms) >= 2:
            readouts.append(_readout(len(readouts) + 1, "ab_test", base, metric, arms, ""))
    # Angle against angle within one content type, for pieces that were not part of an A/B
    # test. Different pieces differ in topic and timing, so this is observational.
    tested = {key[:2] for key, arms in groups.items() if len(arms) >= 2}
    pooled: dict[tuple[str, str], dict[str, Arm]] = defaultdict(dict)
    for (draft_id, metric), (trials, successes) in totals.items():
        draft = drafts[draft_id]
        if draft.angle and (draft.cycle_id, _base_id(draft)) not in tested:
            arm = pooled[(draft.content_type, metric)].setdefault(
                draft.angle, Arm(label=draft.angle, pieces=0, trials=0, successes=0)
            )
            arm.pieces += 1
            arm.trials += int(trials)
            arm.successes += int(successes)
    for (content_type, metric), by_angle in sorted(pooled.items()):
        arms = [by_angle[angle] for angle in ANGLES if angle in by_angle]
        if len(arms) >= 2:
            readouts.append(
                _readout(
                    len(readouts) + 1,
                    "observational",
                    f"{content_type} by angle",
                    metric,
                    arms,
                    "Pooled across different pieces, so topic and timing are not controlled.",
                )  # fmt: skip
            )

    trailing = [row for row in rows if row.date and end - timedelta(days=28) <= row.date < end]
    return Analysis(
        window_start=start.date().isoformat() if start else None,
        window_end=(end - timedelta(days=1)).date().isoformat() if end else None,
        rows_used=len(matched),
        unmatched_rows=len(in_window) - len(matched),
        performance=performance,
        readouts=readouts,
        anomalies=find_anomalies(trailing),
    )
