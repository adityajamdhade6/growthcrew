"""Pre-registration: commit to the question before seeing the answer."""

from growthcrew.experiments.bayes import analyze_rates
from growthcrew.experiments.decide import DecisionConfig, decide
from growthcrew.experiments.guardrails import check_guardrails
from growthcrew.experiments.models import (
    Arm,
    GuardrailData,
    GuardrailSpec,
    Preregistration,
    Verdict,
)
from growthcrew.experiments.power import sample_size


class MetricNotPreregistered(ValueError):
    pass


def preregister(
    experiment: str,
    hypothesis: str,
    primary_metric: str,
    variants: list[str],
    baseline_rate: float,
    minimum_detectable_effect: float,
    guardrails: list[GuardrailSpec] | None = None,
    control: str | None = None,
    planned_per_variant: int | None = None,
) -> Preregistration:
    """Record the plan. The sample size is calculated unless one is given."""
    if len(variants) < 2 or len(set(variants)) != len(variants):
        raise ValueError("Name at least two distinct variants")
    if not hypothesis.strip():
        raise ValueError("A pre-registration needs a hypothesis")
    planned = planned_per_variant or sample_size(
        baseline_rate, minimum_detectable_effect, variants=len(variants)
    )
    return Preregistration(
        experiment=experiment,
        hypothesis=hypothesis,
        primary_metric=primary_metric,
        guardrails=guardrails or [],
        variants=variants,
        control=control or variants[0],
        baseline_rate=baseline_rate,
        minimum_detectable_effect=minimum_detectable_effect,
        planned_per_variant=planned,
    )


def judge(
    registration: Preregistration,
    metric: str,
    arms: list[Arm],
    guardrails: list[GuardrailData] | None = None,
    config: DecisionConfig | None = None,
) -> Verdict:
    """Judge an experiment on its registered primary metric, and nothing else.

    Asking for a verdict on another metric raises: picking the metric after seeing which one
    looks good is the commonest way to find a winner that is not there.
    """
    if metric != registration.primary_metric:
        raise MetricNotPreregistered(
            f"'{registration.experiment}' was registered on '{registration.primary_metric}', "
            f"so it cannot be judged on '{metric}'"
        )
    unknown = [arm.label for arm in arms if arm.label not in registration.variants]
    if unknown:
        raise ValueError(f"Variants not in the registration: {unknown}")
    control = registration.control
    if control not in [arm.label for arm in arms]:
        control = arms[0].label
    posterior = analyze_rates(arms, control=control)
    decision = decide(
        posterior,
        arms,
        config,
        planned_per_variant=registration.planned_per_variant,
        minimum_detectable_effect=registration.minimum_detectable_effect,
    )
    flags: list[str] = []
    if decision.winner:
        registered = {spec.metric for spec in registration.guardrails}
        # Only guardrails named in the registration count; others are ignored, not added.
        relevant = [data for data in guardrails or [] if data.spec.metric in registered]
        flags = check_guardrails(decision.winner, control, relevant)
    return Verdict(
        experiment=registration.experiment,
        metric=metric,
        posterior=posterior,
        decision=decision,
        guardrail_flags=flags,
    )
