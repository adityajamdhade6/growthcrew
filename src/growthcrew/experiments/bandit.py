"""Thompson sampling: how to split the next batch of budget across variants."""

import numpy as np

from growthcrew.experiments.bayes import DRAWS, rate_samples
from growthcrew.experiments.models import Arm

FLOOR = 0.10


def allocate(
    arms: list[Arm],
    floor: float = FLOOR,
    decided_winner: str | None = None,
    draws: int = DRAWS,
    seed: int = 0,
) -> dict[str, float]:
    """Share of the next batch for each variant, summing to 1.

    Each variant gets the probability that it is the best, which is what Thompson sampling
    spends in expectation. While the test is undecided no variant falls below `floor`, so a
    slow starter keeps getting enough traffic to prove itself. Once a winner has been called,
    it takes everything.
    """
    labels = [arm.label for arm in arms]
    if decided_winner is not None:
        if decided_winner not in labels:
            raise ValueError(f"'{decided_winner}' is not one of the variants")
        return {label: float(label == decided_winner) for label in labels}
    if floor * len(arms) > 1:
        raise ValueError("The floor is too high for this many variants")
    samples = rate_samples(arms, draws, seed)
    shares = np.bincount(samples.argmax(axis=1), minlength=len(arms)) / draws
    # Raise anything under the floor to it, taking the difference from the others in
    # proportion; repeat until nothing is below.
    fixed = np.zeros(len(arms), dtype=bool)
    while True:
        low = (shares < floor) & ~fixed
        if not low.any():
            break
        fixed |= low
        shares[fixed] = floor
        free = ~fixed
        remaining = 1 - floor * fixed.sum()
        total = shares[free].sum()
        shares[free] = shares[free] / total * remaining if total else remaining / free.sum()
    return {label: float(share) for label, share in zip(labels, shares, strict=True)}
