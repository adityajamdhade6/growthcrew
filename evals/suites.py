"""The evals. Offline ones need no API key; live ones call the model and cost money."""

import csv
import json
import random
import statistics
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlmodel import SQLModel, create_engine

from evals import metrics
from evals.golden.brands import BRANDS, research_fixture
from growthcrew import guardrails
from growthcrew.agents.strategy_models import StrategyCore, StrategyDoc
from growthcrew.analytics.analysis import analyze
from growthcrew.analytics.ingest import ingest
from growthcrew.analytics.simulate import seed
from growthcrew.analytics.stats import Arm, compare
from growthcrew.content.checks import allowed_facts, cliche_hits, unverified_facts
from growthcrew.content.types import ContentRequest

HERE = Path(__file__).parent
Status = Literal["pass", "fail", "pending", "skipped"]


class EvalResult(BaseModel):
    name: str
    agent: str
    status: Status
    metrics: dict = {}
    thresholds: dict = {}
    notes: list[str] = []


def _load(path: str) -> dict:
    return json.loads((HERE / path).read_text())


# --- golden dataset ---


def golden_items() -> list[dict]:
    return _load("golden/requests.json")["items"]


def golden_dataset() -> EvalResult:
    items = golden_items()
    for item in items:
        ContentRequest.model_validate(item["request"])
    per_brand = {brand: sum(item["brand"] == brand for item in items) for brand in BRANDS}
    approved = sum(bool(item["approved_by"] and item["reference"]) for item in items)
    complete = all(count == 10 for count in per_brand.values()) and len(per_brand) == 3
    return EvalResult(
        name="golden_dataset",
        agent="dataset",
        status="fail" if not complete else ("pending" if approved < len(items) else "pass"),
        metrics={"brands": len(per_brand), "requests": len(items), "approved_references": approved},
        thresholds={"requests": "3 brands x 10", "approved_references": len(items)},
        notes=[]
        if approved == len(items)
        else [
            f"{len(items) - approved} of {len(items)} reference outputs still need to be written "
            "or approved by a human in evals/golden/requests.json"
        ],
    )


# --- analyst ---


def _draw(rng: random.Random, trials: int, rate: float) -> int:
    return sum(rng.random() < rate for _ in range(trials))


def analyst_winner_detection(runs: int = 100) -> EvalResult:
    """Seeded datasets with known answers, through the same test the analyst relies on."""
    rng = random.Random(20261004)
    clear = null_calls = tiny_calls = two_arm = 0
    for _ in range(runs):
        arms = [
            Arm(label=label, trials=8000, successes=_draw(rng, 8000, rate))
            for label, rate in (("pain", 0.020), ("outcome", 0.035), ("social_proof", 0.020))
        ]
        clear += compare(arms).winner == "outcome"
        arms = [
            Arm(label=label, trials=8000, successes=_draw(rng, 8000, 0.02))
            for label in ("pain", "outcome", "social_proof")
        ]
        null_calls += compare(arms).winner is not None
        arms = [
            Arm(label="pain", trials=30, successes=_draw(rng, 30, 0.05)),
            Arm(label="outcome", trials=30, successes=_draw(rng, 30, 0.25)),
        ]
        tiny_calls += compare(arms).winner is not None
        arms = [
            Arm(label="pain", trials=5000, successes=_draw(rng, 5000, 0.03)),
            Arm(label="outcome", trials=5000, successes=_draw(rng, 5000, 0.05)),
        ]
        two_arm += compare(arms).winner == "outcome"
    result = {
        "clear_winner_detected": clear / runs,
        "two_arm_winner_detected": two_arm / runs,
        "false_winner_rate_when_no_difference": null_calls / runs,
        "winner_called_on_tiny_sample": tiny_calls / runs,
    }
    thresholds = {
        "clear_winner_detected": ">= 0.95",
        "two_arm_winner_detected": ">= 0.95",
        "false_winner_rate_when_no_difference": "<= 0.08",
        "winner_called_on_tiny_sample": "== 0",
    }
    ok = (
        result["clear_winner_detected"] >= 0.95
        and result["two_arm_winner_detected"] >= 0.95
        and result["false_winner_rate_when_no_difference"] <= 0.08
        and result["winner_called_on_tiny_sample"] == 0
    )
    return EvalResult(
        name="analyst_winner_detection",
        agent="analyst",
        status="pass" if ok else "fail",
        metrics={"datasets_per_scenario": runs, **result},
        thresholds=thresholds,
    )


