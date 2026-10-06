"""Phase 8: tracing, agent limits, pairwise judging, the red team and the CI gate."""

import json
from types import SimpleNamespace

import pytest
from evals import gate, metrics
from evals.judge import PairChoice, judge_pair
from evals.redteam import run_all
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew import budgets, guardrails, tracing
from growthcrew.content.checks import unverified_facts
from growthcrew.db.models import Alert, LLMCall, Span
from growthcrew.llm import LLM
from test_research import ScriptedClient, reply
from test_strategy import BRAND


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


# --- tracing ---


def test_model_calls_inside_a_span_join_its_trace(engine):
    from growthcrew.config import AgentRole
    from growthcrew.content.types import ContentRequest

    request = ContentRequest(content_type="ad", pillar="p", audience="a",
                             funnel_stage="awareness", goal="g")  # fmt: skip
    llm = LLM(client=ScriptedClient(reply(parsed=request), reply(parsed=request)),
              engine=engine, wait=wait_none())  # fmt: skip
    with tracing.span(engine, "weekly cycle 1", "cycle", "acme") as attrs:
        trace = tracing.current().trace_id
        with tracing.span(engine, "drafting", "stage", "acme"):
            llm.call(AgentRole.CONTENT, system="s", user="u", output_model=ContentRequest,
                     workspace="acme")  # fmt: skip
        attrs["stage"] = "awaiting_approval"
    llm.call(AgentRole.CONTENT, system="s", user="u", output_model=ContentRequest)  # untraced
    [root] = tracing.tree(engine, trace)
    assert root["kind"] == "cycle" and root["attributes"]["stage"] == "awaiting_approval"
    [stage] = root["children"]
    [call] = stage["children"]
    assert (stage["name"], call["kind"], call["attributes"]["input_tokens"]) == (
        "drafting",
        "llm",
        10,
    )
    with Session(engine) as session:
        calls = session.exec(select(LLMCall)).all()
    assert [c.trace_id == trace for c in calls] == [True, False]
    assert "llm: content" in tracing.render(tracing.tree(engine, trace))


def test_a_failing_stage_is_marked_in_the_trace(engine):
    with pytest.raises(RuntimeError):
        with tracing.span(engine, "research", "stage", "acme"):
            raise RuntimeError("site down")
    with Session(engine) as session:
        row = session.exec(select(Span)).one()
    assert row.status == "error" and "site down" in json.loads(row.attributes)["error"]


