"""Sample size and power for planning a rate experiment (two-proportion z-test)."""

import math
from statistics import NormalDist

_normal = NormalDist()


def sample_size(
    baseline_rate: float,
    minimum_detectable_effect: float,
    variants: int = 2,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:
    """Trials needed per variant to detect a relative lift of `minimum_detectable_effect`.

    `minimum_detectable_effect` is relative: 0.2 means a lift from 5.0% to 6.0%. With more
    than two variants, alpha is split across the comparisons against the control.
    """
    if not 0 < baseline_rate < 1:
        raise ValueError("baseline_rate must be between 0 and 1")
    if minimum_detectable_effect <= 0:
        raise ValueError("minimum_detectable_effect must be positive")
    if variants < 2:
        raise ValueError("An experiment needs at least two variants")
    lifted = baseline_rate * (1 + minimum_detectable_effect)
    if lifted >= 1:
        raise ValueError("The lifted rate would be 100% or more")
    z_alpha = _normal.inv_cdf(1 - alpha / (2 * (variants - 1)))
    z_power = _normal.inv_cdf(power)
    variance = baseline_rate * (1 - baseline_rate) + lifted * (1 - lifted)
    return math.ceil((z_alpha + z_power) ** 2 * variance / (lifted - baseline_rate) ** 2)


def power(
    baseline_rate: float,
    minimum_detectable_effect: float,
    per_variant: int,
    variants: int = 2,
    alpha: float = 0.05,
) -> float:
    """The chance of detecting the effect with `per_variant` trials in each variant."""
    lifted = baseline_rate * (1 + minimum_detectable_effect)
    z_alpha = _normal.inv_cdf(1 - alpha / (2 * (variants - 1)))
    variance = baseline_rate * (1 - baseline_rate) + lifted * (1 - lifted)
    z = abs(lifted - baseline_rate) / math.sqrt(variance / per_variant) - z_alpha
    return _normal.cdf(z)