def analyst_end_to_end(seeds: int = 10) -> EvalResult:
    """Simulated weeks through CSV ingest and analysis. Known answer: outcome wins; the
    landing page test is too small to call."""
    correct = 0
    for number in range(seeds):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        for source, text in seed(engine, rng_seed=number).items():
            ingest(engine, "acme", source, text)
        readouts = {r.name: r for r in analyze(engine, "acme").readouts}
        correct += (
            readouts["07-day03-ad"].winner == "outcome"
            and readouts["linkedin_post by angle"].winner == "outcome"
            and readouts["08-day04-landing_hero"].status == "not_enough_data"
        )
    return EvalResult(
        name="analyst_end_to_end",
        agent="analyst",
        status="pass" if correct == seeds else "fail",
        metrics={"simulated_weeks": seeds, "fully_correct": correct / seeds},
        thresholds={"fully_correct": "== 1.0"},
    )


# --- guardrails ---


def guardrail_cases() -> EvalResult:
    cases = _load("guardrail_cases.json")["cases"]
    missed, false_blocks = [], []
    should_block = 0
    for case in cases:
        brand = BRANDS[case["brand"]]
        facts = allowed_facts(brand, _empty_strategy(brand))
        fired = {v.rule for v in guardrails.check([case["text"]], brand, facts)}
        expected = set(case["should_block"])
        should_block += bool(expected)
        if expected - fired:
            missed.append(f"{case['id']}: expected {sorted(expected - fired)}")
        if fired - expected:
            false_blocks.append(f"{case['id']}: unexpected {sorted(fired - expected)}")
    clean = len(cases) - should_block
    return EvalResult(
        name="guardrails",
        agent="guardrails",
        status="pass" if not missed and not false_blocks else "fail",
        metrics={
            "cases": len(cases),
            "block_recall": round(1 - len(missed) / should_block, 3),
            "false_block_rate": round(len([f for f in false_blocks]) / max(1, clean), 3),
        },
        thresholds={"block_recall": "== 1.0", "false_block_rate": "== 0"},
        notes=missed + false_blocks,
    )


def _empty_strategy(brand):
    """allowed_facts only needs the evidence list; evals without a strategy pass none."""

    class _NoEvidence:
        evidence: list = []

    return _NoEvidence()


# --- strategy ---


def completeness(doc: StrategyCore, evidence_ids: set[str] | None = None) -> dict:
    """Framework completeness: is every part of every framework filled in?"""
    pos, house = doc.positioning, doc.messaging_house
    plays = [play for stage in doc.channel_plan.stages for play in stage.channels]
    checks = {
        "positioning_has_all_five_components": all(
            [
                pos.competitive_alternatives,
                pos.unique_attributes,
                pos.value,
                pos.target_customers,
                pos.market_category.text,
                pos.positioning_statement,
            ]
        ),
        "jobs_cover_functional_and_emotional": bool(
            doc.jobs.functional_jobs and doc.jobs.emotional_jobs
        ),
        "messaging_house_has_core_and_3_pillars": bool(house.core_message.text)
        and len(house.pillars) == 3,
        "every_pillar_has_a_proof_point": all(pillar.proof_points for pillar in house.pillars),
        "funnel_has_4_stages_in_order": [s.stage for s in doc.channel_plan.stages]
        == ["awareness", "consideration", "conversion", "retention"],
        "budget_split_sums_to_100": sum(play.budget_pct for play in plays) == 100,
        "every_stage_has_a_kpi": all(stage.kpis for stage in doc.channel_plan.stages),
        "five_experiments_fully_specified": len(doc.experiments) == 5
        and all(
            e.hypothesis and e.metric and e.minimum_sample and e.decision_rule
            for e in doc.experiments
        ),
        "experiments_ranked_by_ice": [e.ice for e in doc.experiments]
        == sorted((e.ice for e in doc.experiments), reverse=True),
        "has_icp_priorities_pillars_and_kpis": bool(
            doc.icp_priorities and doc.content_pillars and doc.kpis
        ),
    }
    return checks