def test_otlp_export_shape(engine):
    with tracing.span(engine, "weekly cycle 7", "cycle", "acme", trace_id="a" * 32, cycle_id=7):
        pass
    exported = tracing.otlp(engine, "a" * 32)
    [span] = exported["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert span["traceId"] == "a" * 32 and len(span["spanId"]) == 16
    assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
    keys = {attribute["key"]: attribute["value"] for attribute in span["attributes"]}
    assert keys["cycle_id"] == {"intValue": "7"} and span["status"]["code"] == 1


def test_cycles_are_traced_end_to_end(engine, tmp_path):
    from growthcrew.agents.orchestrator import Orchestrator
    from growthcrew.agents.strategist import StrategistAgent, StrategyInput
    from growthcrew.brain.store import save_brain
    from test_cycle import CRITIQUE, DRAFT, FINAL, REVISION, Stubs, llm_with
    from test_strategy import RESEARCH

    llm, _, _ = llm_with([*DRAFT, CRITIQUE, REVISION, *FINAL])
    strategy = StrategistAgent(llm).run(StrategyInput(brand=BRAND, research=RESEARCH))
    save_brain(BRAND, note="t", root=tmp_path, engine=engine)
    stubs = Stubs(engine, tmp_path, strategy)
    orchestrator = Orchestrator(engine=engine, root=tmp_path, research=stubs, strategist=stubs,
                                content=stubs)  # fmt: skip
    cycle = orchestrator.run(orchestrator.start_cycle("acme").id)
    [root] = tracing.tree(engine, cycle.trace_id)
    assert [c["name"] for c in root["children"]] == [
        "research", "strategy_check", "content_plan", "drafting", "critic",
    ]  # fmt: skip


# --- per-agent limits ---


def test_calls_over_an_agents_limit_raise_one_alert_a_day(engine):
    expensive = LLMCall(agent="content", workspace="acme", model="m", cost_usd=2.0, latency_ms=10)
    assert "cost $2.00" in budgets.check_call(engine, expensive)
    budgets.check_call(engine, expensive)
    slow = LLMCall(agent="panel", workspace="acme", model="m", cost_usd=0.01, latency_ms=999_000)
    assert "took" in budgets.check_call(engine, slow)
    assert budgets.check_call(engine, LLMCall(agent="content", model="m", cost_usd=0.01)) is None
    with Session(engine) as session:
        kinds = [alert.kind for alert in session.exec(select(Alert))]
    assert sorted(kinds) == ["agent_limit:content", "agent_limit:panel"]


def test_agent_report_has_p95_against_limits(engine):
    with Session(engine) as session:
        for cost in (0.1, 0.2, 0.3):
            session.add(LLMCall(agent="critic", model="m", cost_usd=cost, latency_ms=1000))
        session.commit()
    [row] = budgets.report(engine)
    assert row["calls"] == 3 and row["cost_limit_usd"] == 0.30 and row["mean_cost_usd"] == 0.2


# --- pairwise ---


def test_wilson_interval_and_win_rate():
    low, high = metrics.wilson(8, 10)
    assert 0.49 < low < 0.5 and 0.94 < high < 0.95
    result = metrics.win_rate(["new"] * 6 + ["old"] * 2 + ["tie"] * 2)
    assert result["win_rate"] == 0.7 and result["pairs"] == 10


def test_a_pairwise_preference_must_survive_swapping_the_order(engine):
    def judge(first, second):
        client = SimpleNamespace(answers=[PairChoice(better=first, reason=""),
                                          PairChoice(better=second, reason="")])  # fmt: skip
        scripted = ScriptedClient(*(reply(parsed=a) for a in client.answers))
        return LLM(client=scripted, engine=engine, wait=wait_none())

    assert judge_pair(judge("first", "second"), BRAND, "ad", "A", "B") == "a"
    assert judge_pair(judge("second", "first"), BRAND, "ad", "A", "B") == "b"
    # Always preferring whatever is shown first is position bias, not a preference.
    assert judge_pair(judge("first", "first"), BRAND, "ad", "A", "B") == "tie"


# --- red team and guardrails ---


def test_every_red_team_attack_fails():
    cases = run_all()
    assert len(cases) >= 35
    assert [c.name for c in cases if not c.passed] == []
    assert {c.category for c in cases} >= {"injection", "invented_proof", "sensitive_claim",
                                           "isolation", "budget", "approval"}  # fmt: skip


def test_ratings_head_counts_and_legal_promises_need_proof():
    facts = "Review average 4.8 out of 5 from 2,300 reviews"
    assert [s for _, s in unverified_facts(["Rated 4.9 stars by 12,000 sleepers"], facts)] == [
        "4.9 stars", "12,000",
    ]  # fmt: skip
    assert unverified_facts(["4.8 out of 5 from 2,300 reviews"], facts) == []
    rules = {v.reason for v in guardrails.check(["We guarantee to win your case"])}
    assert rules == {"Legal claim that needs substantiation"}


# --- the gate ---


def card(metric_values: dict):
    results = {}
    for (name, key), value in metric_values.items():
        results.setdefault(name, {"name": name, "status": "pass", "metrics": {}, "notes": []})
        results[name]["metrics"][key] = value
    return {"mode": "offline", "results": list(results.values())}


def test_gate_blocks_regressions_beyond_tolerance_and_failed_evals():
    base = card({("guardrails", "block_recall"): 1.0, ("red_team", "passed"): 39})
    same = card({("guardrails", "block_recall"): 1.0, ("red_team", "passed"): 39})
    assert gate.check(same, base)[0]
    worse = card({("guardrails", "block_recall"): 0.9, ("red_team", "passed"): 39})
    passed, problems, rows = gate.check(worse, base)
    assert not passed and "guardrails.block_recall fell from 1 to 0.9" in problems[0]
    assert any("REGRESSED" in row for row in rows)
    failing = card({("red_team", "passed"): 38})
    failing["results"][0].update(status="fail", notes=["FAILED isolation: GET /drafts/1"])
    passed, problems, _ = gate.check(failing, None)
    assert not passed and "red_team failed: FAILED isolation" in problems[0]
    text = gate.comment(failing, passed, problems, [])
    assert "**Gate: BLOCKED**" in text and "not counted as passed" in text


def test_the_committed_baseline_is_a_real_scorecard():
    baseline = json.loads(gate.BASELINE.read_text())
    names = {result["name"] for result in baseline["results"]}
    assert {"red_team", "guardrails", "analyst_winner_detection"} <= names
    assert not any(r["status"] == "fail" for r in baseline["results"])
