"""The ad test from the demo review: Outcome beat the runner-up by +54% and was rejected."""

import pytest

from growthcrew.agents.learning_models import ProposedChange, Ruling, WeeklyLearnings
from growthcrew.agents.strategist import decide_rulings
from growthcrew.analytics.analysis import Analysis, build_readout
from growthcrew.experiments import Arm, GuardrailData, GuardrailSpec, preregister
from growthcrew.naming import display, piece_name, plural

# CTR 1.09%, 1.85% and 1.20% on about 15k impressions each.
AD_ARMS = [
    Arm(label="pain", trials=14655, successes=160),
    Arm(label="outcome", trials=15184, successes=281),
    Arm(label="social_proof", trials=14483, successes=174),
]


def learnings(readout, **change):
    proposed = ProposedChange(
        id="c1", change="Move ad budget to the outcome hook", rationale="It won the A/B test",
        evidence=[readout.id], expected_effect="Lower cost per click",
        **{"prefer_angle": "outcome", "content_type": "ad", "share_pct": 50, **change},
    )  # fmt: skip
    analysis = Analysis(window_start=None, window_end=None, rows_used=1, unmatched_rows=0,
                        performance=[], readouts=[readout], anomalies=[])  # fmt: skip
    return WeeklyLearnings(workspace="acme", analysis=analysis, changes=[proposed],
                           what_worked=[], what_didnt=[], anomaly_notes=[], vs_targets=[])  # fmt: skip


CTR = "click-through rate"
REGISTERED = preregister(
    experiment="07-day03-ad", hypothesis="Outcome beats the other hooks", primary_metric=CTR,
    variants=["pain", "outcome", "social_proof"], baseline_rate=0.012,
    minimum_detectable_effect=0.4, guardrails=[GuardrailSpec(metric="cost per click")],
)  # fmt: skip


@pytest.fixture
def ad_test():
    readout = build_readout(1, "ab_test", "07-day03-ad", CTR, AD_ARMS, REGISTERED)
    assert (readout.status, readout.winner) == ("significant", "outcome")
    assert readout.lift_pct == pytest.approx(54, abs=1)
    return readout


def test_a_confident_winner_cannot_be_rejected_without_a_stated_risk(ad_test):
    rejected = Ruling(change_id="c1", decision="rejected",
                      reason="Ads are already A/B tested by angle each week")  # fmt: skip
    [ruling], [applied] = decide_rulings(learnings(ad_test), [rejected])
    assert ruling.decision == "accepted" and not ruling.confirm_next_week
    assert ruling.reason.startswith("Accepted by default: outcome won Day 3 ad (+54% over")
    assert "over 99.9% likely to be best" in ruling.reason
    assert "no specific risk was stated" in ruling.reason
    assert "already A/B tested" in ruling.reason  # the objection is kept on the record
    assert applied.share_pct == 50


def test_a_stated_risk_gives_a_partial_shift_to_confirm_next_week(ad_test):
    cautious = Ruling(change_id="c1", decision="rejected", reason="Too soon",
                      risk="the result rests on a single week of data")  # fmt: skip
    [ruling], [applied] = decide_rulings(learnings(ad_test), [cautious])
    assert ruling.decision == "accepted_partial" and ruling.confirm_next_week
    assert "single week of data" in ruling.reason and "Shifting 25% now" in ruling.reason
    assert applied.share_pct == 25

    # The same holds when the strategist accepts but names a risk.
    accepted = cautious.model_copy(update={"decision": "accepted"})
    assert decide_rulings(learnings(ad_test), [accepted])[0][0].decision == "accepted_partial"


def test_defaults_when_the_strategist_says_nothing_or_agrees(ad_test):
    assert decide_rulings(learnings(ad_test), [])[0][0].decision == "accepted"
    agreed = Ruling(change_id="c1", decision="accepted", reason="Clear result")
    [ruling], _ = decide_rulings(learnings(ad_test), [agreed])
    assert (ruling.decision, ruling.reason) == ("accepted", "Clear result")


def test_the_rule_does_not_rescue_changes_the_data_does_not_support(ad_test):
    # Shifting to an angle that did not win is an ordinary ruling, and stays rejected.
    rejected = Ruling(change_id="c1", decision="rejected", reason="Pain did not win")
    [ruling], _ = decide_rulings(learnings(ad_test, prefer_angle="pain"), [rejected])
    assert ruling.decision == "rejected"

    # A test still short of its planned sample has no winner to accept.
    small = [
        Arm(label="pain", trials=41, successes=4),
        Arm(label="outcome", trials=38, successes=2),
    ]
    plan = REGISTERED.model_copy(update={"experiment": "08-day04-landing_hero"})
    hero = build_readout(2, "ab_test", "08-day04-landing_hero", CTR, small, plan)
    [ruling], _ = decide_rulings(learnings(hero), [rejected])
    assert hero.status == "not_enough_data" and ruling.decision == "rejected"

    # The same numbers without a registration are descriptive only.
    loose = build_readout(3, "ab_test", "07-day03-ad", CTR, AD_ARMS)
    assert loose.status == "not_preregistered" and loose.winner is None
    assert decide_rulings(learnings(loose), [rejected])[0][0].decision == "rejected"


