from pydantic import BaseModel, Field, computed_field, field_validator

from growthcrew.frameworks.base import Framework


class Experiment(BaseModel):
    name: str
    hypothesis: str
    metric: str
    minimum_sample: str
    decision_rule: str
    impact: int = Field(description="1-10: how much the metric moves if the hypothesis holds")
    confidence: int = Field(description="1-10: how strongly the evidence supports it")
    ease: int = Field(description="1-10: how cheap and fast it is to run")
    support: list[str]

    @field_validator("impact", "confidence", "ease")
    @classmethod
    def _clamp(cls, value: int) -> int:
        return min(10, max(1, value))

    @computed_field
    @property
    def ice(self) -> float:
        return round((self.impact + self.confidence + self.ease) / 3, 1)


class TestPlan(BaseModel):
    __test__ = False  # not a pytest class

    experiments: list[Experiment]


TEST_AND_LEARN = Framework(
    key="test_and_learn",
    name="Test-and-learn plan",
    purpose="Turn the strategy's riskiest assumptions into experiments with a decision "
    "agreed before the data arrives.",
    inputs=(
        "The positioning, messaging house and channel plan you just wrote",
        "Evidence marked inferred or low confidence: these are the assumptions to test",
    ),
    output_model=TestPlan,
    quality_criteria=(
        "There are exactly five experiments.",
        "Each hypothesis has the form 'If we <change>, then <metric> will <move> because "
        "<reason from the evidence>'.",
        "Each has one primary metric.",
        "minimum_sample is a number of visitors, sends, leads or days that a small business "
        "can reach within 90 days.",
        "The decision rule says what happens on a win, a loss and an inconclusive result.",
        "Confidence scores are low where the supporting evidence is inferred or thin.",
    ),
    instructions="Write five experiments. Prioritise the assumptions the strategy depends on "
    "most, especially any that rest on inferred brain fields. Score impact, confidence and "
    "ease from 1 to 10; the ICE score and ranking are computed for you.",
)
