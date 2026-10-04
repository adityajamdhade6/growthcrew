"""The experiments package on its own: no database, no agents."""

import ast
from pathlib import Path

import pytest

from growthcrew import experiments
from growthcrew.experiments import (
    Arm,
    DecisionConfig,
    GuardrailData,
    GuardrailSpec,
    MetricNotPreregistered,
    RevenueArm,
    allocate,
    analyze_rates,
    analyze_revenue,
    check_guardrails,
    decide,
    judge,
    power,
    preregister,
    sample_size,
)
from growthcrew.experiments.simulate import bandit_regret, winner_rates

CTR = "click-through rate"


def arms(*pairs):
    return [Arm(label=f"v{i}", trials=n, successes=s) for i, (s, n) in enumerate(pairs)]


def test_the_package_is_standalone():
    """It may import numpy, pydantic and itself, and nothing else from GrowthCrew."""
    for path in Path(experiments.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if name.startswith("growthcrew"):
                    assert name.startswith("growthcrew.experiments"), f"{path.name} imports {name}"


# --- Bayesian A/B/n ---


def test_rates_posterior_matches_what_is_known_analytically():
    # Identical data: each of two variants is best half the time, and losses are equal.
    same = analyze_rates(arms((50, 1000), (50, 1000)))
    assert same.prob_best["v0"] == pytest.approx(0.5, abs=0.02)
    assert same.expected_loss["v0"] == pytest.approx(same.expected_loss["v1"], rel=0.1)
    # The posterior mean of Beta(1 + s, 1 + n - s) is (s + 1) / (n + 2).
    assert same.mean["v0"] == pytest.approx(51 / 1002, rel=0.01)

    clear = analyze_rates(arms((50, 1000), (100, 1000)))
    assert clear.leader == "v1" and clear.prob_best["v1"] > 0.999
    assert clear.expected_loss["v1"] < 1e-4 < clear.expected_loss["v0"]
    # Losing 5 points on a 10% rate: the expected loss of the worse arm is about 0.05.
    assert clear.expected_loss["v0"] == pytest.approx(0.05, abs=0.005)
    lift = clear.lift_vs_control["v1"]
    assert lift.low < 100 < lift.high and lift.mean == pytest.approx(98, abs=6)
    assert sum(clear.prob_best.values()) == pytest.approx(1)
    assert clear.sample_size == 2000


def test_results_are_repeatable_and_inputs_are_checked():
    data = arms((30, 900), (41, 950), (28, 870))
    assert analyze_rates(data) == analyze_rates(data)
    assert analyze_rates(data, control="v2").control == "v2"
    with pytest.raises(ValueError, match="at least two"):
        analyze_rates(arms((1, 10)))
    with pytest.raises(ValueError, match="between 0 and trials"):
        analyze_rates(arms((11, 10), (1, 10)))


def test_revenue_per_visitor_accounts_for_conversion_and_order_value():
    # B converts less often but its orders are worth far more.
    a = RevenueArm(label="a", visitors=1000, order_values=[20.0] * 50)
    b = RevenueArm(label="b", visitors=1000, order_values=[80.0] * 30)
    result = analyze_revenue([a, b])
    assert result.mean["a"] == pytest.approx(1.0, rel=0.1)  # 5% x $20
    assert result.mean["b"] == pytest.approx(2.4, rel=0.1)  # 3% x $80
    assert result.leader == "b" and result.prob_best["b"] > 0.99

    # A single huge order should not be mistaken for a reliable difference.
    lucky = RevenueArm(label="b", visitors=1000, order_values=[20.0] * 49 + [900.0])
    assert analyze_revenue([a, lucky]).prob_best["b"] < 0.99
    empty = RevenueArm(label="b", visitors=500, order_values=[])
    assert analyze_revenue([a, empty]).mean["b"] == 0
    with pytest.raises(ValueError, match="more orders than visitors"):
        analyze_revenue([a, RevenueArm(label="b", visitors=1, order_values=[1.0, 2.0])])


# --- decision rule ---


def test_winner_needs_low_expected_loss_and_high_probability():
    data = arms((160, 14655), (281, 15184), (174, 14483))
    decision = decide(analyze_rates(data), data, planned_per_variant=11706)
    assert (decision.status, decision.winner) == ("winner", "v1")
    assert "over 99.9% likely" in decision.reason

    # A stricter loss threshold than the data can meet: no call.
    close = arms((500, 10000), (540, 10000))
    posterior = analyze_rates(close)
    assert decide(posterior, close, planned_per_variant=10000).status == "no_clear_difference"
    loose = DecisionConfig(loss_threshold=0.5, min_prob_best=0.5)
    assert decide(posterior, close, loose, planned_per_variant=10000).winner == "v1"


def test_nothing_is_called_before_the_planned_sample():
    # A huge early lead on a tenth of the planned sample.
    early = arms((10, 1000), (40, 1000))
    posterior = analyze_rates(early)
    assert posterior.prob_best["v1"] > 0.999
    decision = decide(posterior, early, planned_per_variant=10000)
    assert decision.status == "keep_running" and decision.winner is None
    assert decision.more_needed == 18000  # 9,000 more for each of two variants
    assert "1,000 of the 10,000 trials" in decision.reason

    tiny = arms((2, 30), (9, 30))
    unplanned = decide(analyze_rates(tiny), tiny)
    assert unplanned.status == "keep_running" and unplanned.more_needed > 0


# --- power ---


def test_sample_size_matches_the_textbook_value():
    # The standard example: 10% baseline, 20% relative lift, 5% alpha, 80% power.
    assert sample_size(0.10, 0.20) == pytest.approx(3839, abs=5)
    assert power(0.10, 0.20, 3839) == pytest.approx(0.80, abs=0.005)
    # Smaller effects and more variants both need more data.
    assert sample_size(0.10, 0.10) > 3 * sample_size(0.10, 0.20)
    assert sample_size(0.10, 0.20, variants=3) > sample_size(0.10, 0.20)
    assert power(0.10, 0.20, 500) < 0.3
    for bad in [(0, 0.2), (0.1, 0), (0.9, 0.5)]:
        with pytest.raises(ValueError):
            sample_size(*bad)


# --- pre-registration ---


def registration(**overrides):
    return preregister(**{
        "experiment": "ad-1", "hypothesis": "Outcome beats pain", "primary_metric": CTR,
        "variants": ["pain", "outcome"], "baseline_rate": 0.02, "minimum_detectable_effect": 0.4,
        "guardrails": [GuardrailSpec(metric="cost per click"),
                       GuardrailSpec(metric="unsubscribe rate")],
        **overrides,
    })  # fmt: skip


def test_registration_plans_the_sample_and_only_its_metric_can_be_judged():
    plan = registration()
    assert plan.planned_per_variant == sample_size(0.02, 0.4) and plan.control == "pain"
    data = [
        Arm(label="pain", trials=9000, successes=180),
        Arm(label="outcome", trials=9000, successes=270),
    ]
    verdict = judge(plan, CTR, data)
    assert verdict.decision.winner == "outcome" and verdict.clean_winner == "outcome"

    # Switching to a metric that happens to look good is refused.
    with pytest.raises(MetricNotPreregistered, match="registered on 'click-through rate'"):
        judge(plan, "conversion rate", data)
    with pytest.raises(ValueError, match="not in the registration"):
        judge(plan, CTR, [*data, Arm(label="surprise", trials=9000, successes=400)])
    with pytest.raises(ValueError, match="hypothesis"):
        registration(hypothesis=" ")
    with pytest.raises(ValueError, match="two distinct"):
        registration(variants=["pain", "pain"])


# --- guardrails ---


def test_a_winner_that_hurts_a_guardrail_is_flagged():
    plan = registration()
    data = [
        Arm(label="pain", trials=9000, successes=180),
        Arm(label="outcome", trials=9000, successes=270),
    ]
    unsub = GuardrailSpec(metric="unsubscribe rate")
    harmful = GuardrailData(spec=unsub, arms=[Arm(label="pain", trials=5000, successes=25),
                                              Arm(label="outcome", trials=5000, successes=70)])  # fmt: skip
    verdict = judge(plan, CTR, data, [harmful])
    assert verdict.decision.winner == "outcome" and verdict.clean_winner is None
    assert "unsubscribe rate" in verdict.guardrail_flags[0]

    harmless = GuardrailData(spec=unsub, arms=[Arm(label="pain", trials=5000, successes=25),
                                               Arm(label="outcome", trials=5000, successes=27)])  # fmt: skip
    assert judge(plan, CTR, data, [harmless]).guardrail_flags == []

    cpc = GuardrailSpec(metric="cost per click", tolerance=0.10)
    assert (
        check_guardrails(
            "outcome", "pain", [GuardrailData(spec=cpc, values={"pain": 0.40, "outcome": 0.43})]
        )
        == []
    )
    [flag] = check_guardrails(
        "outcome", "pain", [GuardrailData(spec=cpc, values={"pain": 0.40, "outcome": 0.50})]
    )
    assert "25% worse" in flag
    # A guardrail that was not registered is not added after the fact.
    surprise = GuardrailData(
        spec=GuardrailSpec(metric="bounce rate"), values={"pain": 1, "outcome": 9}
    )
    assert judge(plan, CTR, data, [surprise]).guardrail_flags == []
    # Higher-is-better guardrails work the other way round.
    quality = GuardrailSpec(metric="lead quality", lower_is_better=False)
    assert check_guardrails(
        "outcome", "pain", [GuardrailData(spec=quality, values={"pain": 8.0, "outcome": 6.0})]
    )


# --- bandit ---


def test_bandit_split_sums_to_one_and_respects_the_floor():
    undecided = [Arm(label="a", trials=2000, successes=40), Arm(label="b", trials=2000, successes=60),
                 Arm(label="c", trials=2000, successes=20)]  # fmt: skip
    split = allocate(undecided)
    assert sum(split.values()) == pytest.approx(1)
    assert split["c"] == pytest.approx(0.10) and split["b"] > split["a"] >= 0.10

    assert allocate(undecided, floor=0)["c"] < 0.01
    assert allocate(undecided, decided_winner="b") == {"a": 0.0, "b": 1.0, "c": 0.0}
    even = allocate([Arm(label="a", trials=0, successes=0), Arm(label="b", trials=0, successes=0)])
    assert even["a"] == pytest.approx(0.5, abs=0.03)
    with pytest.raises(ValueError, match="floor is too high"):
        allocate(undecided, floor=0.4)
    with pytest.raises(ValueError, match="not one of the variants"):
        allocate(undecided, decided_winner="z")


# --- simulations with known true rates ---


def test_false_winner_rate_between_identical_variants_stays_under_five_percent():
    two = winner_rates([0.02, 0.02], per_variant=8000, experiments=1000, seed=1)
    three = winner_rates([0.02, 0.02, 0.02], per_variant=8000, experiments=600, seed=2)
    assert two["false_winner"] < 0.05 and three["false_winner"] < 0.05
    assert two["true_winner"] == 0


def test_a_real_difference_is_usually_found_and_rarely_the_wrong_way():
    planned = sample_size(0.02, 0.4)
    result = winner_rates([0.02, 0.028], per_variant=planned, experiments=400, seed=3)
    assert result["true_winner"] > 0.65 and result["false_winner"] < 0.01


def test_bandit_loses_less_than_an_even_split():
    regret = bandit_regret([0.012, 0.019, 0.012], weeks=8, weekly_budget=10_000, runs=60, seed=4)
    bandit, even = regret["bandit"], regret["even_split"]
    assert bandit[0] == pytest.approx(even[0], rel=0.01)  # week 1 is an even split for both
    assert bandit[-1] < 0.6 * even[-1]
    assert all(later >= earlier for earlier, later in zip(bandit, bandit[1:], strict=False))
