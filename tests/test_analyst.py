import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from growthcrew.agents.analyst import AnalystAgent
from growthcrew.agents.content_models import BatchPlan, PlannedItem
from growthcrew.agents.learning_models import (
    Finding,
    LearningsDraft,
    ProposedChange,
    Ruling,
    Rulings,
    TargetStatus,
)
from growthcrew.agents.orchestrator import Orchestrator
from growthcrew.agents.strategist import StrategistAgent, StrategyInput
from growthcrew.analytics.analysis import analyze
from growthcrew.analytics.ingest import ingest, parse_csv
from growthcrew.analytics.simulate import seed
from growthcrew.analytics.stats import Arm, compare, two_proportion_z
from growthcrew.api.main import app, engine_dep
from growthcrew.brain.store import save_brain
from growthcrew.db.models import ChangeDecision, Cycle, Learnings, Task
from test_content import REQUEST, llm_with
from test_cycle import Stubs
from test_strategy import BRAND, CRITIQUE, DRAFT, FINAL, RESEARCH, REVISION


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def analysis(engine):
    results = {
        source: ingest(engine, "acme", source, text) for source, text in seed(engine).items()
    }
    return analyze(engine, "acme"), results


def readout(analysis, name):
    return next(r for r in analysis.readouts if r.name == name)


# --- statistics ---


def test_two_proportion_z_test_matches_a_known_value():
    z, p = two_proportion_z(Arm(label="a", trials=1000, successes=80),
                            Arm(label="b", trials=1000, successes=50))  # fmt: skip
    assert z == pytest.approx(2.721, abs=0.01) and p == pytest.approx(0.0065, abs=0.0005)


def test_no_winner_on_tiny_samples_however_big_the_gap():
    result = compare(
        [Arm(label="a", trials=20, successes=10), Arm(label="b", trials=20, successes=2)]
    )
    assert result.status == "not_enough_data" and result.winner is None


def test_no_winner_when_the_difference_could_be_chance():
    result = compare(
        [Arm(label="a", trials=1000, successes=52), Arm(label="b", trials=1000, successes=50)]
    )
    assert result.status == "no_significant_difference" and result.winner is None


def test_three_arms_are_bonferroni_corrected():
    arms = [Arm(label="a", trials=1000, successes=72), Arm(label="b", trials=1000, successes=50)]
    assert compare(arms).status == "significant"  # p about 0.04 for two arms
    third = Arm(label="c", trials=1000, successes=40)
    assert compare([*arms, third]).status == "no_significant_difference"  # doubled p > 0.05


# --- ingest ---


def test_export_headers_and_numbers_are_normalised():
    [row] = parse_csv('Ad name,Impr.,Link clicks,Amount spent (USD)\nSpring,"1,200",30,$12.50\n')
    assert (row["refs"], row["impressions"], row["clicks"], row["spend"]) == (
        ["Spring"],
        1200,
        30,
        12.5,
    )


def test_rows_are_matched_to_pieces_or_reported_unmatched(analysis):
    _, results = analysis
    assert (results["linkedin"].matched, results["ads"].matched) == (6, 21)  # by URL, by key
    assert results["email"].unmatched_refs == ["Black Friday teaser"]
    assert results["gsc"].matched == 0


def test_reuploading_an_export_replaces_rows(engine):
    exports = seed(engine)
    ingest(engine, "acme", "linkedin", exports["linkedin"])
    ingest(engine, "acme", "linkedin", exports["linkedin"])
    assert analyze(engine, "acme").rows_used == 6


# --- the simulated week: one angle clearly wins ---


def test_the_winning_angle_is_detected(analysis):
    result, _ = analysis
    ad = readout(result, "07-day03-ad")
    assert (ad.kind, ad.status, ad.winner) == ("ab_test", "significant", "outcome")
    assert ad.p_value < 0.001 and ad.lift_pct > 30

    posts = readout(result, "linkedin_post by angle")
    assert (posts.kind, posts.status, posts.winner) == ("observational", "significant", "outcome")

    angles = {
        (row.value, row.metric): row for row in result.performance if row.dimension == "angle"
    }
    assert (
        angles[("outcome", "click-through rate")].rate_pct
        > 1.5 * angles[("pain", "click-through rate")].rate_pct
    )
    assert {row.dimension for row in result.performance} == {"pillar", "angle", "format", "channel"}


def test_the_small_test_is_not_called_even_though_one_variant_looks_twice_as_good(analysis):
    result, _ = analysis
    hero = readout(result, "08-day04-landing_hero")
    assert hero.status == "not_enough_data" and hero.winner is None
    assert "needs at least 100 trials" in hero.note


def test_the_traffic_spike_is_flagged(analysis):
    result, _ = analysis
    [anomaly] = result.anomalies
    assert (anomaly.series, anomaly.date, anomaly.direction) == (
        "gsc impressions",
        "2026-09-24",
        "spike",
    )


# --- analyst -> strategist -> next week's plan ---


def learnings_from_model(result):
    ad, hero = readout(result, "07-day03-ad").id, readout(result, "08-day04-landing_hero").id
    posts = readout(result, "linkedin_post by angle").id

    def change(text, angle, content_type, share, evidence):
        return ProposedChange(
            change=text, rationale="r", evidence=evidence, expected_effect="e",
            prefer_angle=angle, content_type=content_type, share_pct=share,
        )  # fmt: skip

    return LearningsDraft(
        what_worked=[
            Finding(statement="Outcome ads won", evidence=[ad], confidence="high"),
            Finding(statement="Outcome posts led", evidence=[posts, "r99"], confidence="high"),
            # The model over-reads the tiny landing page test.
            Finding(statement="The pain hero converts twice as well", evidence=[hero],
                    confidence="high"),
        ],
        what_didnt=[],
        anomaly_notes=[],
        vs_targets=[TargetStatus(kpi="Subscribers", target="+20%", actual="not measured",
                                 status="no_data", note="No subscriber data uploaded")],
        changes=[
            change("Shift 40% of LinkedIn posts to the outcome angle", "outcome", "linkedin_post",
                   40, [posts]),
            change("Make the pain hero the default", "pain", "landing_hero", 100, [hero]),
            change("Keep the hero test running to 100 sessions per variant", "none", "any", 0,
                   [hero]),
        ],
    )  # fmt: skip