def test_a_winner_that_hurts_a_guardrail_is_held_for_a_person():
    # Outcome wins on click-through but costs far more per click than the control.
    costly = [
        GuardrailData(
            spec=REGISTERED.guardrails[0],
            values={"pain": 0.40, "outcome": 0.62, "social_proof": 0.41},
        )
    ]
    readout = build_readout(
        1, "ab_test", "07-day03-ad", CTR, AD_ARMS, REGISTERED, costly, bandit=True
    )
    assert readout.winner == "outcome" and len(readout.guardrail_flags) == 1
    assert "cost per click" in readout.guardrail_flags[0]
    # The bandit does not hand the whole budget to a flagged winner.
    assert readout.next_split["outcome"] < 1 and min(readout.next_split.values()) >= 0.1

    agreed = Ruling(change_id="c1", decision="accepted", reason="Clear result")
    [ruling], _ = decide_rulings(learnings(readout), [agreed])
    assert ruling.decision == "held" and "hurts a guardrail" in ruling.reason


def test_every_readout_carries_its_uncertainty(ad_test):
    u = ad_test.uncertainty
    assert u.leader == "outcome" and u.sample_size == 44322
    assert u.prob_best["outcome"] > 0.999 and sum(u.prob_best.values()) == pytest.approx(
        1, abs=0.001
    )
    assert u.expected_loss_pct["outcome"] < 0.1 < 20 < u.expected_loss_pct["pain"]
    # The point estimate is +54%; the interval is wide but clear of zero.
    assert 20 < u.lift_low_pct < 54 < u.lift_high_pct < 95

    small = [
        Arm(label="pain", trials=41, successes=4),
        Arm(label="outcome", trials=38, successes=2),
    ]
    unsure = build_readout(2, "ab_test", "x", CTR, small).uncertainty
    assert unsure.prob_best["pain"] < 0.9 and unsure.lift_low_pct < 0 < unsure.lift_high_pct


def test_names_people_read():
    assert piece_name("03-day03-newsletter") == "Day 3 newsletter"
    assert piece_name("01-day01-ad-pain") == "Day 1 ad (pain angle)"
    assert piece_name("07-day12-linkedin_post") == "Day 12 LinkedIn post"
    assert piece_name("something-else") == "Something-else"
    assert display("linkedin_post") == "LinkedIn post"
    assert display("linkedin_post by angle") == "LinkedIn post by angle"
    assert display("pending_approval") == "Needs approval"
    assert (plural(1, "model call"), plural(12, "model call")) == ("1 model call", "12 model calls")
    assert plural(1200, "token") == "1,200 tokens"


# --- fixes from the senior-engineer review of the experiment engine ---


def _engine():
    from sqlmodel import SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def test_a_verdict_at_the_planned_sample_is_final():
    """Re-analysing each week as data keeps arriving must not reopen a finished test."""
    from sqlmodel import Session, select

    from growthcrew.analytics.analysis import analyze
    from growthcrew.analytics.ingest import ingest
    from growthcrew.analytics.simulate import seed
    from growthcrew.db.models import PerformanceRow

    engine = _engine()
    exports = seed(engine)
    ingest(engine, "acme", "ads", exports["ads"])
    first = next(r for r in analyze(engine, "acme").readouts if r.name == "07-day03-ad")
    assert first.status == "significant" and first.winner == "outcome"

    # Later data would flip the picture: the pain variant suddenly gets thousands of clicks.
    with Session(engine) as session:
        row = session.exec(
            select(PerformanceRow).where(PerformanceRow.ref.contains("ad-pain"))
        ).first()
        row.clicks += 5000
        session.add(row)
        session.commit()
    again = next(r for r in analyze(engine, "acme").readouts if r.name == "07-day03-ad")
    assert again.winner == "outcome" and again.arms == first.arms


def test_registrations_are_made_once_and_plan_from_the_workspaces_own_history():
    from growthcrew import config
    from growthcrew.analytics import registry
    from growthcrew.analytics.ingest import ingest
    from growthcrew.analytics.simulate import seed
    from growthcrew.experiments import sample_size

    engine = _engine()
    variants = ["pain", "outcome", "social_proof"]
    plan = registry.register_variants(
        engine, "acme", 9, "01-day01-ad", "ad", variants, "Outcome wins"
    )
    assumed = config.EXPERIMENT_DEFAULTS["ad"][1]
    assert plan.baseline_rate == assumed and plan.primary_metric == CTR
    assert plan.planned_per_variant == sample_size(assumed, config.EXPERIMENT_MDE, variants=3)
    assert [g.metric for g in plan.guardrails] == ["cost per click"]

    # Registering again does not rewrite the plan, whatever is passed.
    again = registry.register_variants(
        engine, "acme", 9, "01-day01-ad", "ad", variants, "Changed my mind"
    )
    assert again.hypothesis == "Outcome wins"
    # Content types with no test defaults, and single pieces, are not registered.
    assert registry.register_variants(engine, "acme", 9, "x", "newsletter", variants, "h") is None
    assert registry.register_variants(engine, "acme", 9, "y", "ad", ["pain"], "h") is None

    # With enough history, the workspace's own ad click-through rate replaces the assumption.
    for source, text in seed(engine).items():
        ingest(engine, "acme", source, text)
    informed = registry.register_variants(engine, "acme", 10, "01-day01-ad", "ad", variants, "h")
    assert informed.baseline_rate == pytest.approx(615 / 44322, abs=0.001)  # the ad test's own rate
    assert informed.planned_per_variant < plan.planned_per_variant