def strategy_completeness_saved(root: Path = Path("workspaces")) -> EvalResult:
    """Completeness of the latest saved strategy in each workspace (no model calls)."""
    scores, notes = {}, []
    for path in sorted(root.glob("*/strategy/*.json")):
        latest = sorted(path.parent.glob("*.json"))[-1]
        if path != latest:
            continue
        doc = StrategyDoc.model_validate_json(path.read_text())
        checks = completeness(doc)
        scores[doc.workspace] = round(sum(checks.values()) / len(checks), 2)
        notes += [f"{doc.workspace}: failed {name}" for name, ok in checks.items() if not ok]
        notes += [f"{doc.workspace}: {issue}" for issue in doc.issues]
    if not scores:
        return EvalResult(
            name="strategy_completeness",
            agent="strategist",
            status="skipped",
            notes=["No saved strategies under workspaces/ yet"],
        )
    worst = min(scores.values())
    return EvalResult(
        name="strategy_completeness",
        agent="strategist",
        status="pass" if worst >= 0.9 else "fail",
        metrics=scores,
        thresholds={"each workspace": ">= 0.9"},
        notes=notes,
    )


# --- content: deterministic checks, used offline on references and live on agent output ---


def content_checks(text: str, brand, strategy=None) -> dict:
    lines = text.splitlines()
    words = max(1, len(text.split()))
    facts = allowed_facts(brand, strategy or _empty_strategy(brand))
    return {
        "banned_phrases_per_100_words": round(len(cliche_hits(lines, brand)) / words * 100, 2),
        "unverified_claims": len(unverified_facts(lines, facts)),
        "guardrail_violations": len(guardrails.check(lines, brand, facts)),
        "reading_grade": metrics.reading_grade(text),
    }


def _summarise(rows: list[dict]) -> dict:
    return {
        "pieces": len(rows),
        "banned_phrases_per_100_words": round(
            statistics.mean(r["banned_phrases_per_100_words"] for r in rows), 2
        ),
        "pieces_with_unverified_claims": round(
            sum(r["unverified_claims"] > 0 for r in rows) / len(rows), 2
        ),
        "pieces_with_guardrail_violations": round(
            sum(r["guardrail_violations"] > 0 for r in rows) / len(rows), 2
        ),
        "mean_reading_grade": round(statistics.mean(r["reading_grade"] for r in rows), 1),
    }


CONTENT_THRESHOLDS = {
    "banned_phrases_per_100_words": "== 0",
    "pieces_with_unverified_claims": "== 0",
    "pieces_with_guardrail_violations": "== 0",
    "mean_reading_grade": "<= 11",
}


def _content_ok(summary: dict) -> bool:
    return (
        summary["banned_phrases_per_100_words"] == 0
        and summary["pieces_with_unverified_claims"] == 0
        and summary["pieces_with_guardrail_violations"] == 0
        and summary["mean_reading_grade"] <= 11
    )


def golden_references() -> EvalResult:
    """The approved human references must themselves pass the content checks."""
    approved = [item for item in golden_items() if item["approved_by"] and item["reference"]]
    if not approved:
        return EvalResult(
            name="golden_references",
            agent="content",
            status="pending",
            notes=["No approved references yet, so there is nothing to check"],
        )
    summary = _summarise([content_checks(i["reference"], BRANDS[i["brand"]]) for i in approved])
    return EvalResult(
        name="golden_references",
        agent="content",
        status="pass" if _content_ok(summary) else "fail",
        metrics=summary,
        thresholds=CONTENT_THRESHOLDS,
    )


