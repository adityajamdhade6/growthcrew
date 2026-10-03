from typing import Literal

from pydantic import BaseModel

from growthcrew.frameworks.base import Framework

Stage = Literal["awareness", "consideration", "conversion", "retention"]
Timing = Literal["days 1-30", "days 31-60", "days 61-90", "all 90 days"]


class ChannelPlay(BaseModel):
    channel: str
    tactic: str
    timing: Timing
    # Share of the total 90-day budget. All plays across all stages sum to 100.
    budget_pct: int
    support: list[str]


class StageKPI(BaseModel):
    metric: str
    target: str


class FunnelStage(BaseModel):
    stage: Stage
    objective: str
    channels: list[ChannelPlay]
    kpis: list[StageKPI]


class FunnelPlan(BaseModel):
    stages: list[FunnelStage]


FUNNEL = Framework(
    key="funnel",
    name="Funnel and channel plan (90 days)",
    purpose="Decide where a small budget goes across awareness, consideration, conversion "
    "and retention over the next 90 days.",
    inputs=(
        "Competitor channels and market signals from the research",
        "Brain: ICP, stage of the business, geography",
        "The positioning and messaging house you just wrote",
    ),
    output_model=FunnelPlan,
    quality_criteria=(
        "All four stages appear, in order.",
        "budget_pct across every channel play sums to exactly 100.",
        "A small business can run the plan: at most six channel plays in total.",
        "Each play names a concrete tactic, not just a channel.",
        "Each play cites evidence that the target customer is reachable there.",
        "Each stage has one or two KPIs with a target or an explicit 'baseline first' note.",
    ),
    instructions="For each stage give the objective, the channel plays and the KPIs. Spend "
    "where the evidence says the customer already is. It is fine for a stage to have a single "
    "play. Express budget as percentages only, since the total budget is not known. Where no "
    "baseline exists, write the KPI target as 'establish baseline in days 1-30, then +X%'.",
)
