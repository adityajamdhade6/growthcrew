"""The weekly numbers: performance by dimension, experiment readouts, anomalies.

Everything here is computed in code. The analyst model only interprets these tables.
"""

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.analytics import registry
from growthcrew.analytics.registry import RATE_METRICS
from growthcrew.content.types import ANGLES
from growthcrew.db.models import Draft, PerformanceRow
from growthcrew.experiments import (
    Arm,
    GuardrailData,
    Posterior,
    Preregistration,
    allocate,
    analyze_rates,
    judge,
)
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


# `significant` is a called winner, `no_significant_difference` the planned sample without one,
# `not_enough_data` a test still running, and `not_preregistered` a comparison that was never
# registered and so is descriptive only.
Status = Literal["significant", "no_significant_difference", "not_enough_data", "not_preregistered"]
_STATUS = {
    "winner": "significant",
    "no_clear_difference": "no_significant_difference",
    "keep_running": "not_enough_data",
}
# Metric name -> (numerator field, denominator field), compared as plain values.
VALUE_METRICS = {"cost per click": ("spend", "clicks")}
FIELDS = ("impressions", "clicks", "engagements", "sessions", "conversions", "sends", "opens",
          "replies", "unsubscribes", "spend")  # fmt: skip


class DimensionRow(BaseModel):
    id: str
    dimension: str
    value: str
    metric: str
    pieces: int
    trials: int
    successes: int
    rate_pct: float


class Uncertainty(BaseModel):
    """Shown with every readout, so a verdict is never a bare label."""

    leader: str
    prob_best: dict[str, float]
    # Expected loss of choosing each variant, as a percentage of the leader's rate.
    expected_loss_pct: dict[str, float]
    # 95% credible interval on the leader's relative lift over the runner-up, in percent.
    lift_low_pct: float
    lift_high_pct: float
    sample_size: int


class Readout(BaseModel):
    id: str
    # ab_test: variants of one piece. observational: pooled over different pieces.
    kind: str
    name: str
    # The name as shown to people, e.g. "Day 3 ad" for `07-day03-ad`.
    title: str = ""
    metric: str
    arms: list[dict]
    status: Status
    winner: str | None = None
    # The leader's relative lift over the runner-up, in percent.
    lift_pct: float | None = None
    uncertainty: Uncertainty
    note: str = ""
    # What was committed to before the test started. Empty when it was not registered.
    preregistered: bool = False
    hypothesis: str = ""
    planned_per_variant: int | None = None
    # For a test still running: roughly how many more trials are needed in total.
    more_needed: int = 0
    guardrails_checked: list[str] = []
    # Guardrails the winner damages. A flagged winner is held for a person to decide.
    guardrail_flags: list[str] = []
    # For ad tests: how to split next week's budget across the variants (Thompson sampling).
    next_split: dict[str, float] | None = None


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


def _uncertainty(posterior: Posterior) -> Uncertainty:
    top = posterior.mean[posterior.leader] or 1.0
    return Uncertainty(
        leader=posterior.leader,
        prob_best={label: round(value, 4) for label, value in posterior.prob_best.items()},
        expected_loss_pct={
            label: round(value / top * 100, 2) for label, value in posterior.expected_loss.items()
        },
        lift_low_pct=round(posterior.leader_lift.low, 1),
        lift_high_pct=round(posterior.leader_lift.high, 1),
        sample_size=posterior.sample_size,
    )


def _arm_rows(arms: list[Arm]) -> list[dict]:
    return [{**arm.model_dump(), "rate_pct": round(arm.rate * 100, 2)} for arm in arms]


def build_readout(
    number: int,
    kind: str,
    name: str,
    metric: str,
    arms: list[Arm],
    registration: Preregistration | None = None,
    guardrails: list[GuardrailData] | None = None,
    bandit: bool = False,
) -> Readout:
    """One experiment readout. Only a pre-registered test can have a winner."""
    title = piece_name(name) if kind == "ab_test" else display(name)
    if registration is None:
        posterior = analyze_rates(arms)
        why = (
            "Pooled across different pieces, so topic and timing are not controlled."
            if kind == "observational"
            else "These variants were not registered as a test before they ran."
        )
        return Readout(
            id=f"r{number}", kind=kind, name=name, title=title, metric=metric,
            arms=_arm_rows(arms), status="not_preregistered",
            lift_pct=round(posterior.leader_lift.mean, 1), uncertainty=_uncertainty(posterior),
            note=f"{why} Descriptive only: no winner is called without a pre-registered test.",
        )  # fmt: skip
    verdict = judge(registration, metric, arms, guardrails)
    posterior, decision = verdict.posterior, verdict.decision
    return Readout(
        id=f"r{number}", kind=kind, name=name, title=title, metric=metric,
        arms=_arm_rows(arms), status=_STATUS[decision.status], winner=decision.winner,
        lift_pct=round(posterior.leader_lift.mean, 1), uncertainty=_uncertainty(posterior),
        note=decision.reason, preregistered=True, hypothesis=registration.hypothesis,
        planned_per_variant=registration.planned_per_variant, more_needed=decision.more_needed,
        guardrails_checked=[spec.metric for spec in registration.guardrails],
        guardrail_flags=verdict.guardrail_flags,
        next_split=allocate(arms, decided_winner=verdict.clean_winner) if bandit else None,
    )  # fmt: skip


