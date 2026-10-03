"""Regression runner. Writes evals/results/scorecard.md and scorecard.json.

    make eval        offline evals: no API key, no cost
    make eval-live   also runs the evals that call the model (costs money)

Exits non-zero if any eval fails, so CI can block a prompt change that regresses.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from evals import suites
from evals.suites import EvalResult

RESULTS = Path(__file__).parent / "results"
ICON = {"pass": "PASS", "fail": "FAIL", "pending": "PENDING", "skipped": "SKIPPED"}


def run(live: bool, limit: int | None) -> tuple[list[EvalResult], float | None]:
    results = [
        suites.golden_dataset(),
        suites.golden_references(),
        suites.analyst_winner_detection(),
        suites.analyst_end_to_end(),
        suites.guardrail_cases(),
        suites.strategy_completeness_saved(),
    ]
    if not live:
        results.append(suites.calibration_status())
        for name, agent in (
            ("strategy", "strategist"),
            ("content", "content"),
            ("research_citations", "research"),
        ):
            results.append(
                EvalResult(
                    name=name,
                    agent=agent,
                    status="skipped",
                    notes=["Needs the model: run `make eval-live`"],
                )
            )
        return results, None

    from growthcrew import budget
    from growthcrew.db import models
    from growthcrew.llm import LLM

    # Live evals log to their own database so they do not show up in product costs.
    RESULTS.mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{RESULTS / 'evals.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        before = session.exec(select(func.max(models.LLMCall.id))).one() or 0
    llm = LLM(engine=engine)
    for brand in suites.BRANDS.values():
        budget.set_weekly_limit(engine, brand.workspace, 1000.0)
    root = RESULTS / "workspaces"

    strategy_result, strategies = suites.live_strategy(llm, root)
    results += [
        strategy_result,
        suites.live_content(llm, root, strategies, limit),
        suites.live_judge_calibration(llm),
        suites.live_research(llm),
    ]
    with Session(engine) as session:
        cost = session.exec(
            select(func.sum(models.LLMCall.cost_usd)).where(models.LLMCall.id > before)
        ).one()
    return results, float(cost or 0.0)


def scorecard_markdown(results: list[EvalResult], live: bool, cost: float | None) -> str:
    counts = {status: sum(r.status == status for r in results) for status in ICON}
    lines = [
        "# GrowthCrew eval scorecard",
        "",
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · mode: {'live' if live else 'offline'}"
        + (f" · model cost of this run: ${cost:.2f}" if cost is not None else ""),
        "",
        f"**{counts['pass']} passed, {counts['fail']} failed, {counts['pending']} waiting on a "
        f"human, {counts['skipped']} not run**",
        "",
        "| Eval | Agent | Status |",
        "|---|---|---|",
        *(f"| {r.name} | {r.agent} | {ICON[r.status]} |" for r in results),
        "",
    ]
    for r in results:
        lines += [f"## {r.name}: {ICON[r.status]}", ""]
        if r.metrics:
            lines += ["| Metric | Value | Threshold |", "|---|---|---|"]
            lines += [
                f"| {key} | {value} | {r.thresholds.get(key, '')} |"
                for key, value in r.metrics.items()
            ]
            lines.append("")
        lines += [f"- {note}" for note in r.notes]
        if r.notes:
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="Also run evals that call the model")
    parser.add_argument("--limit", type=int, help="Live content eval: only the first N requests")
    args = parser.parse_args()

    results, cost = run(args.live, args.limit)
    RESULTS.mkdir(exist_ok=True)
    text = scorecard_markdown(results, args.live, cost)
    (RESULTS / "scorecard.md").write_text(text + "\n")
    (RESULTS / "scorecard.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "live" if args.live else "offline",
                "model_cost_usd": cost,
                "results": [r.model_dump() for r in results],
            },
            indent=2,
        )
        + "\n"
    )
    print(text)
    return 1 if any(r.status == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
