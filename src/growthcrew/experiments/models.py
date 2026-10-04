from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

# winner: one variant is clearly best. no_clear_difference: the planned sample is in and the
# variants are too close to separate. keep_running: not enough data yet.
Status = Literal["winner", "no_clear_difference", "keep_running"]


class Arm(BaseModel):
    """One variant of a rate metric: successes out of trials (clicks out of impressions)."""

    label: str
    trials: int
    successes: int

    @property
    def rate(self) -> float:
        return self.successes / self.trials if self.trials else 0.0


class RevenueArm(BaseModel):
    """One variant of a revenue-per-visitor metric: visitors, and the value of each order."""

    label: str
    visitors: int
    order_values: list[float]


class Interval(BaseModel):
    mean: float
    low: float
    high: float


class Posterior(BaseModel):
    """What the data says about each variant. Rates and losses are in the metric's own units."""

    labels: list[str]
    # Posterior mean of each variant's rate (or revenue per visitor).
    mean: dict[str, float]
    # Probability each variant is the best.
    prob_best: dict[str, float]
    # Expected loss of choosing each variant: how much worse than the true best it is, on
    # average over what the truth could be. Zero would mean no possible regret.
    expected_loss: dict[str, float]
    # Relative lift of each variant over the control, in percent, with a 95% credible interval.
    lift_vs_control: dict[str, Interval]
    control: str
    leader: str
    runner_up: str
    # The leader's relative lift over the runner-up, in percent.
    leader_lift: Interval
    sample_size: int


class Decision(BaseModel):
    status: Status
    winner: str | None = None
    reason: str
    # For keep_running: roughly how many more trials, in total, before a call can be made.
    more_needed: int = 0


class GuardrailSpec(BaseModel):
    """A metric the winner must not damage."""

    metric: str
    lower_is_better: bool = True
    # How much worse than the control, relatively, is tolerated. 0.10 means 10%.
    tolerance: float = 0.10


class GuardrailData(BaseModel):
    """A guardrail's observed values. Give `arms` for a rate, or `values` for anything else."""

    spec: GuardrailSpec
    arms: list[Arm] = []
    values: dict[str, float] = {}


class Preregistration(BaseModel):
    """What was committed to before the experiment started."""

    experiment: str
    hypothesis: str
    primary_metric: str
    guardrails: list[GuardrailSpec] = []
    variants: list[str]
    control: str
    # The rate assumed when planning, and the smallest relative lift worth detecting.
    baseline_rate: float
    minimum_detectable_effect: float
    planned_per_variant: int
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Verdict(BaseModel):
    """A pre-registered experiment judged against its registration."""

    experiment: str
    metric: str
    posterior: Posterior
    decision: Decision
    # Guardrails the winner damages. A flagged winner is not accepted automatically.
    guardrail_flags: list[str] = []

    @property
    def clean_winner(self) -> str | None:
        return self.decision.winner if not self.guardrail_flags else None