def _guardrail_data(
    registration: Preregistration, sums: dict[str, dict[str, float]]
) -> list[GuardrailData]:
    """Observed values for each registered guardrail, per variant label."""
    out = []
    for spec in registration.guardrails:
        if spec.metric in RATE_METRICS:
            successes, trials = RATE_METRICS[spec.metric]
            arms = [
                Arm(label=label, trials=int(total[trials]), successes=int(total[successes]))
                for label, total in sums.items()
                if total[trials] > 0
            ]
            if len(arms) >= 2:
                out.append(GuardrailData(spec=spec, arms=arms))
        elif spec.metric in VALUE_METRICS:
            top, bottom = VALUE_METRICS[spec.metric]
            values = {
                label: total[top] / total[bottom] for label, total in sums.items() if total[bottom]
            }
            if len(values) >= 2:
                out.append(GuardrailData(spec=spec, values=values))
    return out


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

    # Every field summed per piece, for judging registered tests and their guardrails.
    sums: dict[int, dict[str, float]] = defaultdict(lambda: dict.fromkeys(FIELDS, 0.0))
    for row in matched:
        for field in FIELDS:
            sums[row.draft_id][field] += getattr(row, field)

    readouts: list[Readout] = []
    # A/B tests: the variants of one piece, judged against their pre-registration.
    groups: dict[tuple[int, str], list[Draft]] = defaultdict(list)
    for draft_id in sums:
        draft = drafts[draft_id]
        if draft.angle:
            groups[(draft.cycle_id, _base_id(draft))].append(draft)
    tested = {key for key, members in groups.items() if len(members) >= 2}
    for cycle_id, base in sorted(tested):
        members = sorted(groups[(cycle_id, base)], key=lambda d: ANGLES.index(d.angle))
        registration = registry.find(engine, workspace, cycle_id, base)
        if registration and registration.primary_metric in RATE_METRICS:
            metric = registration.primary_metric
        else:
            registration = None
            metric = next(m for (draft_id, m) in totals if draft_id == members[0].id)
        successes, trials = RATE_METRICS[metric]
        arms = [
            Arm(label=d.angle, trials=int(sums[d.id][trials]), successes=int(sums[d.id][successes]))
            for d in members
            if sums[d.id][trials] > 0
        ]
        if len(arms) < 2:
            continue
        by_label = {d.angle: sums[d.id] for d in members}
        number = len(readouts) + 1
        final = registry.final_verdict(engine, workspace, cycle_id, base) if registration else None
        if final:
            # The test was already judged at its planned sample. Looking again as more data
            # arrives, and changing the answer, is how false winners get made.
            stored = Readout.model_validate_json(final)
            readouts.append(stored.model_copy(update={"id": f"r{number}"}))
            continue
        readout = build_readout(
            number, "ab_test", base, metric, arms, registration,
            _guardrail_data(registration, by_label) if registration else None,
            bandit=members[0].content_type == "ad",
        )  # fmt: skip
        if registration and readout.status != "not_enough_data":
            registry.record_verdict(engine, workspace, cycle_id, base, readout.model_dump_json())
        readouts.append(readout)
    # Angle against angle within one content type, for pieces that were not part of an A/B
    # test. Never registered, so always descriptive.
    pooled: dict[tuple[str, str], dict[str, Arm]] = defaultdict(dict)
    for (draft_id, metric), (trials, successes) in totals.items():
        draft = drafts[draft_id]
        if draft.angle and (draft.cycle_id, _base_id(draft)) not in tested:
            arm = pooled[(draft.content_type, metric)].setdefault(
                draft.angle, Arm(label=draft.angle, trials=0, successes=0)
            )
            arm.trials += int(trials)
            arm.successes += int(successes)
    for (content_type, metric), by_angle in sorted(pooled.items()):
        arms = [by_angle[angle] for angle in ANGLES if angle in by_angle]
        if len(arms) >= 2:
            readouts.append(
                build_readout(
                    len(readouts) + 1, "observational", f"{content_type} by angle", metric, arms
                )
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
