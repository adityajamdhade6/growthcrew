import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew.agents.content import ContentAgent
from growthcrew.agents.strategist import build_evidence
from growthcrew.db.models import Draft, LLMCall, MemoryPiece, PerformanceRow, RuleEvent
from growthcrew.llm import LLM
from growthcrew.memory import miner, playbook, store
from growthcrew.memory.embed import HashingEmbedder, cosine
from growthcrew.memory.features import extract, hook
from growthcrew.memory.simulate import WEEKS, run
from growthcrew.versions import prompt_version
from test_content import REQUEST, critique, linkedin
from test_content import strategy as strategy  # noqa: F401  (fixture)
from test_strategy import BRAND, RESEARCH, ScriptedClient


def fresh_engine():
    # Shared across threads so the API test client can read it too.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="module")
def twelve_weeks():
    engine = fresh_engine()
    return engine, run(engine)


# --- the 12-week simulation: one real pattern, one that stops holding ---


def test_the_real_pattern_becomes_a_rule_and_stays(twelve_weeks):
    engine, log = twelve_weeks
    assert len(log) == WEEKS
    status = [week["rules"].get("question_hook") for week in log]
    assert status[0] is None  # one week is not enough to say anything
    assert "candidate" in status[:4]  # seen once, not yet trusted
    assert status[-6:] == ["active"] * 6
    [rule] = playbook.rules(engine, "acme", "active")
    assert rule.feature == "question_hook" and rule.probability > 0.99
    assert 20 < rule.lift_pct < 60  # the true lift is 40%
    assert "a question as the hook beats a statement as the hook" in rule.statement


def test_the_pattern_that_stops_holding_is_retired(twelve_weeks):
    engine, log = twelve_weeks
    status = [week["rules"].get("number_in_hook") for week in log]
    assert "active" in status[:6]  # it looked real while the lucky streak lasted
    assert "active" not in status[-3:]  # and was out of use once the streak left the window
    assert status[-1] == "retired"

    retired = {rule.feature: rule for rule in playbook.rules(engine, "acme", "retired")}
    with Session(engine) as session:
        events = session.exec(
            select(RuleEvent)
            .where(RuleEvent.rule_id == retired["number_in_hook"].id)
            .order_by(RuleEvent.at)
        ).all()
    # The history shows the whole life of the rule, one entry per weekly run.
    assert [e.status for e in events][0] == "candidate" and events[-1].status == "retired"
    assert any("→ active" in e.note for e in events) and any("→ retired" in e.note for e in events)


def test_playbook_summary_says_what_was_learned(twelve_weeks):
    engine, _ = twelve_weeks
    book = playbook.summary(engine, "acme")
    assert book["pieces_remembered"] == 96
    assert book["learned"]["still_active"] == 1 and book["learned"]["retired"] >= 1
    assert book["active"][0]["history"] and "over 99% probability" in book["active"][0]["summary"]
    versions = {row["prompt_version"]: row for row in book["by_version"]}
    assert set(versions) == {"p-sim00001", "p-sim00002"} and versions["p-sim00001"]["pieces"] == 48


def test_a_feature_riding_along_with_a_stronger_one_is_not_credited():
    # "Number" only ever appears alongside "question", which is what really helps.
    items = [(0.04, True, True)] * 6 + [(0.04, False, True)] * 6 + [(0.02, False, False)] * 12
    rates_with = [rate for rate, has, _ in items if has]
    rates_without = [rate for rate, has, _ in items if not has]
    probability, lift = miner.compare(rates_with, rates_without)
    assert probability > 0.9 and lift > 30  # looks like a strong pattern on its own
    within, adjusted = miner.compare_within(items)
    assert adjusted == pytest.approx(0, abs=1) and within < 0.9  # and vanishes once controlled
    # If the two never occur apart, they cannot be told apart at all.
    assert miner.compare_within([(0.04, True, True)] * 6 + [(0.02, False, False)] * 6) is None


# --- content memory ---


def test_memory_returns_the_best_pieces_similar_to_a_request(twelve_weeks):
    engine, _ = twelve_weeks
    examples = store.best_similar(engine, "acme", "linkedin_post", "washing linen so it stays soft")
    assert 1 <= len(examples) <= 5
    assert all(example.score > 1 for example in examples)  # only above-average pieces
    assert [e.score for e in examples] == sorted((e.score for e in examples), reverse=True)
    assert "washing linen" in examples[0].text
    assert store.best_similar(engine, "acme", "newsletter", "anything") == []


