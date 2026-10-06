"""Does the panel predict real results? Each pre-test against the test's final verdict.

Only final verdicts count: a test judged once at its planned sample. A comparison with a test
still running, or one never registered, would score the panel against noise.
"""

import json

import numpy as np
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.db.models import ExperimentRegistration, PanelRun

# Fewer compared tests than this and the panel is "untested".
MIN_TESTS = 3
# A mean rank correlation below this, or a top-pick hit rate no better than chance, is "low".
MIN_CORRELATION = 0.2


def spearman(predicted: list[float], actual: list[float]) -> float:
    """Spearman rank correlation, with ties given their average rank."""

    def ranks(values: list[float]) -> np.ndarray:
        order = np.argsort(values, kind="stable")
        out = np.empty(len(values))
        out[order] = np.arange(len(values), dtype=float)
        array = np.asarray(values)
        for value in set(values):
            same = array == value
            out[same] = out[same].mean()
        return out

    a, b = ranks(predicted), ranks(actual)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def comparisons(engine: Engine, workspace: str) -> list[dict]:
    """One point per pre-tested experiment that now has a final verdict."""
    with Session(engine) as session:
        runs = session.exec(
            select(PanelRun).where(PanelRun.workspace == workspace).order_by(PanelRun.created_at)
        ).all()
        verdicts = {
            (row.cycle_id, row.experiment): row.verdict
            for row in session.exec(
                select(ExperimentRegistration).where(
                    ExperimentRegistration.workspace == workspace,
                    ExperimentRegistration.verdict.is_not(None),
                )
            )
        }
    points = []
    for run in runs:
        verdict = verdicts.get((run.cycle_id, run.experiment))
        if not verdict:
            continue
        readout = json.loads(verdict)
        actual = {arm["label"]: arm["successes"] / arm["trials"]
                  for arm in readout["arms"] if arm["trials"]}  # fmt: skip
        prediction = json.loads(run.prediction)
        stats = {
            v["label"]: v["click_share"] + v["mean_stop"] / 100 for v in prediction["variants"]
        }
        labels = [label for label in stats if label in actual]
        if len(labels) < 2:
            continue
        best = max(labels, key=lambda label: actual[label])
        points.append(
            {
                "experiment": run.experiment,
                "cycle_id": run.cycle_id,
                "date": run.created_at.isoformat(),
                "variants": len(labels),
                "correlation": round(
                    spearman([stats[x] for x in labels], [actual[x] for x in labels]), 3
                ),  # fmt: skip
                "top_pick_right": prediction["ranking"][0] == best,
                "predicted": prediction["ranking"],
                "actual": sorted(labels, key=lambda label: -actual[label]),
            }
        )
    return points


def accuracy(engine: Engine, workspace: str) -> dict:
    """The panel's record, and how far to trust it: untested, low, or useful."""
    points = comparisons(engine, workspace)
    n = len(points)
    if n:
        mean_corr = float(np.mean([p["correlation"] for p in points]))
        hit_rate = float(np.mean([p["top_pick_right"] for p in points]))
        chance = float(np.mean([1 / p["variants"] for p in points]))
    else:
        mean_corr = hit_rate = chance = 0.0
    if n < MIN_TESTS:
        trust = "untested"
        message = (
            f"The panel has been checked against {n} real test{'s' if n != 1 else ''}; it needs "
            f"{MIN_TESTS} before its predictions mean anything. Treat them as a hunch."
        )
    elif mean_corr < MIN_CORRELATION or hit_rate <= chance:
        trust = "low"
        message = (
            f"Across {n} real tests the panel's ranking matched reality poorly (average rank "
            f"correlation {mean_corr:.2f}; top pick right {hit_rate:.0%} of the time against "
            f"{chance:.0%} by chance). It is down-weighted: it no longer suggests dropping "
            "variants, and its predictions are shown for the record only."
        )
    else:
        trust = "useful"
        message = (
            f"Across {n} real tests the panel's ranking agreed with reality (average rank "
            f"correlation {mean_corr:.2f}; top pick right {hit_rate:.0%} against {chance:.0%} by "
            "chance). It still never replaces a test."
        )
    return {
        "tests_compared": n,
        "mean_correlation": round(mean_corr, 3),
        "top_pick_hit_rate": round(hit_rate, 3),
        "chance_hit_rate": round(chance, 3),
        "trust": trust,
        "message": message,
        "points": points,
    }
