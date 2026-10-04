import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew import budget, workflow
from growthcrew.agents.content_models import (
    BatchPlan,
    Critique,
    PieceRecord,
    PlannedItem,
    Version,
)
from growthcrew.agents.orchestrator import Orchestrator, render_timeline, timeline
from growthcrew.agents.strategist import StrategistAgent, StrategyInput
from growthcrew.api.main import app, engine_dep, orchestrator_dep
from growthcrew.brain.store import load_brain, save_brain
from growthcrew.config import AgentRole
from growthcrew.content.types import Newsletter
from growthcrew.db.models import AgentRun, Alert, Approval, CalendarItem, Cycle, LLMCall, Task
from growthcrew.llm import LLM
from growthcrew.schemas import Claim
from test_content import META, REQUEST, critique, linkedin, llm_with
from test_strategy import BRAND, CRITIQUE, DRAFT, FINAL, RESEARCH, REVISION

NEWSLETTER = REQUEST.model_copy(update={"content_type": "newsletter"})


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


class Stubs:
    """Stand-ins for the three agents that log spend the way the real ones do."""

    def __init__(self, engine, root, strategy):
        self.engine, self.root, self.strategy = engine, root, strategy
        self.research_cost = 0.5
        self.source = "https://rival.test/pricing"
        self.strategies = 0
        self.reports = 0

    def spend(self, agent, cost):
        with Session(self.engine) as session:
            session.add(LLMCall(agent=agent, workspace="acme", model="claude-opus-5-5",
                                input_tokens=1000, output_tokens=200, cost_usd=cost))  # fmt: skip
            session.commit()

    # research
    def run(self, inp, workspace=None):
        if hasattr(inp, "research"):  # strategist
            self.spend("strategist", 0.8)
            self.strategies += 1
            return self.strategy
        self.spend("research", self.research_cost)
        self.reports += 1
        report = RESEARCH.model_copy(deep=True)
        report.voice_of_customer.top_pains = [Claim(statement="Stale", source_url=self.source)]
        folder = self.root / "acme" / "research"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{self.reports:04d}.json").write_text(report.model_dump_json())
        return report

    # content
    def plan_batch(self, brand, strategy, weeks=1, max_items=5):
        self.spend("content", 0.1)
        return BatchPlan(items=[
            PlannedItem(day=2, request=REQUEST, rationale="r"),
            PlannedItem(day=4, request=NEWSLETTER, rationale="r"),
        ])  # fmt: skip

    def produce(self, request, brand, strategy, piece_id="piece", day=None):
        self.spend("content", 0.3)
        self.spend("critic", 0.2)
        if request.content_type == "newsletter":
            body = Newsletter(subject="Fresh this week", preview_text="p", body_markdown="Hello.")
            score = 6
        else:
            body, score = linkedin("Great bread! Really great!").body, 9
        version = Version(
            round=1, metadata=META, body=body.model_dump(), text="\n".join(body.lines()),
            critique=Critique(**critique(score).model_dump()),
        )  # fmt: skip
        return [PieceRecord(id=piece_id, day=day, request=request, versions=[version])], []


@pytest.fixture
def setup(engine, tmp_path):
    llm, _, _ = llm_with([*DRAFT, CRITIQUE, REVISION, *FINAL])
    strategy = StrategistAgent(llm).run(StrategyInput(brand=BRAND, research=RESEARCH))
    save_brain(BRAND, note="test", root=tmp_path, engine=engine)
    stubs = Stubs(engine, tmp_path, strategy)
    orchestrator = Orchestrator(
        engine=engine, root=tmp_path, research=stubs, strategist=stubs, content=stubs
    )
    return orchestrator, stubs


def run_cycle(orchestrator):
    return orchestrator.run(orchestrator.start_cycle("acme").id)


@pytest.fixture
def client(engine, setup):
    orchestrator, _ = setup
    app.dependency_overrides[engine_dep] = lambda: engine
    app.dependency_overrides[orchestrator_dep] = lambda: orchestrator
    yield TestClient(app)
    app.dependency_overrides.pop(engine_dep, None)
    app.dependency_overrides.pop(orchestrator_dep, None)


# --- state machine ---


