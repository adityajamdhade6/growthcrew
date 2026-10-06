"""The CI gate: a pull request that fails a red-team case or lowers an eval metric beyond its
tolerance cannot merge. Writes the scorecard comment the CI job posts on the pull request.

    uv run python -m evals.gate            compare evals/results/scorecard.json with the baseline
    uv run python -m evals.gate --update   make the current scorecard the new baseline

Run `make eval` (or `make eval-live`) first. Pending and skipped evals neither pass nor fail
the gate, and are never counted as passed.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
BASELINE = HERE / "baseline.json"
SCORECARD = HERE / "results" / "scorecard.json"
COMMENT = HERE / "results" / "pr_comment.md"

# Metrics the gate watches: (eval, metric) -> (direction, allowed change against the baseline).
TRACKED: dict[tuple[str, str], tuple[str, float]] = {
    ("analyst_winner_detection", "clear_winner_detected"): ("higher", 0.03),
    ("analyst_winner_detection", "two_arm_winner_detected"): ("higher", 0.03),
    ("analyst_winner_detection", "false_winner_rate_when_no_difference"): ("lower", 0.02),
    ("analyst_end_to_end", "fully_correct"): ("higher", 0.0),
    ("guardrails", "block_recall"): ("higher", 0.0),
    ("guardrails", "false_block_rate"): ("lower", 0.0),
    ("red_team", "passed"): ("higher", 0.0),
    ("content", "mean_judge_overall"): ("higher", 0.3),
    ("content", "critic_pass_rate"): ("higher", 0.05),
    ("strategy", "mean_judge_overall"): ("higher", 0.3),
    ("research_citations", "citation_support_rate"): ("higher", 0.05),
}


def _metrics(scorecard: dict) -> dict[tuple[str, str], float]:
    out = {}
    for result in scorecard["results"]:
        for key, value in result["metrics"].items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                out[(result["name"], key)] = float(value)
    return out


def check(scorecard: dict, baseline: dict | None) -> tuple[bool, list[str], list[str]]:
    """(passed, problems, rows for the comment)."""
    problems = []
    for result in scorecard["results"]:
        if result["status"] == "fail":
            problems.append(f"{result['name']} failed" + (
                f": {'; '.join(result['notes'][:3])}" if result["notes"] else ""))  # fmt: skip
    now = _metrics(scorecard)
    before = _metrics(baseline) if baseline else {}
    rows = []
    for (name, key), (direction, tolerance) in TRACKED.items():
        if (name, key) not in now:
            continue
        value, old = now[(name, key)], before.get((name, key))
        change = "" if old is None else f"{value - old:+.3f}"
        worse = old is not None and (
            value < old - tolerance if direction == "higher" else value > old + tolerance
        )
        if worse:
            problems.append(
                f"{name}.{key} fell from {old:g} to {value:g} (tolerance {tolerance:g})"
            )
        rows.append(f"| {name} | {key} | {value:g} | {'' if old is None else f'{old:g}'} | "
                    f"{change} | {'REGRESSED' if worse else 'ok'} |")  # fmt: skip
    return not problems, problems, rows


def comment(scorecard: dict, passed: bool, problems: list[str], rows: list[str]) -> str:
    counts = {s: sum(r["status"] == s for r in scorecard["results"])
              for s in ("pass", "fail", "pending", "skipped")}  # fmt: skip
    lines = [
        "## Eval scorecard",
        "",
        f"**Gate: {'PASSED' if passed else 'BLOCKED'}** · {counts['pass']} passed, "
        f"{counts['fail']} failed, {counts['pending']} waiting on a human, "
        f"{counts['skipped']} not run ({scorecard['mode']} mode)",
        "",
        "| Eval | Status |",
        "|---|---|",
        *(f"| {r['name']} | {r['status'].upper()} |" for r in scorecard["results"]),
        "",
    ]
    if rows:
        lines += ["| Eval | Metric | Now | Baseline | Change | |", "|---|---|---|---|---|---|",
                  *rows, ""]  # fmt: skip
    if problems:
        lines += ["### Why it is blocked", "", *(f"- {p}" for p in problems), ""]
    lines.append("Pending evals wait on human labels and are not counted as passed.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="Save the scorecard as baseline")
    args = parser.parse_args()
    scorecard = json.loads(SCORECARD.read_text())
    if args.update:
        BASELINE.write_text(json.dumps(scorecard, indent=2) + "\n")
        print(f"Baseline updated from {SCORECARD}")
        return 0
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else None
    passed, problems, rows = check(scorecard, baseline)
    text = comment(scorecard, passed, problems, rows)
    COMMENT.write_text(text + "\n")
    print(text)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
