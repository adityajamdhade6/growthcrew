import json

import pytest
from evals import metrics, suites
from evals.golden.brands import BRANDS, LOOMHOUSE, research_fixture
from evals.run import run, scorecard_markdown
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from growthcrew import guardrails, workflow
from growthcrew.agents.content import ContentAgent
from growthcrew.agents.strategist import build_evidence
from growthcrew.api.main import app, engine_dep
from growthcrew.brain.models import Competitor
from growthcrew.db.models import Cycle, Draft, GuardrailBlock, LLMCall
from growthcrew.reports.costs import cost_summary
from test_content import REQUEST, critique, linkedin, llm_with
from test_content import strategy as strategy  # noqa: F401  (fixture)
from test_strategy import BRAND


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


# --- guardrails ---


def rules(text, brand=LOOMHOUSE, facts=""):
    return [v.rule for v in guardrails.check([text], brand, facts)]


def test_guardrail_rules():
    assert rules("Our sheets cure insomnia.") == ["sensitive_claim"]
    assert rules("Brightside Linen is a scam.") == ["competitor_defamation"]
    assert rules("Unlike Brightside Linen, ours is stonewashed.") == []
    assert rules("Pure luxury.") == ["off_brand"]
    assert rules("Loved by 97% of sleepers.") == ["invented_proof"]
    assert rules("Sheet sets from $189. Free returns.", facts="from $189; duvet covers") == []


def test_a_piece_that_still_breaks_a_guardrail_is_blocked_and_logged(strategy):  # noqa: F811
    brand = BRAND.model_copy(deep=True)
    brand.competitors = [Competitor(name="Crumb & Co")]
    bad = linkedin("Crumb & Co is a scam")
    llm, _, engine = llm_with([bad, critique(9)] * 3)
    [record], _ = ContentAgent(llm).produce(REQUEST, brand, strategy)

    assert record.blocked and not record.passed
    # The guardrail finding capped the critic's accuracy score and reached the writer.
    assert record.versions[0].critique.scores()["accuracy"] == 3
    with Session(engine) as session:
        blocks = session.exec(select(GuardrailBlock)).all()
    assert len(blocks) == 3 and {b.rule for b in blocks} == {"competitor_defamation"}
    assert blocks[0].stage == "draft" and "Crumb & Co" in blocks[0].reason


def test_a_blocked_draft_cannot_be_approved_only_rejected_or_fixed(engine, tmp_path):
    with Session(engine) as session:
        cycle = Cycle(workspace="acme", week_start=workflow.datetime.now(workflow.UTC))
        session.add(cycle)
        session.flush()
        draft = Draft(
            cycle_id=cycle.id, workspace="acme", piece_id="p1", content_type="linkedin_post",
            original_text="Our bread cures insomnia.", text="Our bread cures insomnia.",
            body_json="{}", metadata_json="{}", min_score=9, passed_critic=False, status="blocked",
        )  # fmt: skip
        session.add(draft)
        session.commit()
        draft_id = draft.id

    with pytest.raises(workflow.WorkflowError, match="blocked by a guardrail"):
        workflow.decide(engine, draft_id, "approved", "adi", root=tmp_path)
    with pytest.raises(workflow.WorkflowError, match="still breaks a guardrail"):
        workflow.decide(engine, draft_id, "edited", "adi", "", "It cures insomnia!", root=tmp_path)
    approval, _ = workflow.decide(
        engine, draft_id, "edited", "adi", "Removed the claim", "Our bread is baked at 4am.",
        root=tmp_path,
    )  # fmt: skip
    assert approval.decision == "edited"
    with Session(engine) as session:
        assert session.get(Draft, draft_id).status == "approved"
        [block] = session.exec(select(GuardrailBlock)).all()
    assert block.stage == "edit"


# --- eval metrics ---


def test_reading_grade_separates_plain_from_dense_text():
    plain = "We bake bread. We bring it to you. It is still warm."
    dense = (
        "Notwithstanding organisational considerations, comprehensive implementation of "
        "multidimensional optimisation methodologies necessitates extraordinary coordination."
    )
    assert metrics.reading_grade(plain) < 4 < 12 < metrics.reading_grade(dense)


