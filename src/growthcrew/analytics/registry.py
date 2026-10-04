"""Where pre-registrations are stored, and how A/B tests get one when they are drafted."""

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew import config
from growthcrew.db.models import Draft, ExperimentRegistration, PerformanceRow
from growthcrew.experiments import GuardrailSpec, Preregistration, preregister

# Metric name -> (successes field, trials field) on a PerformanceRow.
RATE_METRICS = {
    "click-through rate": ("clicks", "impressions"),
    "conversion rate": ("conversions", "sessions"),
    "reply rate": ("replies", "sends"),
    "email click rate": ("clicks", "sends"),
    "unsubscribe rate": ("unsubscribes", "sends"),
}
# A workspace's own history replaces the planning assumption once it has this many trials.
MIN_HISTORY = 1000


def _row(session: Session, workspace: str, cycle_id: int, experiment: str):
    return session.exec(
        select(ExperimentRegistration).where(
            ExperimentRegistration.workspace == workspace,
            ExperimentRegistration.cycle_id == cycle_id,
            ExperimentRegistration.experiment == experiment,
        )
    ).first()


def final_verdict(engine: Engine, workspace: str, cycle_id: int, experiment: str) -> str | None:
    with Session(engine) as session:
        row = _row(session, workspace, cycle_id, experiment)
        return row.verdict if row else None


def record_verdict(
    engine: Engine, workspace: str, cycle_id: int, experiment: str, verdict: str
) -> None:
    """Keep the first judgement made at the planned sample. Later ones do not replace it."""
    with Session(engine) as session:
        row = _row(session, workspace, cycle_id, experiment)
        if row and row.verdict is None:
            row.verdict = verdict
            session.add(row)
            session.commit()


def observed_rate(engine: Engine, workspace: str, metric: str, content_type: str) -> float | None:
    """The workspace's own rate on a metric for one content type, if it has enough history.

    Kept to one content type because rates differ widely between them: an ad's click-through
    rate says little about a LinkedIn post's.
    """
    successes, trials = RATE_METRICS[metric]
    with Session(engine) as session:
        won, total = session.exec(
            select(
                func.sum(getattr(PerformanceRow, successes)),
                func.sum(getattr(PerformanceRow, trials)),
            )
            .join(Draft, Draft.id == PerformanceRow.draft_id)
            .where(PerformanceRow.workspace == workspace, Draft.content_type == content_type)
        ).one()
    if not total or total < MIN_HISTORY or not won:
        return None
    return won / total


def register(
    engine: Engine, workspace: str, cycle_id: int, registration: Preregistration
) -> Preregistration:
    """Store a registration. A test that already has one keeps it: registrations are not
    rewritten once made."""
    existing = find(engine, workspace, cycle_id, registration.experiment)
    if existing:
        return existing
    with Session(engine) as session:
        session.add(
            ExperimentRegistration(
                workspace=workspace,
                cycle_id=cycle_id,
                experiment=registration.experiment,
                data=registration.model_dump_json(),
            )
        )
        session.commit()
    return registration


def find(engine: Engine, workspace: str, cycle_id: int, experiment: str) -> Preregistration | None:
    with Session(engine) as session:
        row = _row(session, workspace, cycle_id, experiment)
    return Preregistration.model_validate_json(row.data) if row else None


def register_variants(
    engine: Engine,
    workspace: str,
    cycle_id: int,
    experiment: str,
    content_type: str,
    variants: list[str],
    hypothesis: str,
) -> Preregistration | None:
    """Pre-register an A/B test from the defaults for its content type, as it is drafted."""
    if content_type not in config.EXPERIMENT_DEFAULTS or len(variants) < 2:
        return None
    metric, assumed, guardrails = config.EXPERIMENT_DEFAULTS[content_type]
    # Plan from the workspace's own history when there is enough of it; the configured rate
    # is only an assumption for a brand with no data yet.
    baseline = observed_rate(engine, workspace, metric, content_type) or assumed
    return register(
        engine,
        workspace,
        cycle_id,
        preregister(
            experiment=experiment,
            hypothesis=hypothesis or f"One angle will beat the others on {metric}",
            primary_metric=metric,
            variants=variants,
            baseline_rate=baseline,
            minimum_detectable_effect=config.EXPERIMENT_MDE,
            guardrails=[GuardrailSpec(metric=name) for name in guardrails],
        ),
    )
