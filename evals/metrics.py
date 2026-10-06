"""Deterministic metrics used by the evals."""

import re
import statistics


def _syllables(word: str) -> int:
    word = word.lower()
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(1, count)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid grade level. Around 8 is plain English; above 12 is hard going."""
    sentences = [s for s in re.split(r"[.!?]+\s|\n+", text) if re.search(r"[A-Za-z]", s)]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if not sentences or not words:
        return 0.0
    syllables = sum(_syllables(word) for word in words)
    grade = 0.39 * len(words) / len(sentences) + 11.8 * syllables / len(words) - 15.59
    return round(grade, 1)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation: do the judge and the human order the pieces the same way?"""
    ra, rb = _ranks(a), _ranks(b)
    if len(set(ra)) < 2 or len(set(rb)) < 2:
        return 0.0
    return round(statistics.correlation(ra, rb), 3)


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Agreement on a yes/no decision, corrected for chance."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = (sum(a) / n) * (sum(b) / n) + (1 - sum(a) / n) * (1 - sum(b) / n)
    return round((observed - expected) / (1 - expected), 3) if expected < 1 else 1.0


def agreement(judge: list[float], human: list[float], pass_mark: int = 8) -> dict:
    diffs = [abs(j - h) for j, h in zip(judge, human, strict=True)]
    return {
        "n": len(judge),
        "spearman": spearman(judge, human),
        "mean_abs_diff": round(statistics.mean(diffs), 2),
        "within_1_point": round(sum(d <= 1 for d in diffs) / len(diffs), 2),
        "pass_fail_kappa": cohen_kappa(
            [j >= pass_mark for j in judge], [h >= pass_mark for h in human]
        ),
        "judge_mean_minus_human_mean": round(statistics.mean(judge) - statistics.mean(human), 2),
    }


def wilson(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion. Ties count as half a win before calling this."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def win_rate(preferences: list[str]) -> dict:
    """Win rate of "new" over "old" from pairwise preferences (new, old, tie), with its interval."""
    n = len(preferences)
    wins = sum(p == "new" for p in preferences) + 0.5 * sum(p == "tie" for p in preferences)
    low, high = wilson(wins, n)
    return {"pairs": n, "win_rate": round(wins / n, 3) if n else 0.0,
            "ci_low": round(low, 3), "ci_high": round(high, 3)}  # fmt: skip