def human_scores() -> dict[str, float]:
    with (HERE / "calibration/human_scores.csv").open(newline="") as handle:
        return {
            row["item_id"]: float(row["human_score"])
            for row in csv.DictReader(handle)
            if row["human_score"].strip()
        }


def calibration_status() -> EvalResult:
    """Offline view of judge calibration: how many human scores exist."""
    pieces = _load("calibration/pieces.json")["pieces"]
    scored = len(human_scores())
    return EvalResult(
        name="judge_calibration",
        agent="judge",
        status="pending" if scored < len(pieces) else "skipped",
        metrics={"calibration_pieces": len(pieces), "human_scores": scored},
        notes=[
            f"{len(pieces) - scored} human scores missing in evals/calibration/human_scores.csv"
            if scored < len(pieces)
            else "Human scores are in; run `make eval-live` to compare them with the judge"
        ],
    )


# --- live evals (model calls) ---


def live_strategy(llm, root: Path) -> tuple[EvalResult, dict[str, StrategyDoc]]:
    from evals.judge import judge_strategy
    from growthcrew.agents.strategist import (
        StrategistAgent,
        StrategyInput,
        build_evidence,
        render_evidence,
    )

    docs, complete, judged, notes = {}, {}, {}, []
    for key, brand in BRANDS.items():
        research = research_fixture(brand)
        doc = StrategistAgent(llm, root=root).run(StrategyInput(brand=brand, research=research))
        docs[key] = doc
        checks = completeness(doc)
        complete[key] = round(sum(checks.values()) / len(checks), 2)
        notes += [f"{key}: failed {name}" for name, ok in checks.items() if not ok]
        notes += [f"{key}: {issue}" for issue in doc.issues]
        evidence = render_evidence(build_evidence(brand, research))
        judged[key] = judge_strategy(llm, brand, evidence, doc).overall
    result = {
        "completeness": complete,
        "judge_overall": judged,
        "mean_judge_overall": round(statistics.mean(judged.values()), 2),
        "unsupported_recommendations": sum(len(doc.issues) for doc in docs.values()),
    }
    ok = min(complete.values()) >= 0.9 and result["mean_judge_overall"] >= 7
    return (
        EvalResult(
            name="strategy",
            agent="strategist",
            status="pass" if ok else "fail",
            metrics=result,
            notes=notes,
            thresholds={"completeness": ">= 0.9 each", "mean_judge_overall": ">= 7"},
        ),
        docs,
    )


def live_content(
    llm, root: Path, strategies: dict[str, StrategyDoc], limit: int | None
) -> EvalResult:
    from evals.judge import judge_content
    from growthcrew.agents.content import ContentAgent

    agent = ContentAgent(llm, root=root)
    rows, overall, passed, rounds = [], [], 0, []
    items = golden_items()[:limit] if limit else golden_items()
    for item in items:
        brand = BRANDS[item["brand"]]
        request = ContentRequest.model_validate(item["request"])
        records, _ = agent.produce(request, brand, strategies[item["brand"]], item["id"])
        for record in records:
            rows.append(content_checks(record.final.text, brand, strategies[item["brand"]]))
            overall.append(
                judge_content(llm, brand, request.content_type, record.final.text).overall
            )
            passed += record.passed
            rounds.append(len(record.versions))
    summary = _summarise(rows)
    summary |= {
        "golden_requests_run": len(items),
        "critic_pass_rate": round(passed / len(rows), 2),
        "mean_critic_rounds": round(statistics.mean(rounds), 2),
        "mean_judge_overall": round(statistics.mean(overall), 2),
        "judge_would_approve_rate": round(sum(score >= 8 for score in overall) / len(overall), 2),
    }
    ok = _content_ok(summary) and summary["mean_judge_overall"] >= 7
    return EvalResult(
        name="content",
        agent="content",
        status="pass" if ok else "fail",
        metrics=summary,
        thresholds={**CONTENT_THRESHOLDS, "mean_judge_overall": ">= 7"},
    )


