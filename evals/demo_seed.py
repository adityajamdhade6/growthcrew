"""Create a demo workspace with sample data, for developing and reviewing the web app.

    uv run python -m evals.demo_seed

Everything it writes is invented: the brand (Loomhouse, from the eval fixtures), the strategy,
the drafts, their scores, the costs and the performance numbers. It needs no API key.
The demo login below is for local development only.
"""

import json
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from evals.golden.brands import LOOMHOUSE, research_fixture
from growthcrew import budget
from growthcrew.agents.analyst import check as check_learnings
from growthcrew.agents.learning_models import (
    Finding,
    LearningsDraft,
    ProposedChange,
    Ruling,
    TargetStatus,
    WeeklyLearnings,
)
from growthcrew.agents.strategist import build_evidence, check, decide_rulings
from growthcrew.agents.strategy_models import (
    KPI,
    Change,
    ContentPillar,
    Critique,
    CritiquePoint,
    ICPPriority,
    RevisionLog,
    StrategyCore,
    StrategyDoc,
)
from growthcrew.analytics.analysis import analyze
from growthcrew.analytics.ingest import ingest
from growthcrew.analytics.simulate import seed as seed_performance
from growthcrew.api.auth import create_user
from growthcrew.brain.models import FieldMeta
from growthcrew.brain.store import WORKSPACES_DIR, list_versions, save_brain
from growthcrew.db.models import (
    AgentRun,
    ChangeDecision,
    Cycle,
    Draft,
    Learnings,
    LLMCall,
    Task,
    User,
)
from growthcrew.db.session import get_engine
from growthcrew.frameworks import Point
from growthcrew.frameworks.funnel import ChannelPlay, FunnelPlan, FunnelStage, StageKPI
from growthcrew.frameworks.jtbd import Job, JobsToBeDone
from growthcrew.frameworks.messaging_house import MessagingHouse, Pillar
from growthcrew.frameworks.positioning import Positioning
from growthcrew.frameworks.test_and_learn import Experiment
from growthcrew.memory import playbook
from growthcrew.memory.miner import mine
from growthcrew.memory.simulate import run as simulate_memory
from growthcrew.monitor import signals
from growthcrew.reports.strategy import save_strategy

WORKSPACE = "demo-loomhouse"
DEMO_EMAIL = "demo@growthcrew.test"
DEMO_PASSWORD = "loomhouse-demo-2026"


def P(text: str, *support: str) -> Point:
    return Point(text=text, support=list(support))


