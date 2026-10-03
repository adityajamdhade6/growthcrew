"""Significance testing for experiment readouts. Pure functions, no model involved."""

import math
from typing import Literal

from pydantic import BaseModel

ALPHA = 0.05
# An arm with fewer trials than this is never judged.
MIN_TRIALS = 100
# The normal approximation needs at least this many expected successes and failures per arm.
MIN_EXPECTED = 5

Status = Literal["significant", "no_significant_difference", "not_enough_data"]


class Arm(BaseModel):
    label: str
    pieces: int = 1
    trials: int
    successes: int

    @property
    def rate(self) -> float:
        return self.successes / self.trials if self.trials else 0.0


class Comparison(BaseModel):
    status: Status
    winner: str | None = None
    # Adjusted p-value of the winner's weakest pairwise comparison.
    p_value: float | None = None
    # Relative lift of the winner over the runner-up, in percent.
    lift_pct: float | None = None
    note: str = ""


def two_proportion_z(a: Arm, b: Arm) -> tuple[float, float]:
    """Pooled two-proportion z-test. Returns (z, two-sided p)."""
    pooled = (a.successes + b.successes) / (a.trials + b.trials)
    se = math.sqrt(pooled * (1 - pooled) * (1 / a.trials + 1 / b.trials))
    if se == 0:
        return 0.0, 1.0
    z = (a.rate - b.rate) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def compare(arms: list[Arm]) -> Comparison:
    """Is the best arm significantly better than every other arm?

    With more than two arms the p-values are Bonferroni-corrected.
    """
    if len(arms) < 2:
        return Comparison(status="not_enough_data", note="Only one variant has data")
    total_trials = sum(arm.trials for arm in arms)
    pooled = sum(arm.successes for arm in arms) / total_trials if total_trials else 0.0
    for arm in arms:
        expected = min(arm.trials * pooled, arm.trials * (1 - pooled))
        if arm.trials < MIN_TRIALS or expected < MIN_EXPECTED:
            return Comparison(
                status="not_enough_data",
                note=f"'{arm.label}' has {arm.trials} trials and {arm.successes} successes; "
                f"each variant needs at least {MIN_TRIALS} trials and about {MIN_EXPECTED} "
                "expected successes and failures before a result can be called",
            )
    ranked = sorted(arms, key=lambda arm: -arm.rate)
    best, runner_up = ranked[0], ranked[1]
    comparisons = len(arms) - 1
    worst_p = max(min(1.0, two_proportion_z(best, other)[1] * comparisons) for other in ranked[1:])
    lift = (best.rate / runner_up.rate - 1) * 100 if runner_up.rate else None
    if worst_p < ALPHA:
        return Comparison(
            status="significant", winner=best.label, p_value=worst_p,
            lift_pct=round(lift, 1) if lift is not None else None,
        )  # fmt: skip
    return Comparison(
        status="no_significant_difference", p_value=worst_p,
        note=f"'{best.label}' leads but the difference could be chance (p={worst_p:.2f})",
    )  # fmt: skip
