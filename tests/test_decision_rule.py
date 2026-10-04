"""The ad test from the demo review: Outcome beat the runner-up by +54% and was rejected."""

import pytest

from growthcrew.agents.learning_models import ProposedChange, Ruling, WeeklyLearnings
from growthcrew.agents.strategist import decide_rulings
from growthcrew.analytics.analysis import Analysis, _readout
from growthcrew.analytics.stats import Arm, uncertainty
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


@pytest.fixture
def ad_test():
    readout = _readout(1, "ab_test", "07-day03-ad", "click-through rate", AD_ARMS, "")
    assert (readout.status, readout.winner, readout.lift_pct) == ("significant", "outcome", 54.0)
    return readout


def test_a_confident_winner_cannot_be_rejected_without_a_stated_risk(ad_test):
    rejected = Ruling(change_id="c1", decision="rejected",
                      reason="Ads are already A/B tested by angle each week")  # fmt: skip
    [ruling], [applied] = decide_rulings(learnings(ad_test), [rejected])
    assert ruling.decision == "accepted" and not ruling.confirm_next_week
    assert ruling.reason.startswith("Accepted by default: outcome won Day 3 ad (+54.0% over")
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

    small = [
        Arm(label="pain", trials=41, successes=4),
        Arm(label="outcome", trials=38, successes=2),
    ]
    hero = _readout(2, "ab_test", "08-day04-landing_hero", "conversion rate", small, "")
    [ruling], _ = decide_rulings(learnings(hero), [rejected])
    assert hero.status == "not_enough_data" and ruling.decision == "rejected"


def test_every_readout_carries_its_uncertainty(ad_test):
    u = ad_test.uncertainty
    assert u.leader == "outcome" and u.sample_size == 44322
    assert u.prob_best["outcome"] > 0.999 and sum(u.prob_best.values()) == pytest.approx(
        1, abs=0.001
    )
    # The point estimate is +54%; the interval is wide but clear of zero.
    assert 20 < u.lift_low_pct < 54 < u.lift_high_pct < 95
    assert uncertainty(AD_ARMS) == u  # seeded, so repeatable

    small = [
        Arm(label="pain", trials=41, successes=4),
        Arm(label="outcome", trials=38, successes=2),
    ]
    unsure = uncertainty(small)
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