def test_pieces_without_enough_data_are_not_remembered(twelve_weeks):
    engine, _ = twelve_weeks
    with Session(engine) as session:
        row = session.exec(select(PerformanceRow)).first()
        draft_id, row.impressions, row.clicks = row.draft_id, 50, 5
        session.add(row)
        session.delete(
            session.exec(select(MemoryPiece).where(MemoryPiece.draft_id == draft_id)).one()
        )
        session.commit()
    assert store.sync(engine, "acme") == 95  # a 10% rate on 50 impressions is not a result


def test_embedder_and_features():
    embed = HashingEmbedder().embed
    linen, same_topic, other = (embed(t) for t in (
        "How to wash linen sheets so they stay soft",
        "Washing linen: keep your sheets soft",
        "Invoice reminders for freelancers who hate chasing payments",
    ))  # fmt: skip
    assert cosine(linen, same_topic) > 2 * cosine(linen, other)
    assert cosine(linen, linen) == pytest.approx(1)

    text = "Hook: Do you know the 3 reasons linen wrinkles?\nBody text here."
    assert hook(text) == "Do you know the 3 reasons linen wrinkles?"
    assert extract(text) == {"question_hook": True, "number_in_hook": True, "second_person_hook": True,
                             "short": True, "has_quote": False, "has_emoji": False}  # fmt: skip


# --- agents use memory ---


def test_writer_is_given_past_winners_and_rules_and_the_editor_checks_them(twelve_weeks, strategy):  # noqa: F811
    engine, _ = twelve_weeks
    client = ScriptedClient([linkedin("A short note about washing linen."), critique(9)])
    llm = LLM(client=client, engine=engine, wait=wait_none())
    request = REQUEST.model_copy(update={"topic": "washing linen"})
    [record], _ = ContentAgent(llm).produce(request, BRAND, strategy)

    brief = client.requests[0]["messages"][0]["content"][0]["text"]
    assert '<past_winner number="1"' in brief and "Playbook rules currently holding:" in brief
    assert "a question as the hook beats a statement as the hook" in brief
    assert record.memory.examples and len(record.memory.rules) == 1
    assert record.prompt_version.startswith("p-")
    # The draft opens with a statement, against the active rule: the editor is told.
    findings = record.final.critique.code_findings
    assert any(f.startswith("Playbook: this draft uses a statement as the hook") for f in findings)
    assert record.passed  # advice, not a block

    with Session(engine) as session:
        versions = {call.agent: call.prompt_version for call in session.exec(select(LLMCall))}
    assert (
        versions["content"] == record.prompt_version and versions["critic"] != versions["content"]
    )


def test_strategist_can_cite_playbook_rules(twelve_weeks):
    engine, _ = twelve_weeks
    rules = playbook.rules(engine, "acme", "active")
    evidence = {item.id: item for item in build_evidence(BRAND, RESEARCH, rules)}
    item = evidence[f"rule:{rules[0].id}"]
    assert "question as the hook" in item.text and item.quality == "playbook rule, active"


def test_prompt_versions_change_with_the_prompt():
    assert prompt_version("You are the writer.") == prompt_version("You are the writer.")
    assert prompt_version("You are the writer.") != prompt_version("You are the writer!")
    assert Draft.model_fields["prompt_version"].default == ""


def test_playbook_and_voice_proposal_routes(twelve_weeks, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from growthcrew.api.main import app, engine_dep
    from growthcrew.brain import learning
    from growthcrew.brain import store as brain_store

    engine, _ = twelve_weeks
    brain_store.save_brain(BRAND, note="t", root=tmp_path, engine=engine)
    monkeypatch.setattr(learning.resolve_proposal, "__defaults__", (tmp_path,))
    for _ in range(3):
        learning.record_edit(engine, "acme", "Fresh bread! Every day!", "Fresh bread. Every day.")
    app.dependency_overrides[engine_dep] = lambda: engine
    try:
        client = TestClient(app)
        book = client.get("/workspaces/acme/playbook").json()
        [proposal] = client.get("/workspaces/acme/voice-proposals").json()
        rejected = client.post(
            f"/workspaces/acme/voice-proposals/{proposal['id']}", json={"accept": False}
        )
        again = client.post(
            f"/workspaces/acme/voice-proposals/{proposal['id']}", json={"accept": True}
        )
    finally:
        app.dependency_overrides.pop(engine_dep, None)
    assert book["active"][0]["feature"] == "question_hook"
    assert proposal == {
        "id": proposal["id"],
        "rule": "Do not use exclamation marks.",
        "times_seen": 3,
    }
    assert rejected.json()["status"] == "rejected" and again.status_code == 404
    # A rejected proposal changes nothing in the brand voice.
    assert brain_store.load_brain("acme", root=tmp_path).version == 1