def test_cycle_runs_automated_stages_and_stops_for_approval(setup, engine):
    orchestrator, _ = setup
    cycle = run_cycle(orchestrator)
    assert cycle.stage == "awaiting_approval" and cycle.halted_reason is None

    data = timeline(engine, cycle.id)
    assert [(s["stage"], s["status"]) for s in data["steps"]] == [
        ("research", "done"), ("strategy_check", "done"), ("content_plan", "done"),
        ("drafting", "done"), ("critic", "done"),
    ]  # fmt: skip
    drafting = data["steps"][3]["agents"]
    assert {run["agent"]: run["cost_usd"] for run in drafting} == {"content": 0.6, "critic": 0.4}
    assert data["total_cost_usd"] == pytest.approx(0.5 + 0.8 + 0.1 + 0.6 + 0.4)
    assert data["steps"][4]["detail"] == (
        "1 of 2 pieces scored 8 or more on every criterion; "
        "flagged for the reviewer: Day 4 newsletter"
    )
    assert data["steps"][2]["detail"] == "2 items planned: LinkedIn post, newsletter"
    assert [d["passed_critic"] for d in data["awaiting_approval"]] == [True, False]

    with Session(engine) as session:
        assert session.exec(select(CalendarItem)).all() == []
        assert session.exec(select(Approval)).all() == []
    text = render_timeline(data)
    assert "Waiting for your approval: 2" in text and "(did not pass the critic)" in text


def test_strategy_changes_only_when_research_finds_new_sources(setup, engine):
    orchestrator, stubs = setup
    run_cycle(orchestrator)
    second = run_cycle(orchestrator)
    assert stubs.strategies == 1
    with Session(engine) as session:
        task = session.exec(
            select(Task).where(Task.cycle_id == second.id, Task.stage == "strategy_check")
        ).one()
    assert task.status == "skipped" and "No new evidence" in task.detail

    stubs.source = "https://newrival.test/launch"
    run_cycle(orchestrator)
    assert stubs.strategies == 2


def test_running_again_does_not_pass_the_approval_stage(setup):
    orchestrator, _ = setup
    cycle = run_cycle(orchestrator)
    assert orchestrator.run(cycle.id).stage == "awaiting_approval"


# --- budget ---


def test_budget_stops_the_cycle_alerts_and_can_be_resumed(setup, engine):
    orchestrator, stubs = setup
    budget.set_weekly_limit(engine, "acme", 1.0)
    stubs.research_cost = 2.0
    cycle = run_cycle(orchestrator)

    assert cycle.stage == "strategy_check" and cycle.halted_reason.startswith("budget:")
    with Session(engine) as session:
        [alert] = session.exec(select(Alert)).all()
        blocked = session.exec(select(Task).where(Task.status == "blocked")).one()
    assert "spent $2.00 this week; the limit is $1.00" in alert.message
    assert blocked.stage == "strategy_check"
    assert stubs.strategies == 0

    budget.set_weekly_limit(engine, "acme", 50.0)
    assert orchestrator.run(cycle.id).stage == "awaiting_approval"


def test_llm_refuses_requests_once_the_budget_is_spent(engine):
    with Session(engine) as session:
        session.add(LLMCall(agent="research", workspace="acme", model="m", cost_usd=30.0))
        session.commit()
    llm = LLM(client=llm_with([])[1], engine=engine, wait=wait_none())
    with pytest.raises(budget.BudgetExceeded):
        llm.call(AgentRole.CONTENT, system="s", user="u", output_model=BatchPlan, workspace="acme")
    assert llm.client.requests == []


# --- human in the loop, through the API ---


def start(client):
    response = client.post("/workspaces/acme/cycles")
    assert response.status_code == 202
    cycle_id = response.json()["id"]
    drafts = client.get("/workspaces/acme/drafts", params={"status": "pending_approval"}).json()
    return cycle_id, [draft["id"] for draft in drafts]


def stage(client, cycle_id):
    return client.get(f"/cycles/{cycle_id}/timeline").json()["stage"]


def test_nothing_moves_past_approval_without_a_human(client):
    cycle_id, (post, letter) = start(client)
    assert stage(client, cycle_id) == "awaiting_approval"
    # Not approved yet: no export, nothing on the calendar.
    assert client.get(f"/drafts/{post}/text").status_code == 409
    assert client.get(f"/drafts/{letter}/email.eml").status_code == 409
    assert client.get("/workspaces/acme/calendar").json() == []
    # A decision needs a named reviewer.
    anonymous = {"decision": "approved", "reviewer": " "}
    assert client.post(f"/drafts/{post}/decision", json=anonymous).status_code == 409

    approve = {"decision": "approved", "reviewer": "adi", "comment": "Good"}
    assert client.post(f"/drafts/{post}/decision", json=approve).status_code == 200
    assert stage(client, cycle_id) == "awaiting_approval"  # one draft still pending
    assert client.post(f"/drafts/{post}/decision", json=approve).status_code == 409  # no re-decide

    reject = {"decision": "rejected", "reviewer": "adi", "comment": "Off brand"}
    assert client.post(f"/drafts/{letter}/decision", json=reject).status_code == 200
    assert stage(client, cycle_id) == "scheduled"
    assert client.get(f"/drafts/{letter}/text").status_code == 409  # rejected stays locked

    [item] = client.get("/workspaces/acme/calendar").json()
    assert item["channel"] == "LinkedIn" and item["status"] == "scheduled"
    publish = f"/calendar/{item['id']}/publish"
    assert client.post(publish, json={"published_by": "adi"}).status_code == 409  # no confirm
    body = {"published_by": "adi", "confirm": True, "via": "linkedin"}
    assert "No 'linkedin' integration" in client.post(publish, json=body).json()["detail"]
    assert client.post(f"/calendar/{item['id']}/metrics", json={"clicks": 3}).status_code == 409

    body["via"] = "manual"
    assert client.post(publish, json=body).json()["published_via"] == "manual"
    assert stage(client, cycle_id) == "published"
    assert client.post(f"/calendar/{item['id']}/metrics", json={"clicks": 3}).status_code == 200
    assert stage(client, cycle_id) == "measured"

    detail = client.get(f"/drafts/{letter}").json()
    assert detail["approvals"][0]["comment"] == "Off brand"