def demo_strategy(brain, research) -> StrategyDoc:
    core = StrategyCore(
        jobs=JobsToBeDone(
            functional_jobs=[
                Job(
                    statement="When summer nights get warm, I want bedding that stays cool, so I can sleep through",
                    support=["voc:1", "brain:icp.pains"],
                )
            ],
            emotional_jobs=[
                Job(
                    statement="When I spend this much on sheets, I want to feel sure they will last, so I do not regret it",
                    support=["brain:icp.objections"],
                )
            ],
            social_jobs=[],
        ),
        positioning=Positioning(
            competitive_alternatives=[
                P("Cotton percale from a department store", "brain:icp.pains"),
                P("Cheaper linen that pills", "claim:2"),
            ],
            unique_attributes=[
                P("Stonewashed European flax", "brain:business.what_they_sell"),
                P("60-night free returns", "brain:business.pricing"),
            ],
            value=[
                P("Sleeps cooler than cotton", "voc:1"),
                P("Gets softer with every wash", "brain:proof.testimonials"),
            ],
            target_customers=[
                P("Hot sleepers aged 28 to 45 who own or rent long term", "brain:icp.demographics")
            ],
            market_category=P("Linen bedding, sold direct", "brain:business.one_liner"),
            positioning_statement="For hot sleepers who want bedding that lasts, Loomhouse is the stonewashed linen that sleeps cool from the first night and gets softer every wash.",
        ),
        messaging_house=MessagingHouse(
            core_message=P("Linen that sleeps cool tonight and softer every year.", "learning:1"),
            pillars=[
                Pillar(
                    message="Sleeps cooler",
                    support=["voc:1"],
                    proof_points=[
                        P(
                            '"First summer I have not kicked the covers off"',
                            "brain:proof.testimonials",
                        )
                    ],
                ),
                Pillar(
                    message="Softer every wash",
                    support=["brain:proof.testimonials"],
                    proof_points=[
                        P(
                            '"Softer after every wash, which I did not believe until the tenth"',
                            "brain:proof.testimonials",
                        )
                    ],
                ),
                Pillar(
                    message="Made to last",
                    support=["brain:icp.goals"],
                    proof_points=[P("4.8 out of 5 from 2,300 reviews", "brain:proof.stats")],
                ),
            ],
        ),
        channel_plan=FunnelPlan(
            stages=[
                FunnelStage(
                    stage="awareness",
                    objective="Reach hot sleepers before summer",
                    kpis=[
                        StageKPI(
                            metric="Ad click-through rate", target="establish baseline in days 1-30"
                        )
                    ],
                    channels=[
                        ChannelPlay(
                            channel="Meta ads",
                            tactic="Three-angle hook test on the sheet set",
                            timing="days 1-30",
                            budget_pct=35,
                            support=["voc:1"],
                        )
                    ],
                ),
                FunnelStage(
                    stage="consideration",
                    objective="Answer the wrinkle and price objections",
                    kpis=[StageKPI(metric="Blog to product click rate", target="5%")],
                    channels=[
                        ChannelPlay(
                            channel="Blog",
                            tactic="Linen vs cotton comparison article",
                            timing="days 1-30",
                            budget_pct=15,
                            support=["brain:icp.objections"],
                        )
                    ],
                ),
                FunnelStage(
                    stage="conversion",
                    objective="Turn product-page visitors into buyers",
                    kpis=[StageKPI(metric="Add-to-cart rate", target="baseline first, then +15%")],
                    channels=[
                        ChannelPlay(
                            channel="Landing page",
                            tactic="Hero test leading with the 60-night trial",
                            timing="days 31-60",
                            budget_pct=25,
                            support=["brain:business.pricing"],
                        )
                    ],
                ),
                FunnelStage(
                    stage="retention",
                    objective="Second purchase within 90 days",
                    kpis=[StageKPI(metric="Repeat purchase rate", target="baseline first")],
                    channels=[
                        ChannelPlay(
                            channel="Newsletter",
                            tactic="Care guide, then pillowcase offer",
                            timing="all 90 days",
                            budget_pct=25,
                            support=["brain:proof.testimonials"],
                        )
                    ],
                ),
            ]
        ),
        experiments=[
            Experiment(
                name="Ad angle test",
                hypothesis="If we lead with the outcome (cool sleep), then click-through will beat the pain and social-proof hooks because it is the most quoted benefit",
                metric="Click-through rate",
                minimum_sample="10,000 impressions per variant",
                decision_rule="Scale the winner if significant; rerun with new creative if not",
                impact=8,
                confidence=6,
                ease=8,
                support=["voc:1"],
            ),
            Experiment(
                name="Hero: trial first",
                hypothesis="If the hero leads with the 60-night trial, then add-to-cart will rise because price is the main objection",
                metric="Add-to-cart rate",
                minimum_sample="100 sessions per variant",
                decision_rule="Keep running until each variant has 100 sessions",
                impact=7,
                confidence=5,
                ease=7,
                support=["brain:icp.objections"],
            ),
            Experiment(
                name="Admit the wrinkles",
                hypothesis="If product copy admits wrinkles up front, then returns will fall because expectations are set",
                metric="Return rate",
                minimum_sample="200 orders",
                decision_rule="Adopt if returns fall by a fifth",
                impact=6,
                confidence=5,
                ease=9,
                support=["brain:icp.objections"],
            ),
            Experiment(
                name="Care guide email",
                hypothesis="If buyers get a care guide in week two, then repeat purchase will rise because softness is the payoff",
                metric="Repeat purchase rate",
                minimum_sample="300 recipients",
                decision_rule="Make it a standing flow if repeat rate rises",
                impact=5,
                confidence=5,
                ease=8,
                support=["brain:proof.testimonials"],
            ),
            Experiment(
                name="Registry landing page",
                hypothesis="If registry visitors see durability first, then registry adds will rise because gifts are bought to last",
                metric="Registry add rate",
                minimum_sample="150 sessions",
                decision_rule="Keep if adds rise; otherwise fold into the main page",
                impact=5,
                confidence=4,
                ease=6,
                support=["brain:icp.buying_triggers"],
            ),
        ],
        icp_priorities=[
            ICPPriority(
                segment="Hot sleepers, 28 to 45",
                rationale="The most quoted benefit and the clearest trigger (summer)",
                support=["voc:1", "brain:icp.buying_triggers"],
            ),
            ICPPriority(
                segment="Wedding registries",
                rationale="High order value, durability message fits",
                support=["brain:icp.buying_triggers"],
            ),
        ],
        content_pillars=[
            ContentPillar(
                name="Sleep cooler",
                description="Why linen breathes and what that feels like",
                example_topics=[
                    "Linen vs cotton for hot sleepers",
                    "What 'cool to the touch' means",
                ],
                support=["voc:1"],
            ),
            ContentPillar(
                name="Honest linen",
                description="Wrinkles, price and care, told straight",
                example_topics=["Why we tell you it wrinkles", "How to wash linen"],
                support=["brain:icp.objections"],
            ),
            ContentPillar(
                name="Made to last",
                description="Durability and how it softens",
                example_topics=["Year three with the same sheets"],
                support=["brain:proof.testimonials"],
            ),
        ],
        kpis=[
            KPI(
                metric="Ad click-through rate",
                baseline="unknown, measure in days 1-30",
                target="+25% on baseline",
                timeframe="90 days",
                support=["voc:1"],
            ),
            KPI(
                metric="Add-to-cart rate",
                baseline="unknown, measure in days 1-30",
                target="+15% on baseline",
                timeframe="90 days",
                support=["brain:icp.objections"],
            ),
            KPI(
                metric="Review average",
                baseline="4.8 out of 5",
                target="hold at 4.8",
                timeframe="90 days",
                support=["brain:proof.stats"],
            ),
        ],
    )
    evidence = build_evidence(brain, research)
    issues = check(core, {item.id for item in evidence})
    return StrategyDoc(
        **core.model_dump(), workspace=WORKSPACE, brand_name=brain.business.name, evidence=evidence,
        first_draft=core, issues=issues,
        critique=Critique(
            summary="Sound on the hot-sleeper segment; thin on proof for durability.",
            weak_assumptions=[CritiquePoint(section="messaging_house", issue="'Made to last' rests on a review average, not on durability evidence", why_it_matters="The pillar could read as an unsupported claim", suggested_fix="Lead that pillar with the softness testimonial until durability data exists", severity="medium")],
            missing_risks=[CritiquePoint(section="channel_plan", issue="35% of budget on one ad test before any baseline", why_it_matters="A weak first creative would burn a third of the budget", suggested_fix="Cap the first test at the minimum sample, then release the rest", severity="high")],
        ),
        revision=RevisionLog(changes=[
            Change(critique_issue="35% of budget on one ad test before any baseline", decision="accepted", change_made="Ad test now stops at 10,000 impressions per variant before more budget is released", reason="A fair risk for a small budget"),
            Change(critique_issue="'Made to last' rests on a review average", decision="partly_accepted", change_made="Kept the pillar; proof point is now labelled as a review score", reason="Durability is a real buying goal even though proof is thin"),
        ]),
    )  # fmt: skip