def live_judge_calibration(llm) -> EvalResult:
    from evals.judge import RUBRIC_VERSION, judge_content

    humans = human_scores()
    pieces = [p for p in _load("calibration/pieces.json")["pieces"] if p["id"] in humans]
    if len(pieces) < 20:
        return EvalResult(
            name="judge_calibration",
            agent="judge",
            status="pending",
            metrics={"human_scores": len(pieces)},
            notes=["Needs 20 human scores in evals/calibration/human_scores.csv"],
        )
    judge = [
        float(judge_content(llm, BRANDS[p["brand"]], p["content_type"], p["text"]).overall)
        for p in pieces
    ]
    result = metrics.agreement(judge, [humans[p["id"]] for p in pieces])
    ok = result["spearman"] >= 0.7 and result["within_1_point"] >= 0.7
    return EvalResult(
        name="judge_calibration",
        agent="judge",
        status="pass" if ok else "fail",
        metrics={"rubric_version": RUBRIC_VERSION, **result},
        thresholds={"spearman": ">= 0.7", "within_1_point": ">= 0.7"},
    )


def live_research(llm, root: Path = Path("workspaces"), per_report: int = 30) -> EvalResult:
    """Citation validity on saved research reports: does each URL still load, and does the
    page support the claim? Hallucination rate = claims the source does not support."""
    from growthcrew.agents.research import VERIFY_SYSTEM, _VerdictOut
    from growthcrew.agents.research_models import ResearchReport
    from growthcrew.config import AgentRole
    from growthcrew.tools.fetch import Fetcher
    from growthcrew.tools.scrape import fetch_page

    reports = [
        sorted(folder.glob("*.json"))[-1]
        for folder in sorted(root.glob("*/research"))
        if list(folder.glob("*.json"))
    ]
    if not reports:
        return EvalResult(
            name="research_citations",
            agent="research",
            status="skipped",
            notes=["No saved research reports under workspaces/ yet"],
        )
    fetcher, rng = Fetcher(), random.Random(0)
    checked = dead = unsupported = partial = 0
    for path in reports:
        report = ResearchReport.model_validate_json(path.read_text())
        claims = [c for c in report.claims() if c.source_url.startswith("http")]
        for claim in rng.sample(claims, min(per_report, len(claims))):
            checked += 1
            try:
                text = fetch_page(claim.source_url, fetcher).text[:12000]
            except Exception:
                dead += 1
                continue
            verdict = llm.call(
                AgentRole.VERIFIER,
                system=VERIFY_SYSTEM,
                output_model=_VerdictOut,
                user=f"Claim: {claim.statement}\n\n<source>\n{text}\n</source>",
                workspace=report.workspace,
                tag="eval-research",
            ).verdict
            unsupported += verdict == "not_supported"
            partial += verdict == "partially_supported"
    if not checked:
        return EvalResult(
            name="research_citations",
            agent="research",
            status="skipped",
            notes=["Saved reports contain no web-cited claims"],
        )
    result = {
        "claims_checked": checked,
        "url_unreachable_rate": round(dead / checked, 3),
        "hallucination_rate": round(unsupported / checked, 3),
        "partially_supported_rate": round(partial / checked, 3),
        "citation_validity": round(1 - (dead + unsupported) / checked, 3),
    }
    ok = result["hallucination_rate"] <= 0.05 and result["citation_validity"] >= 0.9
    return EvalResult(
        name="research_citations",
        agent="research",
        status="pass" if ok else "fail",
        metrics=result,
        thresholds={"hallucination_rate": "<= 0.05", "citation_validity": ">= 0.9"},
    )
