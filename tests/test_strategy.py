from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, create_engine
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew.agents.research_models import (
    Learning,
    MarketSignals,
    Quote,
    QuoteTheme,
    ResearchBrief,
    ResearchReport,
    VoiceOfCustomer,
)
from growthcrew.agents.strategist import StrategistAgent, StrategyInput, build_evidence
from growthcrew.agents.strategy_models import (
    KPI,
    Change,
    ContentPillar,
    Critique,
    CritiquePoint,
    ICPPriority,
    Priorities,
    RevisionLog,
)
from growthcrew.brain.models import ICP, Brain, BusinessProfile, FieldMeta
from growthcrew.frameworks import FRAMEWORKS, Point
from growthcrew.frameworks.funnel import ChannelPlay, FunnelPlan, FunnelStage, StageKPI
from growthcrew.frameworks.jtbd import Job, JobsToBeDone
from growthcrew.frameworks.messaging_house import MessagingHouse, Pillar
from growthcrew.frameworks.positioning import Positioning
from growthcrew.frameworks.test_and_learn import Experiment, TestPlan
from growthcrew.llm import LLM
from growthcrew.reports.strategy import save_strategy, to_markdown
from growthcrew.schemas import Claim

URL = "https://rival.test/pricing"
USAGE = SimpleNamespace(
    input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
)

BRAND = Brain(
    workspace="acme",
    business=BusinessProfile(name="Acme Bakery", what_they_sell="Sourdough subscriptions"),
    icp=ICP(pains=["No time to bake"]),
    fields={
        "business.what_they_sell": FieldMeta(status="confirmed", confidence="high"),
        "icp.pains": FieldMeta(confidence="low"),
    },
)
RESEARCH = ResearchReport(
    workspace="acme",
    focus="",
    teardowns=[],
    voice_of_customer=VoiceOfCustomer(
        top_pains=[Claim(statement="Supermarket bread goes stale", source_url=URL)],
        desired_outcomes=[],
        objections=[],
        themes=[
            QuoteTheme(
                theme="Freshness", quotes=[Quote(text="stale by Tuesday", source_url=URL)]
            )
        ],
    ),
    market_signals=MarketSignals(trends=[], seasonality=[], search_demand_themes=[]),
    brief=ResearchBrief(
        headline="Freshness wins",
        learnings=[Learning(insight="Freshness is the top pain", why_it_matters="Lead with it",
                            confidence="medium", source_urls=[URL])],
        open_questions=[],
    ),
)  # fmt: skip


def point(text, *support):
    return Point(text=text, support=list(support))


def experiment(name, impact, confidence, ease, *support):
    return Experiment(
        name=name, hypothesis="If we X, then Y will rise because Z", metric="signups",
        minimum_sample="200 visitors", decision_rule="Ship on win; drop on loss; extend if unclear",
        impact=impact, confidence=confidence, ease=ease, support=list(support),
    )  # fmt: skip


def sections(budget, experiments, category_support):
    """One response per framework, in order, then the priorities."""
    return [
        JobsToBeDone(
            functional_jobs=[Job(statement="When the week starts, I want fresh bread, so I can "
                                 "skip the shop", support=["voc:1"])],
            emotional_jobs=[], social_jobs=[],
        ),
        Positioning(
            competitive_alternatives=[point("Supermarket bread", "claim:1")],
            unique_attributes=[point("Baked the same morning", "brain:business.what_they_sell")],
            value=[point("Bread that is still fresh midweek", "voc:1")],
            target_customers=[point("Busy households", "brain:icp.pains")],
            market_category=point("Bread subscription", *category_support),
            positioning_statement="For busy households, Acme is the bread subscription that "
            "arrives fresh.",
        ),
        MessagingHouse(
            core_message=point("Fresh bread without the trip", "learning:1"),
            pillars=[Pillar(message=f"Pillar {n}", support=["learning:1"], proof_points=[])
                     for n in range(3)],
        ),
        FunnelPlan(stages=[
            FunnelStage(stage=stage, objective="Objective", kpis=[StageKPI(metric="m", target="t")],
                        channels=[ChannelPlay(channel="Instagram", tactic="Reels",
                                              support=["voc:1"], timing="all 90 days",
                                              budget_pct=share)])
            for stage, share in zip(("awareness", "consideration", "conversion", "retention"),
                                    budget, strict=True)
        ]),
        TestPlan(experiments=experiments),
        Priorities(
            icp_priorities=[ICPPriority(segment="Busy households", rationale="r",
                                        support=["brain:icp.pains"])],
            content_pillars=[ContentPillar(name="Freshness", description="d",
                                           example_topics=["How long bread lasts"],
                                           support=["voc:1"])],
            kpis=[KPI(metric="Subscribers", baseline="unknown, measure in days 1-30",
                      target="+20%", timeframe="90 days", support=["learning:1"])],
        ),
    ]  # fmt: skip