def _history(text: str, scores: dict, edits=(), violations=()) -> str:
    critique = {name: {"score": value, "reason": ""} for name, value in scores.items()}
    critique |= {"edits": list(edits), "code_findings": [], "passed": min(scores.values()) >= 8}
    meta = {"messaging_pillar": "Sleeps cooler", "target_persona": "Hot sleepers, 28 to 45",
            "cta": "Shop sheet sets",
            "hypothesis": "If we lead with cool sleep, then click-through will rise because it is the most quoted benefit"}  # fmt: skip
    version = {"round": 1, "metadata": meta, "body": {}, "text": text, "critique": critique,
               "guardrail_violations": list(violations)}  # fmt: skip
    first = {**version, "text": "Treat yourself to luxury linen. " + text} if edits else None
    return json.dumps([first, {**version, "round": 2}] if first else [version])


def seed_panel(engine) -> None:
    """A sample synthetic panel and its pre-test of the ad test, simulated for the demo."""
    import random

    from growthcrew.db.models import ExperimentRegistration, PanelRun, Persona
    from growthcrew.panel.pretest import aggregate, recommend

    with Session(engine) as session:
        test = session.exec(
            select(ExperimentRegistration).where(
                ExperimentRegistration.workspace == WORKSPACE,
                ExperimentRegistration.experiment == "07-day03-ad",
            )
        ).first()
    if test is None:
        return
    labels = json.loads(test.data)["variants"]
    people = [
        ("Maya", "Hot sleeper who wakes at 3am kicking off the duvet", "hot sleepers"),
        ("Tom", "New homeowner furnishing a guest room on a budget", "first home"),
        ("Priya", "Buys gifts for weddings from the registry", "gift buyers"),
        ("Dan", "Sceptical of 'luxury' bedding claims; reads every review", "sceptics"),
        ("Lena", "Wants natural fibres and cares where they are made", "natural fibres"),
        ("Sam", "Replaces sheets only when they wear through", "practical"),
    ]
    rng = random.Random(4)
    responses = []
    with Session(engine) as session:
        for name, summary, segment in people:
            profile = {"summary": f"Sample persona: {summary}", "segment": segment,
                       "demographics": [], "pains": [], "objections": [], "media_habits": [],
                       "phrases": []}  # fmt: skip
            session.add(Persona(workspace=WORKSPACE, generation=1, name=name,
                                profile=json.dumps(profile), support='["brain:icp.pains"]'))  # fmt: skip
            lean = {"outcome": 1.5, "social_proof": 0.5}
            responses.append([
                {"persona": name, "label": label, "position": i,
                 "stop": max(1, min(7, round(3.5 + lean.get(label, 0) + rng.gauss(0, 1.2)))),
                 "click": rng.random() < 0.25 + 0.15 * lean.get(label, 0),
                 "objection": "Sample: not sure linen is worth the price", "confusing": ""}
                for i, label in enumerate(labels)
            ])  # fmt: skip
        prediction = aggregate(responses, labels)
        session.add(PanelRun(workspace=WORKSPACE, cycle_id=test.cycle_id, experiment=test.experiment,
                             generation=1, responses=json.dumps(responses),
                             prediction=prediction.model_dump_json(),
                             recommendation=recommend(prediction, "untested").model_dump_json(),
                             trust="untested"))  # fmt: skip
        session.commit()


