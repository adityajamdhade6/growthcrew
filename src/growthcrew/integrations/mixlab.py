"""The GrowthCrew side of the MixLab connection.

MixLab is a separate marketing-mix model. It answers three questions over MCP:
`get_channel_roi`, `get_response_curves` and `optimize_budget`. GrowthCrew uses the budget
split it recommends, with its uncertainty, to check the strategy's channel plan, and sends
back final experiment verdicts so MixLab can calibrate its curves against real tests.

Rules kept here:
- MixLab's split is advice with an interval, not an order. A planned share outside the
  interval is not changed in code; it is listed as an issue the strategist must explain or fix.
- Only final, pre-registered verdicts go back as calibration; descriptive comparisons never do.
- `SyntheticMixLab` is a stand-in with made-up curves for demos and tests. Everything it
  returns is labelled synthetic and must never be shown as a client's real ROI.
"""

import json
import math
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.db.models import ExperimentRegistration


class ChannelROI(BaseModel):
    channel: str
    roi: float
    roi_low: float
    roi_high: float


class ChannelShare(BaseModel):
    channel: str
    share_pct: float
    low_pct: float
    high_pct: float


class BudgetAdvice(BaseModel):
    total: float
    shares: list[ChannelShare]
    source: str
    synthetic: bool


class MixLab(Protocol):
    def get_channel_roi(self, workspace: str) -> list[ChannelROI]: ...
    def get_response_curves(self, workspace: str) -> dict[str, list[tuple[float, float]]]: ...
    def optimize_budget(self, workspace: str, total: float) -> BudgetAdvice: ...
    def send_calibration(self, workspace: str, results: list[dict]) -> int: ...


class MCPMixLab:
    """Calls a MixLab MCP server through any `call(tool, arguments) -> dict` function, such
    as the MCP client in `connectors.mcp_source`. Read-only, apart from the calibration upload."""

    def __init__(self, call, source: str = "MixLab") -> None:
        self.call, self.source = call, source

    def get_channel_roi(self, workspace: str) -> list[ChannelROI]:
        rows = self.call("get_channel_roi", {"workspace": workspace})["channels"]
        return [ChannelROI(**row) for row in rows]

    def get_response_curves(self, workspace: str) -> dict[str, list[tuple[float, float]]]:
        curves = self.call("get_response_curves", {"workspace": workspace})["curves"]
        return {name: [tuple(p) for p in points] for name, points in curves.items()}

    def optimize_budget(self, workspace: str, total: float) -> BudgetAdvice:
        out = self.call("optimize_budget", {"workspace": workspace, "total": total})
        shares = [ChannelShare(**row) for row in out["shares"]]
        return BudgetAdvice(total=total, shares=shares, source=self.source, synthetic=False)

    def send_calibration(self, workspace: str, results: list[dict]) -> int:
        out = self.call("record_calibration", {"workspace": workspace, "results": results})
        return int(out.get("accepted", 0))


class SyntheticMixLab:
    """Made-up diminishing-returns curves: spend s on a channel returns a * (1 - exp(-s / k)).
    For the joint demo and tests only."""

    CURVES = {"email": (900.0, 300.0), "search": (1400.0, 900.0), "social": (700.0, 600.0)}

    def __init__(self) -> None:
        self.calibration: list[dict] = []

    def _value(self, channel: str, spend: float) -> float:
        a, k = self.CURVES[channel]
        return a * (1 - math.exp(-spend / k))

    def get_channel_roi(self, workspace: str) -> list[ChannelROI]:
        out = []
        for channel in self.CURVES:
            roi = self._value(channel, 500) / 500
            out.append(
                ChannelROI(
                    channel=channel,
                    roi=round(roi, 2),
                    roi_low=round(roi * 0.7, 2),
                    roi_high=round(roi * 1.3, 2),
                )
            )
        return out

    def get_response_curves(self, workspace: str) -> dict[str, list[tuple[float, float]]]:
        steps = [0, 250, 500, 1000, 2000, 4000]
        return {c: [(s, round(self._value(c, s), 1)) for s in steps] for c in self.CURVES}

    def optimize_budget(self, workspace: str, total: float) -> BudgetAdvice:
        # Greedy allocation in 1% steps by marginal return.
        spend = dict.fromkeys(self.CURVES, 0.0)
        step = total / 100
        for _ in range(100):
            best = max(
                self.CURVES,
                key=lambda c: self._value(c, spend[c] + step) - self._value(c, spend[c]),
            )
            spend[best] += step
        shares = [
            ChannelShare(
                channel=c,
                share_pct=round(100 * s / total, 1),
                low_pct=round(max(0, 100 * s / total - 10), 1),
                high_pct=round(min(100, 100 * s / total + 10), 1),
            )
            for c, s in spend.items()
        ]
        return BudgetAdvice(total=total, shares=shares, source="MixLab", synthetic=True)

    def send_calibration(self, workspace: str, results: list[dict]) -> int:
        self.calibration += results
        return len(results)


def _channel(play_channel: str) -> str:
    name = play_channel.lower()
    for key, words in {
        "email": ("email", "newsletter"),
        "search": ("search", "seo", "google", "ppc"),
        "social": ("social", "instagram", "facebook", "linkedin", "tiktok"),
    }.items():
        if any(word in name for word in words):
            return key
    return name


def review_split(planned: dict[str, float], advice: BudgetAdvice) -> list[str]:
    """Issues for the strategy: each planned share outside MixLab's interval needs a reason.
    `planned` maps channel names (as the plan writes them) to budget percentages."""
    totals: dict[str, float] = {}
    for name, pct in planned.items():
        totals[_channel(name)] = totals.get(_channel(name), 0) + pct
    label = f"{advice.source}{' (synthetic data)' if advice.synthetic else ''}"
    issues = []
    for share in advice.shares:
        plan = totals.get(share.channel, 0)
        if not share.low_pct <= plan <= share.high_pct:
            issues.append(
                f"Plan gives {plan:.0f}% to {share.channel}; {label} suggests "
                f"{share.share_pct:.0f}% (95% interval {share.low_pct:.0f}-{share.high_pct:.0f}%). "
                "Explain the difference or adjust the split."
            )
    return issues


def plan_split(strategy_core) -> dict[str, float]:
    """The channel plan's budget split, from a StrategyCore's funnel plan."""
    return {
        play.channel: play.budget_pct
        for stage in strategy_core.channel_plan.stages
        for play in stage.channels
    }


def calibration_results(engine: Engine, workspace: str) -> list[dict]:
    """Final, pre-registered verdicts with their uncertainty, for MixLab to calibrate against."""
    with Session(engine) as session:
        rows = session.exec(
            select(ExperimentRegistration).where(
                ExperimentRegistration.workspace == workspace,
                ExperimentRegistration.verdict.is_not(None),
            )
        ).all()
    out = []
    for row in rows:
        verdict = json.loads(row.verdict)
        uncertainty = verdict["uncertainty"]
        out.append(
            {
                "experiment": row.experiment,
                "channel": row.experiment.rsplit("-", 1)[-1],
                "metric": verdict["metric"],
                "status": verdict["status"],
                "winner": verdict.get("winner"),
                "lift_low_pct": uncertainty["lift_low_pct"],
                "lift_high_pct": uncertainty["lift_high_pct"],
                "sample_size": uncertainty["sample_size"],
            }
        )
    return out
