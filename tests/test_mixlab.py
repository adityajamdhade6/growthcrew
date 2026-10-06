"""Phase 12: the GrowthCrew side of the MixLab connection."""

import json

from sqlmodel import Session, SQLModel, create_engine

from growthcrew.db.models import ExperimentRegistration
from growthcrew.integrations import mixlab


def test_synthetic_split_sums_to_100_and_is_labelled():
    advice = mixlab.SyntheticMixLab().optimize_budget("acme", 3000)
    assert advice.synthetic
    assert abs(sum(s.share_pct for s in advice.shares) - 100) < 0.5
    assert all(s.low_pct <= s.share_pct <= s.high_pct for s in advice.shares)


def test_a_share_outside_the_interval_needs_an_explanation():
    advice = mixlab.BudgetAdvice(
        total=1000,
        source="MixLab",
        synthetic=False,
        shares=[
            mixlab.ChannelShare(channel="email", share_pct=40, low_pct=30, high_pct=50),
            mixlab.ChannelShare(channel="search", share_pct=60, low_pct=50, high_pct=70),
        ],
    )
    assert mixlab.review_split({"Email newsletter": 35, "Google search ads": 65}, advice) == []
    issues = mixlab.review_split({"Newsletter": 10, "SEO": 60, "Instagram": 30}, advice)
    assert len(issues) == 1 and "email" in issues[0] and "30-50%" in issues[0]


def test_mcp_client_reads_the_three_tools():
    answers = {
        "optimize_budget": {
            "shares": [{"channel": "email", "share_pct": 100, "low_pct": 90, "high_pct": 100}]
        },
        "get_channel_roi": {
            "channels": [{"channel": "email", "roi": 2, "roi_low": 1, "roi_high": 3}]
        },
        "get_response_curves": {"curves": {"email": [[0, 0], [100, 50]]}},
        "record_calibration": {"accepted": 1},
    }
    client = mixlab.MCPMixLab(lambda tool, args: answers[tool])
    assert not client.optimize_budget("acme", 500).synthetic
    assert client.get_channel_roi("acme")[0].roi == 2
    assert client.get_response_curves("acme")["email"][1] == (100, 50)
    assert client.send_calibration("acme", [{}]) == 1


def test_only_final_verdicts_are_sent_as_calibration():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    verdict = {
        "metric": "clicks",
        "status": "winner",
        "winner": "b",
        "uncertainty": {"lift_low_pct": 2.0, "lift_high_pct": 30.0, "sample_size": 4000},
    }
    with Session(engine) as session:
        session.add(
            ExperimentRegistration(
                workspace="acme",
                cycle_id=1,
                experiment="07-day03-ad",
                data="{}",
                verdict=json.dumps(verdict),
            )
        )
        session.add(
            ExperimentRegistration(workspace="acme", cycle_id=1, experiment="08-x-email", data="{}")
        )
        session.commit()
    results = mixlab.calibration_results(engine, "acme")
    assert [r["experiment"] for r in results] == ["07-day03-ad"]
    assert results[0]["channel"] == "ad" and results[0]["sample_size"] == 4000