def test_judge_agreement_metrics():
    human = [2, 4, 5, 7, 8, 9, 3, 6, 8, 10]
    close = [3, 4, 6, 7, 8, 9, 2, 6, 9, 9]
    result = metrics.agreement(close, human)
    assert result["spearman"] > 0.9 and result["within_1_point"] == 1.0
    assert result["pass_fail_kappa"] == 1.0
    opposite = metrics.agreement([11 - h for h in human], human)
    assert opposite["spearman"] < -0.9 and opposite["within_1_point"] < 0.5


# --- eval suites and runner ---


def test_golden_fixtures_are_well_formed():
    result = suites.golden_dataset()
    assert result.metrics["brands"] == 3 and result.metrics["requests"] == 30
    assert result.status in ("pending", "pass")
    for brand in BRANDS.values():
        evidence = build_evidence(brand, research_fixture(brand))
        assert any(item.id == "learning:1" for item in evidence)
        assert all(meta.status == "confirmed" for meta in brand.fields.values())


def test_offline_evals_pass_and_never_report_unrun_evals_as_passed():
    results, cost = run(live=False, limit=None)
    by_name = {result.name: result for result in results}
    assert cost is None
    assert not [r.name for r in results if r.status == "fail"]
    for name in ("analyst_winner_detection", "analyst_end_to_end", "guardrails"):
        assert by_name[name].status == "pass"
    for name in ("strategy", "content", "research_citations"):
        assert by_name[name].status == "skipped"
    assert by_name["analyst_winner_detection"].metrics["winner_called_on_tiny_sample"] == 0

    text = scorecard_markdown(results, live=False, cost=None)
    assert "| analyst_winner_detection | analyst | PASS |" in text
    assert "| content | content | SKIPPED |" in text


def test_strategy_completeness_checks(strategy):  # noqa: F811
    checks = suites.completeness(strategy)
    assert checks["funnel_has_4_stages_in_order"] and checks["budget_split_sums_to_100"]
    assert checks["five_experiments_fully_specified"] and checks["experiments_ranked_by_ice"]
    assert not checks["every_pillar_has_a_proof_point"]  # the fixture's pillars have none


# --- cost dashboard ---


def test_cost_dashboard_prices_agents_weeks_and_pieces(engine):
    with Session(engine) as session:
        for agent, tag, cost in [
            ("research", None, 1.00),
            ("content", "linkedin_post|01-day02-linkedin_post", 0.20),
            ("critic", "linkedin_post|01-day02-linkedin_post", 0.05),
            ("content", "blog_article|02-day03-blog_article", 0.75),
        ]:
            session.add(LLMCall(agent=agent, workspace="acme", tag=tag, model="m",
                                input_tokens=100, output_tokens=50, cost_usd=cost))  # fmt: skip
        session.commit()

    data = cost_summary(engine, "acme")
    assert data["total_cost_usd"] == 2.0
    assert [row["agent"] for row in data["by_agent"]] == ["research", "content", "critic"]
    [week] = data["by_week"]
    assert (week["pieces"], week["fully_loaded_cost_per_piece_usd"]) == (2, 1.0)
    types = {row["content_type"]: row for row in data["by_content_type"]}
    assert types["linkedin_post"]["avg_cost_usd"] == 0.25
    # No comparison is made until the owner supplies quotes they have actually received.
    assert types["blog_article"]["freelancer_low_usd"] is None

    app.dependency_overrides[engine_dep] = lambda: engine
    try:
        client = TestClient(app)
        page = client.get("/costs/dashboard", params={"workspace": "acme"})
        api = client.get("/costs/summary.json", params={"workspace": "acme"}).json()
        blocks = client.get("/workspaces/acme/guardrail-blocks").json()
    finally:
        app.dependency_overrides.pop(engine_dep, None)
    assert page.status_code == 200 and "$2.00" in page.text and "cheaper" not in page.text
    assert json.dumps(api) == json.dumps(data) and blocks == []