class LearningStubs(Stubs):
    def __init__(self, engine, root, strategy, reviewer):
        super().__init__(engine, root, strategy)
        self.reviewer = reviewer

    def review_changes(self, learnings, strategy, workspace=None):
        return self.reviewer.review_changes(learnings, strategy, workspace)

    def plan_batch(self, brand, strategy, weeks=1, max_items=5):
        items = [
            PlannedItem(day=d, request=REQUEST.model_copy(), rationale="r") for d in range(1, 6)
        ]
        return BatchPlan(items=items)


@pytest.fixture
def loop(engine, analysis, tmp_path):
    result, _ = analysis
    strategy = StrategistAgent(llm_with([*DRAFT, CRITIQUE, REVISION, *FINAL])[0]).run(
        StrategyInput(brand=BRAND, research=RESEARCH)
    )
    # The analyst model's output, then the strategist model accepting everything.
    accept_all = Rulings(rulings=[
        Ruling(change_id=f"c{n}", decision="accepted", reason="Supported by the data")
        for n in (1, 2, 3)
    ])  # fmt: skip
    llm, _, _ = llm_with([learnings_from_model(result), accept_all])
    llm.engine = engine
    learnings = AnalystAgent(llm, root=tmp_path).run("acme", strategy, engine)

    save_brain(BRAND, note="test", root=tmp_path, engine=engine)
    stubs = LearningStubs(engine, tmp_path, strategy, StrategistAgent(llm))
    orchestrator = Orchestrator(
        engine=engine, root=tmp_path, research=stubs, strategist=stubs, content=stubs
    )
    cycle = orchestrator.run(orchestrator.start_cycle("acme").id)
    return learnings, cycle


def test_code_corrects_what_the_numbers_do_not_support(loop):
    learnings, _ = loop
    assert [finding.confidence for finding in learnings.what_worked] == ["high", "medium", "low"]
    assert "r99" not in learnings.what_worked[1].evidence
    shift, blocked, keep = learnings.changes
    assert [change.id for change in learnings.changes] == ["c1", "c2", "c3"]
    assert not shift.blocked_reason and not keep.blocked_reason
    assert "No significant readout shows 'pain' winning" in blocked.blocked_reason
    assert learnings.issues == [f"c2: {blocked.blocked_reason}"]


def test_strategist_rulings_are_logged_and_a_blocked_change_cannot_be_accepted(loop, engine):
    _, cycle = loop
    with Session(engine) as session:
        decisions = session.exec(select(ChangeDecision).order_by(ChangeDecision.id)).all()
        assert session.exec(select(Learnings)).one().reviewed is True
    assert [d.decision for d in decisions] == ["accepted", "rejected", "accepted"]
    assert decisions[1].reason.startswith("Overruled by the statistical check")
    assert all(d.cycle_id == cycle.id and d.reason for d in decisions)


def test_next_weeks_plan_shifts_toward_the_winning_angle(loop, engine):
    _, cycle = loop
    with Session(engine) as session:
        plan = BatchPlan.model_validate_json(session.get(Cycle, cycle.id).state_json)
        tasks = {t.stage: t for t in session.exec(select(Task).where(Task.cycle_id == cycle.id))}
    assert [item.request.angle for item in plan.items] == ["outcome", "outcome", None, None, None]
    assert "2 of 5 eligible pieces moved to the outcome angle" in tasks["content_plan"].detail
    assert "accepted 2 of 3 changes" in tasks["strategy_check"].detail


def test_learning_log_shows_what_changed_and_why(loop, engine):
    app.dependency_overrides[engine_dep] = lambda: engine
    try:
        client = TestClient(app)
        entries = client.get("/workspaces/acme/learning-log.json").json()
        page = client.get("/workspaces/acme/learning-log")
    finally:
        app.dependency_overrides.pop(engine_dep, None)

    week = next(entry for entry in entries if entry["kind"] == "learnings")
    assert [change["decision"] for change in week["changes"]] == [
        "accepted",
        "rejected",
        "accepted",
    ]
    assert {r["winner"] for r in week["significant"]} == {"outcome"}
    assert any(entry["kind"] == "strategy" for entry in entries)

    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "Shift 40% of LinkedIn posts to the outcome angle" in page.text
    assert "Overruled by the statistical check" in page.text
    assert "no winner yet" in page.text


def test_upload_endpoint_and_analysis_without_data(engine):
    app.dependency_overrides[engine_dep] = lambda: engine
    try:
        client = TestClient(app)
        csv_text = seed(engine)["email"]
        ok = client.post("/workspaces/acme/metrics/email", content=csv_text,
                         headers={"content-type": "text/csv"})  # fmt: skip
        bad = client.post("/workspaces/acme/metrics/tiktok", content=csv_text,
                          headers={"content-type": "text/csv"})  # fmt: skip
    finally:
        app.dependency_overrides.pop(engine_dep, None)
    assert ok.json()["matched"] == 1 and bad.status_code == 400
    assert json.loads(ok.text)["unmatched_refs"] == ["Black Friday teaser"]
