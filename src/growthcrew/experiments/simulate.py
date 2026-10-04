"""Simulations with known true rates: does the module behave as claimed?"""

import numpy as np

from growthcrew.experiments.bandit import allocate
from growthcrew.experiments.bayes import analyze_rates
from growthcrew.experiments.decide import DecisionConfig, decide
from growthcrew.experiments.models import Arm


def _arms(rng: np.random.Generator, rates: list[float], trials: list[int]) -> list[Arm]:
    return [
        Arm(label=f"v{index}", trials=n, successes=int(rng.binomial(n, rate)))
        for index, (rate, n) in enumerate(zip(rates, trials, strict=True))
    ]


def winner_rates(
    true_rates: list[float],
    per_variant: int,
    experiments: int = 1000,
    seed: int = 0,
    config: DecisionConfig | None = None,
) -> dict[str, float]:
    """Run many experiments at the planned sample and tally the decisions.

    Returns the share that called a winner, the share that called a wrong one (a variant that
    is not truly best, or any variant when all are identical), and the share that called the
    right one.
    """
    rng = np.random.default_rng(seed)
    identical = len(set(true_rates)) == 1
    best = int(np.argmax(true_rates))
    called = wrong = 0
    for run in range(experiments):
        arms = _arms(rng, true_rates, [per_variant] * len(true_rates))
        posterior = analyze_rates(arms, draws=4000, seed=run)
        decision = decide(posterior, arms, config, planned_per_variant=per_variant)
        if decision.winner:
            called += 1
            wrong += identical or decision.winner != f"v{best}"
    return {
        "winner_called": called / experiments,
        "false_winner": wrong / experiments,
        "true_winner": (called - wrong) / experiments,
    }


def bandit_regret(
    true_rates: list[float],
    weeks: int = 8,
    weekly_budget: int = 10_000,
    runs: int = 200,
    seed: int = 0,
    floor: float = 0.10,
) -> dict[str, list[float]]:
    """Average cumulative regret, week by week, of Thompson allocation and of an even split.

    Regret is the successes given up by not putting the whole budget on the variant that is
    truly best.
    """
    rng = np.random.default_rng(seed)
    best = max(true_rates)
    k = len(true_rates)
    totals = {"bandit": np.zeros(weeks), "even_split": np.zeros(weeks)}
    for run in range(runs):
        for policy in totals:
            trials, successes = np.zeros(k, dtype=int), np.zeros(k, dtype=int)
            regret = 0.0
            for week in range(weeks):
                if policy == "even_split" or week == 0:
                    shares = np.full(k, 1 / k)
                else:
                    arms = [Arm(label=f"v{i}", trials=int(trials[i]), successes=int(successes[i]))
                            for i in range(k)]  # fmt: skip
                    split = allocate(arms, floor=floor, draws=2000, seed=run * 100 + week)
                    shares = np.array([split[f"v{i}"] for i in range(k)])
                spend = np.floor(shares * weekly_budget).astype(int)
                successes += rng.binomial(spend, true_rates)
                trials += spend
                regret += float((spend * (best - np.array(true_rates))).sum())
                totals[policy][week] += regret
    return {policy: (values / runs).round(1).tolist() for policy, values in totals.items()}
