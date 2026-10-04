"""Simulation report for the experiment engine. Writes charts and numbers to reports/.

    uv run python -m evals.experiments_report

Synthetic experiments with known true rates, so the right answer is known in advance.
(a) How often is a winner called when the variants are identical?
(b) How much does Thompson allocation lose compared with an even split?
"""

import json
from pathlib import Path

import altair as alt

from growthcrew.experiments import sample_size
from growthcrew.experiments.simulate import bandit_regret, winner_rates

OUT = Path("reports/experiments")
BLUE, ORANGE, INK = "#2a78d6", "#eb6834", "#52514e"


def false_winner_table(experiments: int = 2000) -> list[dict]:
    rows = []
    scenarios = [
        ("2 variants, 2% rate", [0.02, 0.02], 8000),
        ("3 variants, 2% rate", [0.02, 0.02, 0.02], 8000),
        ("2 variants, 5% rate", [0.05, 0.05], 3000),
        ("4 variants, 1% rate", [0.01] * 4, 15000),
        ("2 variants, 10% rate", [0.10, 0.10], 2000),
    ]
    for seed, (name, rates, per_variant) in enumerate(scenarios):
        result = winner_rates(rates, per_variant, experiments, seed=seed)
        rows.append({"scenario": name, "per_variant": per_variant, "experiments": experiments,
                     "false_winner_pct": round(result["false_winner"] * 100, 2)})  # fmt: skip
    return rows


def power_table(experiments: int = 1000) -> list[dict]:
    rows = []
    for seed, (base, lift) in enumerate([(0.02, 0.4), (0.05, 0.3), (0.10, 0.2)]):
        planned = sample_size(base, lift)
        result = winner_rates([base, base * (1 + lift)], planned, experiments, seed=10 + seed)
        rows.append({"baseline_pct": base * 100, "true_lift_pct": lift * 100, "per_variant": planned,
                     "right_winner_pct": round(result["true_winner"] * 100, 1),
                     "wrong_winner_pct": round(result["false_winner"] * 100, 2)})  # fmt: skip
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    false_winners = false_winner_table()
    powered = power_table()
    true_rates = [0.012, 0.019, 0.012]
    regret = bandit_regret(true_rates, weeks=8, weekly_budget=10_000, runs=300, seed=7)

    for row in false_winners:
        row["label"] = f"{row['false_winner_pct']:.1f}%"
    base = alt.Chart(alt.Data(values=false_winners)).encode(
        y=alt.Y("scenario:N", sort=None, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("false_winner_pct:Q", title="Experiments that called a winner between identical variants (%)",
                scale=alt.Scale(domain=[0, 6]), axis=alt.Axis(values=[0, 1, 2, 3, 4, 5, 6])),
    )  # fmt: skip
    limit = alt.Chart(alt.Data(values=[{"x": 5, "note": "5% limit"}]))
    chart = (
        base.mark_bar(color=BLUE, cornerRadiusEnd=4, size=22)
        + base.mark_text(align="left", dx=6, color=INK).encode(text="label:N")
        + limit.mark_rule(color=INK, strokeDash=[4, 4]).encode(x="x:Q")
        + limit.mark_text(align="left", dx=4, dy=-112, color=INK).encode(x="x:Q", text="note:N")
    ).properties(
        width=560, height=240, title="False-winner rate, 2,000 simulated A/A tests per row"
    )
    chart.save(str(OUT / "false_winner_rate.png"), scale_factor=2)

    points = [
        {"week": week + 1, "policy": name, "regret": values[week]}
        for name, values in (("Thompson sampling, 10% floor", regret["bandit"]),
                             ("Even split", regret["even_split"]))
        for week in range(len(values))
    ]  # fmt: skip
    lines = alt.Chart(alt.Data(values=points)).encode(
        x=alt.X("week:Q", title="Week", axis=alt.Axis(values=list(range(1, 9)), format="d")),
        y=alt.Y("regret:Q", title="Clicks given up so far (cumulative regret)"),
        color=alt.Color("policy:N", title=None, scale=alt.Scale(range=[ORANGE, BLUE]),
                        legend=alt.Legend(orient="top-left")),
    )  # fmt: skip
    ends = lines.transform_filter("datum.week == 8")
    chart = (
        lines.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60))
        + ends.mark_text(align="right", dx=-8, dy=-12, color=INK).encode(text=alt.Text("regret:Q", format=",.0f"))
    ).properties(width=560, height=300,
                 title="Three ad variants (true CTR 1.2%, 1.9%, 1.2%), 10,000 impressions a week")  # fmt: skip
    chart.save(str(OUT / "bandit_regret.png"), scale_factor=2)

    results = {"false_winner": false_winners, "power": powered,
               "bandit": {"true_rates": true_rates, "weekly_budget": 10_000, "runs": 300, **regret}}  # fmt: skip
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    worst = max(row["false_winner_pct"] for row in false_winners)
    saved = 1 - regret["bandit"][-1] / regret["even_split"][-1]
    print(f"Worst false-winner rate: {worst}%")
    print(f"Bandit regret after 8 weeks: {regret['bandit'][-1]:,.0f} clicks against "
          f"{regret['even_split'][-1]:,.0f} for an even split ({saved:.0%} less)")  # fmt: skip
    for row in powered:
        print(row)


if __name__ == "__main__":
    main()