def seed_signals(engine) -> None:
    """Sample findings for the Signals inbox. Invented for the demo, like the brand itself."""
    sample = "Sample data: "
    findings = [
        signals.Finding(monitor="competitor", category="price_change",
                        title="Brightside Linen changed prices on its pricing page",
                        summary=f"{sample}Prices no longer shown: $179. New prices: $159.",
                        suggested_response="Check whether our pricing message still holds "
                        "against the new numbers.", base_importance=0.9,
                        sources=[signals.Source(url="https://brightside.example/pricing",
                                                date="2026-10-01")]),
        signals.Finding(monitor="competitor", category="positioning",
                        title="Cotton & Co's home page: new positioning",
                        summary=f"{sample}The headline moved from 'everyday cotton' to "
                        "'cooling sheets for hot sleepers', the audience Loomhouse targets.",
                        suggested_response="Lead with lived-in softness and the 60-night "
                        "trial, where Cotton & Co has no claim.", base_importance=0.8,
                        sources=[signals.Source(url="https://cottonco.example/",
                                                date="2026-10-02")]),
        signals.Finding(monitor="seo", category="keyword_gap",
                        title="Content gap: linen sheets for hot sleepers",
                        summary=f"{sample}3 keywords with 6,100 monthly searches where a "
                        "competitor ranks and we do not.",
                        suggested_response="Outline: Why linen sleeps cooler; Thread count "
                        "does not matter for linen; Washing and care", base_importance=0.7,
                        sources=[signals.Source(url="workspace://demo-loomhouse/seo/keywords.csv",
                                                date="2026-09-30")]),
        signals.Finding(monitor="social", category="question",
                        title="Does linen soften or stay scratchy?",
                        summary=f'{sample}"Bought linen once and it felt like a sack. Does it '
                        'actually get softer?"', base_importance=0.5,
                        sources=[signals.Source(url="workspace://demo-loomhouse/social/reddit.csv#4",
                                                date="2026-09-29")]),
    ]  # fmt: skip
    signals.file_findings(engine, WORKSPACE, findings)


