"""Joint GrowthCrew x MixLab demo on synthetic data.

    uv run python -m evals.mixlab_demo

A made-up plan is checked against the synthetic MixLab's budget split, and a made-up final
verdict is sent back as calibration. Every number here is synthetic; none is a client result.
"""

from growthcrew.integrations.mixlab import SyntheticMixLab, review_split

PLAN = {"Email newsletter": 20, "Google search ads": 30, "Instagram posts": 50}
VERDICTS = [
    {
        "experiment": "07-day03-email",
        "channel": "email",
        "metric": "clicks",
        "status": "winner",
        "winner": "b",
        "lift_low_pct": 3.1,
        "lift_high_pct": 41.0,
        "sample_size": 5200,
    },
]


def run() -> str:
    lab = SyntheticMixLab()
    advice = lab.optimize_budget("demo", 3000)
    lines = [
        "# GrowthCrew x MixLab demo (synthetic data)",
        "",
        "| Channel | Plan | MixLab (95% interval) |",
        "|---|---|---|",
    ]
    for share in advice.shares:
        planned = sum(
            v
            for k, v in PLAN.items()
            if share.channel in k.lower()
            or (share.channel == "search" and "google" in k.lower())
            or (share.channel == "social" and "instagram" in k.lower())
        )
        lines.append(
            f"| {share.channel} | {planned}% | {share.share_pct:.0f}% "
            f"({share.low_pct:.0f}-{share.high_pct:.0f}%) |"
        )
    issues = review_split(PLAN, advice)
    lines += ["", "Issues the strategist must explain or fix:", ""]
    lines += [f"- {issue}" for issue in issues] or ["- none"]
    sent = lab.send_calibration("demo", VERDICTS)
    lines += [
        "",
        f"Calibration sent back to MixLab: {sent} final verdict(s).",
        "",
        "All figures are synthetic and illustrate the flow only.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(run())
