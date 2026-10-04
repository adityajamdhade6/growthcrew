"""Posterior inference for A/B/n tests, by Monte Carlo with a fixed seed.

Rates use a Beta-Binomial model with a uniform prior. Revenue per visitor is modelled as
conversion rate (Beta) times mean order value (Bayesian bootstrap of the observed orders),
which makes no assumption about the shape of the order-value distribution.
"""

import numpy as np

from growthcrew.experiments.models import Arm, Interval, Posterior, RevenueArm

DRAWS = 20_000


def _summarise(
    labels: list[str], samples: np.ndarray, control: str | None, sample_size: int
) -> Posterior:
    """`samples` has one row per draw and one column per variant."""
    control = control or labels[0]
    means = samples.mean(axis=0)
    best = samples.argmax(axis=1)
    loss = (samples.max(axis=1, keepdims=True) - samples).mean(axis=0)
    order = np.argsort(-means)
    leader, runner_up = int(order[0]), int(order[1])

    def lift(over: int, base: int) -> Interval:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = samples[:, over] / samples[:, base] - 1
        ratio = ratio[np.isfinite(ratio)] * 100
        if ratio.size == 0:
            return Interval(mean=0.0, low=0.0, high=0.0)
        low, high = np.percentile(ratio, [2.5, 97.5])
        # The median is reported as the central estimate: the mean of a ratio is pulled
        # around by a few extreme draws when samples are small.
        return Interval(mean=float(np.median(ratio)), low=float(low), high=float(high))

    index = labels.index(control)
    return Posterior(
        labels=labels,
        mean={label: float(means[i]) for i, label in enumerate(labels)},
        prob_best={label: float((best == i).mean()) for i, label in enumerate(labels)},
        expected_loss={label: float(loss[i]) for i, label in enumerate(labels)},
        lift_vs_control={label: lift(i, index) for i, label in enumerate(labels) if i != index},
        control=control,
        leader=labels[leader],
        runner_up=labels[runner_up],
        leader_lift=lift(leader, runner_up),
        sample_size=sample_size,
    )


def rate_samples(arms: list[Arm], draws: int = DRAWS, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    successes = np.array([arm.successes for arm in arms])
    failures = np.array([arm.trials - arm.successes for arm in arms])
    return rng.beta(1 + successes, 1 + failures, size=(draws, len(arms)))


def analyze_rates(
    arms: list[Arm], control: str | None = None, draws: int = DRAWS, seed: int = 0
) -> Posterior:
    """Beta-Binomial analysis of a rate metric across two or more variants."""
    if len(arms) < 2:
        raise ValueError("An experiment needs at least two variants")
    for arm in arms:
        if arm.successes > arm.trials or arm.trials < 0 or arm.successes < 0:
            raise ValueError(f"{arm.label}: successes must be between 0 and trials")
    labels = [arm.label for arm in arms]
    samples = rate_samples(arms, draws, seed)
    return _summarise(labels, samples, control, sum(arm.trials for arm in arms))


def analyze_revenue(
    arms: list[RevenueArm], control: str | None = None, draws: int = 4_000, seed: int = 0
) -> Posterior:
    """Revenue per visitor: Beta conversion rate times a Bayesian bootstrap of order values."""
    if len(arms) < 2:
        raise ValueError("An experiment needs at least two variants")
    rng = np.random.default_rng(seed)
    columns = []
    for arm in arms:
        orders = np.asarray(arm.order_values, dtype=float)
        if orders.size > arm.visitors:
            raise ValueError(f"{arm.label}: more orders than visitors")
        conversion = rng.beta(1 + orders.size, 1 + arm.visitors - orders.size, size=draws)
        if orders.size:
            weights = rng.dirichlet(np.ones(orders.size), size=draws)
            order_value = weights @ orders
        else:
            order_value = np.zeros(draws)
        columns.append(conversion * order_value)
    labels = [arm.label for arm in arms]
    return _summarise(labels, np.column_stack(columns), control, sum(a.visitors for a in arms))