def test_rejecting_everything_halts_the_cycle(client):
    cycle_id, drafts = start(client)
    for draft in drafts:
        client.post(f"/drafts/{draft}/decision", json={"decision": "rejected", "reviewer": "adi"})
    data = client.get(f"/cycles/{cycle_id}/timeline").json()
    assert data["halted_reason"] == "every draft was rejected"


def test_exports_cover_only_approved_pieces(client):
    _, (post, letter) = start(client)
    for draft in (post, letter):
        client.post(f"/drafts/{draft}/decision", json={"decision": "approved", "reviewer": "adi"})

    assert client.get(f"/drafts/{post}/text").text.startswith("Great bread!")
    csv_text = client.get("/workspaces/acme/calendar.csv").text
    assert csv_text.splitlines()[0].startswith("date,channel,content_type")
    assert "LinkedIn,linkedin_post" in csv_text and "Newsletter,newsletter" in csv_text

    eml = client.get(f"/drafts/{letter}/email.eml")
    assert "Subject: Fresh this week" in eml.text and "X-Unsent: 1" in eml.text
    assert "To:" not in eml.text
    assert client.get(f"/drafts/{post}/email.eml").status_code == 400


def test_budget_endpoints(client):
    assert client.get("/workspaces/acme/budget").json()["weekly_limit_usd"] == 25.0
    updated = client.put("/workspaces/acme/budget", json={"weekly_limit_usd": 5}).json()
    assert updated["weekly_limit_usd"] == 5
    assert client.put("/workspaces/acme/budget", json={"weekly_limit_usd": -1}).status_code == 400


# --- learning from edits ---


def test_edits_store_a_diff_and_recurring_edits_are_proposed_not_applied(setup, engine, tmp_path):
    from growthcrew.brain import learning
    from growthcrew.db.models import Draft

    orchestrator, _ = setup
    proposed_each_time = []
    for _ in range(3):
        cycle = run_cycle(orchestrator)
        with Session(engine) as session:
            draft = session.exec(
                select(Draft).where(
                    Draft.cycle_id == cycle.id, Draft.content_type == "linkedin_post"
                )
            ).one()
        edited = draft.text.replace("!", ".")
        approval, proposed = workflow.decide(
            engine, draft.id, "edited", "adi", "Too shouty", edited, root=tmp_path
        )
        proposed_each_time.append(proposed)

    assert "-Great bread! Really great!" in approval.diff
    assert "+Great bread. Really great." in approval.diff
    assert proposed_each_time == [[], [], ["Do not use exclamation marks."]]

    # The third identical edit proposes a rule. Nothing changes until a person accepts it.
    assert load_brain("acme", root=tmp_path).version == 1
    [proposal] = learning.proposals(engine, "acme")
    assert (proposal["rule"], proposal["times_seen"]) == ("Do not use exclamation marks.", 3)

    assert (
        learning.resolve_proposal(engine, "acme", proposal["id"], True, root=tmp_path) == "accepted"
    )
    brain = load_brain("acme", root=tmp_path)
    assert brain.version == 2 and "Do not use exclamation marks." in brain.voice.guide.rules
    assert learning.proposals(engine, "acme") == []
    with pytest.raises(LookupError):
        learning.resolve_proposal(engine, "acme", proposal["id"], True, root=tmp_path)

    with Session(engine) as session:
        [item] = session.exec(select(CalendarItem).where(CalendarItem.cycle_id == cycle.id)).all()
        draft = session.get(Draft, item.draft_id)
    assert draft.text == edited and draft.original_text != edited and draft.status == "approved"


def test_agent_runs_and_cycle_rows_exist(setup, engine):
    orchestrator, _ = setup
    cycle = run_cycle(orchestrator)
    with Session(engine) as session:
        runs = session.exec(select(AgentRun).where(AgentRun.cycle_id == cycle.id)).all()
        assert session.get(Cycle, cycle.id).state_json
    by_agent = {run.agent for run in runs}
    assert by_agent == {"research", "strategist", "content", "critic", "orchestrator"}
    assert json.loads(runs[0].model_dump_json())["input_tokens"] == 1000
