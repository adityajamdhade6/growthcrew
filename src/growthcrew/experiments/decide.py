"""The decision rule: call a winner, keep running, or say the variants cannot be separated."""

from dataclasses import dataclass

from growthcrew.experiments.models import Arm, Decision, Posterior
from growthcrew.experiments.power import sample_size

# Variants with fewer trials than this are never judged, whatever was planned.
MIN_TRIALS = 100


@dataclass(frozen=True)
class DecisionConfig:
    # Call a winner when its expected loss, relative to its own rate, is below this.
    # 0.01 means: choosing it is expected to cost under 1% of the rate.
    loss_threshold: float = 0.01
    # The winner must also be this likely to be the best. None picks 1 - 0.04 / variants,
    # which keeps the rate of false winners between identical variants near 4%.
    min_prob_best: float | None = None
    min_trials: int = MIN_TRIALS

    def prob_needed(self, variants: int) -> float:
        return self.min_prob_best if self.min_prob_best is not None else 1 - 0.04 / variants


def _chance(probability: float) -> str:
    """A probability as text that never claims certainty."""
    return "over 99.9%" if probability > 0.999 else f"{probability:.1%}"


def more_trials_needed(
    posterior: Posterior, arms: list[Arm], planned: int | None, mde: float
) -> int:
    """Roughly how many more trials, in total, before a call is likely.

    Uses the larger of the observed lift and the minimum detectable effect, so a small or
    noisy early lead does not produce an absurd figure.
    """
    base = max(posterior.mean[posterior.runner_up], 1e-4)
    effect = max(posterior.leader_lift.mean / 100, mde, 0.01)
    try:
        needed = sample_size(min(base, 0.99), effect, variants=len(arms))
    except ValueError:
        needed = planned or MIN_TRIALS
    target = max(needed, planned or 0, MIN_TRIALS)
    return sum(max(0, target - arm.trials) for arm in arms)


def decide(
    posterior: Posterior,
    arms: list[Arm],
    config: DecisionConfig | None = None,
    planned_per_variant: int | None = None,
    minimum_detectable_effect: float = 0.2,
) -> Decision:
    """Apply the rule to a rate experiment.

    If a sample size was planned, nothing is called before every variant reaches it: looking
    early and stopping on a lucky streak is how false winners are made.
    """
    config = config or DecisionConfig()
    floor = max(config.min_trials, planned_per_variant or 0)
    short = [arm for arm in arms if arm.trials < floor]
    if short:
        smallest = min(short, key=lambda arm: arm.trials)
        more = more_trials_needed(posterior, arms, planned_per_variant, minimum_detectable_effect)
        return Decision(
            status="keep_running",
            more_needed=more,
            reason=f"'{smallest.label}' has {smallest.trials:,} of the {floor:,} trials each "
            "variant needs before a call is made",
        )

    best = min(posterior.expected_loss, key=posterior.expected_loss.get)
    rate = posterior.mean[best]
    relative_loss = posterior.expected_loss[best] / rate if rate else 1.0
    needed = config.prob_needed(len(arms))
    if relative_loss < config.loss_threshold and posterior.prob_best[best] >= needed:
        return Decision(
            status="winner",
            winner=best,
            reason=f"'{best}' is {_chance(posterior.prob_best[best])} likely to be best, and "
            f"choosing it is expected to cost {relative_loss:.2%} of the rate if that is wrong",
        )
    if planned_per_variant:
        return Decision(
            status="no_clear_difference",
            reason="The planned sample is in and no variant is clearly best "
            f"('{best}' is {posterior.prob_best[best]:.0%} likely). Treat them as equivalent, "
            "or register a new test with a larger sample",
        )
    more = more_trials_needed(posterior, arms, None, minimum_detectable_effect)
    return Decision(
        status="keep_running",
        more_needed=more,
        reason=f"'{best}' leads but is only {posterior.prob_best[best]:.0%} likely to be best",
    )