def main() -> None:
    engine = get_engine()
    if list_versions(WORKSPACE):
        print(
            f"{WORKSPACE} already exists; delete workspaces/{WORKSPACE} and growthcrew.db to reseed"
        )
        return
    brain = LOOMHOUSE.model_copy(deep=True, update={"workspace": WORKSPACE})
    # Leave a few fields unconfirmed so the review screen has something to do.
    site = brain.source_url
    for path, confidence, note, page in (
        (
            "icp.objections",
            "low",
            "Implied by the questions the FAQ answers, not stated by customers",
            "/faq",
        ),
        (
            "icp.buying_triggers",
            "low",
            "Inferred from seasonal blog posts and the registry page",
            "/blog",
        ),
        (
            "competitors",
            "low",
            "Each is named once, on the linen comparison page",
            "/blog/linen-vs-cotton",
        ),
        (
            "business.geography",
            "medium",
            "Taken from the shipping page; no mention of other countries",
            "/shipping",
        ),
    ):
        brain.fields[path] = FieldMeta(
            status="inferred", confidence=confidence, note=note, source_urls=[f"{site}{page}"]
        )
    brain = save_brain(brain, note="demo seed", engine=engine)

    research = research_fixture(brain)
    folder = WORKSPACES_DIR / WORKSPACE / "research"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "demo.json").write_text(research.model_dump_json(indent=2))
    strategy = demo_strategy(brain, research)
    save_strategy(strategy, WORKSPACES_DIR, pdf=False)

    # Twelve earlier weeks of published LinkedIn posts, mined weekly: the brand's memory and
    # playbook. Simulated, with one pattern that is real and one that stops holding.
    simulate_memory(engine, workspace=WORKSPACE)

    # Last week: published pieces with performance data, analysed.
    for source, text in seed_performance(engine, workspace=WORKSPACE).items():
        ingest(engine, WORKSPACE, source, text)
    analysis = analyze(engine, WORKSPACE)
    # The weekly memory job, as it runs at the start of each cycle: remember last week's
    # pieces and re-test the playbook.
    mine(engine, WORKSPACE, as_of=datetime(2026, 9, 28, tzinfo=UTC))
    ad = next(r.id for r in analysis.readouts if r.name == "07-day03-ad")
    hero = next(r.id for r in analysis.readouts if r.name == "08-day04-landing_hero")
    posts = next(r.id for r in analysis.readouts if r.name == "linkedin_post by angle")
    changes = [
        ProposedChange(change="Move ad budget to the outcome hook", rationale="It won the pre-registered A/B test by a clear margin", evidence=[ad], expected_effect="Lower cost per click", prefer_angle="outcome", content_type="ad", share_pct=50),
        ProposedChange(change="Register an A/B test of the outcome angle on LinkedIn posts", rationale="Outcome posts drew about twice the click-through, but they were different posts, never a registered test", evidence=[posts], expected_effect="A result we can act on in two weeks", prefer_angle="none", content_type="linkedin_post", share_pct=0),
        ProposedChange(change="Keep the landing page test running to its planned sample", rationale="About 40 sessions per variant is far short of what was registered", evidence=[hero], expected_effect="A result we can trust", prefer_angle="none", content_type="any", share_pct=0),
    ]  # fmt: skip
    draft = LearningsDraft(
        changes=changes,
        what_worked=[Finding(statement="The outcome hook won the ad test: 1.85% click-through against 1.09% and 1.20%", evidence=[ad], confidence="high"),
                     Finding(statement="Outcome-angle LinkedIn posts drew about twice the click-through of the others, in an unregistered comparison", evidence=[posts], confidence="medium")],
        what_didnt=[Finding(statement="The landing page hero test has no result yet at about 40 sessions per variant", evidence=[hero], confidence="low")],
        anomaly_notes=[], vs_targets=[TargetStatus(kpi="Ad click-through rate", target="+25% on baseline", actual="1.85% for the winning hook", status="on_track", note="Baseline is this week")],
    )  # fmt: skip
    # The same code check the analyst's output goes through: ids, confidence caps, blocks.
    issues = check_learnings(draft, analysis)
    learnings = WeeklyLearnings(
        **draft.model_dump(), workspace=WORKSPACE, analysis=analysis, issues=issues
    )

    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        row = Learnings(workspace=WORKSPACE, data=learnings.model_dump_json(), reviewed=True)
        session.add(row)
        cycle = Cycle(workspace=WORKSPACE, week_start=budget.week_start(), stage="awaiting_approval",
                      created_at=now - timedelta(minutes=14))  # fmt: skip
        session.add(cycle)
        session.flush()
        # What the strategist model might say; the decision rule in code has the last word.
        said = [
            Ruling(
                change_id="c1",
                decision="rejected",
                reason="Ads are already A/B tested by angle each week",
                risk="the result rests on a single week of data",
            ),
            Ruling(
                change_id="c2",
                decision="accepted",
                reason="A lead worth a proper test before any shift",
            ),
            Ruling(
                change_id="c3",
                decision="accepted",
                reason="No winner should be called at this sample size",
            ),
        ]
        rulings, applied = decide_rulings(learnings, said)
        for ruling, change in zip(rulings, applied, strict=True):
            session.add(ChangeDecision(workspace=WORKSPACE, learnings_id=row.id, cycle_id=cycle.id,
                                       change_json=change.model_dump_json(),
                                       decision=ruling.decision, reason=ruling.reason))  # fmt: skip
        steps = [
            ("research", "done", "14 cited claims from 9 sources; 2 sources not seen in the previous research", [("research", 9, 61200, 5400, 0.41)]),
            ("strategy_check", "done", "Strategist accepted 3 of 3 changes from last week's learnings (1 in part, to confirm next week)", [("strategist", 1, 8300, 900, 0.05)]),
            ("content_plan", "done", "5 items planned: ad, LinkedIn post, newsletter, landing page hero, LinkedIn post", [("content", 1, 6100, 700, 0.04)]),
            ("drafting", "done", "7 pieces written in 11 writer and editor rounds", [("content", 11, 70400, 9800, 0.48), ("critic", 11, 52800, 6100, 0.33)]),
            ("critic", "done", "5 of 7 pieces scored 8 or more on every criterion; flagged for the reviewer: Day 3 newsletter; blocked by guardrails: Day 1 ad (pain angle)", [("orchestrator", 0, 0, 0, 0.0)]),
        ]  # fmt: skip
        for index, (stage, status, detail, runs) in enumerate(steps):
            started = cycle.created_at + timedelta(minutes=index * 2.5)
            task = Task(cycle_id=cycle.id, workspace=WORKSPACE, stage=stage, status=status,
                        detail=detail, started_at=started,
                        finished_at=started + timedelta(minutes=2, seconds=10))  # fmt: skip
            session.add(task)
            session.flush()
            for agent, calls, tokens_in, tokens_out, cost in runs:
                session.add(AgentRun(task_id=task.id, cycle_id=cycle.id, workspace=WORKSPACE,
                                     agent=agent, llm_calls=calls, input_tokens=tokens_in,
                                     output_tokens=tokens_out, cost_usd=cost))  # fmt: skip
                if calls:
                    session.add(LLMCall(agent=agent, workspace=WORKSPACE, model="claude-opus-5-5",
                                        input_tokens=tokens_in, output_tokens=tokens_out,
                                        cost_usd=cost, created_at=started))  # fmt: skip

        good = {
            "voice": 9,
            "clarity": 9,
            "persuasion": 8,
            "accuracy": 10,
            "channel_fit": 9,
            "ai_cliche": 9,
        }
        weak = {**good, "clarity": 6, "persuasion": 6}
        ad_text = "Hook: {hook}\nPrimary text: Linen stays cool to the touch. Sleep on it for 60 nights; send it back if you do not love it.\nHeadline: Linen sheets from $189"
        edit = {
            "line": 1,
            "criterion": "voice",
            "original": "Treat yourself to luxury linen.",
            "suggestion": "DELETE",
            "reason": "Uses two words the brand has banned",
        }
        violation = {
            "rule": "sensitive_claim",
            "line": 2,
            "excerpt": "cures insomnia",
            "reason": "Health claim that needs substantiation",
        }
        drafts = [
            ("01-day01-ad-pain", "ad", "pain", 1, "Hook: Kicked the covers off again?\nPrimary text: Our linen cures insomnia on hot nights.\nHeadline: Linen sheets from $189", {**good, "accuracy": 3}, (), (violation,), "blocked"),
            ("01-day01-ad-outcome", "ad", "outcome", 1, ad_text.format(hook="Sleep through a warm night"), good, (), (), "pending_approval"),
            ("01-day01-ad-social_proof", "ad", "social_proof", 1, ad_text.format(hook="\"First summer I have not kicked the covers off\""), good, (), (), "pending_approval"),
            ("02-day02-linkedin_post", "linkedin_post", "outcome", 2, "Linen wrinkles.\nWe put that on the product page, above the price.\nReturns dropped, because the people who mind wrinkles stopped buying and the people who like a lived-in bed stopped being surprised.\nTelling customers the drawback first is the cheapest thing we have done for our return rate.", good, (edit,), (), "pending_approval"),
            ("03-day03-newsletter", "newsletter", None, 3, "Subject: Start of summer: the case for linen\nPreview: Cooler nights, and one honest drawback.\nLinen is a good fabric and many people like it for a number of reasons.\nIt wrinkles. We think that is the point.\nSheet sets are from $189, with free returns for 60 nights.", weak, (), (), "pending_approval"),
            ("04-day04-landing_hero-outcome", "landing_hero", "outcome", 4, "Headline: Sleep cooler tonight\nSubheadline: Stonewashed linen sheets, woven from European flax. Try them for 60 nights.\nCTA button: Shop sheet sets\nPoint: Cool to the touch, even in July\nPoint: Softer after every wash\nSocial proof: 4.8 out of 5 from 2,300 reviews", good, (), (), "pending_approval"),
            ("05-day05-linkedin_post", "linkedin_post", None, 5, "Our flax is grown in Normandy and woven in Portugal.\nThen we wash it until it feels like it has been yours for years.\nThat last step is the one customers notice first.", good, (), (), "pending_approval"),
        ]  # fmt: skip
        for piece, kind, angle, day, text, scores, edits, violations, status in drafts:
            history = _history(text, scores, edits, violations)
            meta = json.loads(history)[-1]["metadata"]
            # What the writer would have been shown from memory for this piece.
            _, used = playbook.writer_context(
                engine, WORKSPACE, kind, f"{text[:120]} {meta['messaging_pillar']}"
            )
            session.add(Draft(cycle_id=cycle.id, workspace=WORKSPACE, piece_id=piece,
                              content_type=kind, angle=angle, day=day, original_text=text, text=text,
                              body_json="{}", metadata_json=json.dumps(meta),
                              min_score=min(scores.values()),
                              passed_critic=min(scores.values()) >= 8 and not violations,
                              history_json=history, status=status,
                              memory_json=used.model_dump_json(), prompt_version="p-sim00002",
                              strategy_version=1))  # fmt: skip
        if not session.exec(select(User).where(User.email == DEMO_EMAIL)).first():
            session.commit()
            # The public demo is read-only: a viewer on the sample workspace, never an admin.
            demo = create_user(engine, DEMO_EMAIL, DEMO_PASSWORD, WORKSPACE)
            with Session(engine) as fresh:
                row = fresh.get(User, demo.id)
                row.roles = json.dumps({WORKSPACE: "viewer"})
                fresh.add(row)
                fresh.commit()
        session.commit()
    seed_signals(engine)
    seed_panel(engine)
    print(f"Seeded {WORKSPACE}. Demo login: {DEMO_EMAIL} (password is DEMO_PASSWORD in this file)")


if __name__ == "__main__":
    main()
