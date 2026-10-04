"""Experiment statistics: Bayesian A/B/n tests, pre-registration, power, bandits, guardrails.

Standalone: this package imports only numpy, pydantic and the standard library, and nothing
from the rest of GrowthCrew, so it can be tested and reused on its own.
"""

from growthcrew.experiments.bandit import allocate
from growthcrew.experiments.bayes import analyze_rates, analyze_revenue
from growthcrew.experiments.decide import DecisionConfig, decide
from growthcrew.experiments.guardrails import check_guardrails
from growthcrew.experiments.models import (
    Arm,
    Decision,
    GuardrailData,
    GuardrailSpec,
    Posterior,
    Preregistration,
    RevenueArm,
    Verdict,
)
from growthcrew.experiments.power import power, sample_size
from growthcrew.experiments.prereg import MetricNotPreregistered, judge, preregister

__all__ = [
    "Arm", "Decision", "DecisionConfig", "GuardrailData", "GuardrailSpec",
    "MetricNotPreregistered", "Posterior", "Preregistration", "RevenueArm", "Verdict",
    "allocate", "analyze_rates", "analyze_revenue", "check_guardrails", "decide", "judge",
    "power", "preregister", "sample_size",
]  # fmt: skip