FIVE = [experiment(f"E{n}", n, n, n, "learning:1") for n in (3, 9, 5, 7, 1)]
DRAFT = sections((40, 30, 10, 10), FIVE[:4], ["claim:999"])
FINAL = sections((40, 30, 20, 10), FIVE, ["claim:1"])
CRITIQUE = Critique(
    summary="The plan leans on an inferred pain.",
    weak_assumptions=[CritiquePoint(section="positioning.target_customers",
                                    issue="Rests on an inferred, low-confidence pain",
                                    why_it_matters="The whole plan targets this segment",
                                    suggested_fix="Test it first", severity="high")],
    missing_risks=[],
)  # fmt: skip
REVISION = RevisionLog(changes=[Change(
    critique_issue="Rests on an inferred, low-confidence pain", decision="accepted",
    change_made="Added an experiment to validate the segment", reason="The evidence is thin",
)])  # fmt: skip


class ScriptedClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []
        messages = SimpleNamespace(parse=self._send, create=self._send)
        self.messages = messages
        self.beta = SimpleNamespace(messages=messages)

    def _send(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
            parsed_output=self.outputs.pop(0), model="claude-opus-5-5", usage=USAGE,
        )  # fmt: skip


@pytest.fixture
def result():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    client = ScriptedClient([*DRAFT, CRITIQUE, REVISION, *FINAL])
    llm = LLM(client=client, engine=engine, wait=wait_none())
    doc = StrategistAgent(llm).run(StrategyInput(brand=BRAND, research=RESEARCH))
    return doc, client


def test_frameworks_are_complete_templates():
    assert [fw.key for fw in FRAMEWORKS] == [
        "jtbd", "positioning", "messaging_house", "funnel", "test_and_learn",
    ]  # fmt: skip
    for fw in FRAMEWORKS:
        prompt = fw.prompt()
        assert fw.purpose in prompt and fw.instructions in prompt
        assert all(criterion in prompt for criterion in fw.quality_criteria)
        assert fw.inputs and fw.output_model.model_fields


def test_evidence_ids_cover_brain_and_research():
    evidence = {item.id: item for item in build_evidence(BRAND, RESEARCH)}
    assert evidence["brain:business.what_they_sell"].quality == "confirmed"
    assert evidence["brain:icp.pains"].quality == "inferred, low confidence"
    assert evidence["learning:1"].source_url == URL
    assert "stale by Tuesday" in evidence["voc:1"].text
    assert "claim:1" in evidence and "brain:business.pricing" not in evidence


def test_draft_problems_are_sent_to_the_revision(result):
    doc, client = result
    # Request 7 is the devil's advocate, in a fresh context with the draft and criteria.
    critic = client.requests[6]
    assert len(critic["messages"]) == 1
    assert "exactly five experiments" in critic["messages"][0]["content"]
    # Request 8 asks the strategist to respond, and includes what code checks found.
    revise = client.requests[7]["messages"][-1]["content"][-1]["text"]
    assert "Rests on an inferred, low-confidence pain" in revise
    assert "No valid supporting evidence: positioning.market_category" in revise
    assert "sums to 90%" in revise
    assert "Expected 5 experiments, got 4" in revise
    assert doc.first_draft.positioning.market_category.support == []


def test_final_doc_is_checked_ranked_and_pending(result):
    doc, _ = result
    assert doc.issues == []
    assert doc.status == "pending_approval"
    assert [e.name for e in doc.experiments] == ["E9", "E7", "E5", "E3", "E1"]
    assert doc.experiments[0].ice == 9.0
    assert doc.critique == CRITIQUE and doc.revision == REVISION


def test_report_renders_markdown_and_pdf(result, tmp_path):
    doc, _ = result
    text = to_markdown(doc)
    for heading in ("## Positioning", "## Messaging house", "## 90-day channel plan",
                    "## Experiments, ranked by ICE", "## KPIs", "## Devil's advocate review",
                    "### What changed in response", "## Evidence cited"):  # fmt: skip
        assert heading in text
    assert "`[brain:icp.pains]`" in text
    assert "`brain:icp.pains` (inferred, low confidence)" in text

    report = save_strategy(doc, tmp_path)
    assert report.suffix == ".md"
    [pdf] = (tmp_path / "acme" / "strategy").glob("*.pdf")
    assert pdf.read_bytes().startswith(b"%PDF") and pdf.stat().st_size > 2000
