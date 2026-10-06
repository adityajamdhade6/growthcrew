"""Phase 9: job queue, crash recovery, response cache, tenancy, roles, audit log, kill switch,
log scrubber, migrations, Postgres."""

import json
import logging
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew import audit, jobs, safety, tenancy, workflow
from growthcrew.config import AgentRole
from growthcrew.content.types import ContentRequest
from growthcrew.db.models import Alert, AuditLog, Cycle, Draft, Job, LLMCall
from growthcrew.llm import LLM, cache_scope
from test_research import ScriptedClient, reply

REQUEST = ContentRequest(
    content_type="ad", pillar="p", audience="a", funnel_stage="awareness", goal="g"
)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def add_draft(engine, workspace="acme", status="pending_approval"):
    with Session(engine, expire_on_commit=False) as session:
        cycle = Cycle(workspace=workspace, week_start=datetime(2026, 9, 28, tzinfo=UTC))
        session.add(cycle)
        session.flush()
        draft = Draft(
            cycle_id=cycle.id,
            workspace=workspace,
            piece_id="01-day01-ad",
            content_type="ad",
            original_text="t",
            text="t",
            body_json="{}",
            metadata_json="{}",
            min_score=9,
            passed_critic=True,
            status=status,
        )
        session.add(draft)
        session.commit()
        return draft


# --- the job queue ---


def test_enqueue_is_idempotent_and_one_worker_claims_a_job(engine):
    first = jobs.enqueue(engine, "sync", "sync:acme:2026-10-05", "acme")
    again = jobs.enqueue(engine, "sync", "sync:acme:2026-10-05", "acme")
    assert first.id == again.id
    claimed = jobs.claim(engine, "w1")
    assert claimed.id == first.id and claimed.attempts == 1
    assert jobs.claim(engine, "w2") is None  # leased to w1


def test_failures_back_off_then_die_with_an_alert(engine):
    jobs.enqueue(engine, "sync", "k", "acme", max_attempts=2)

    def boom(engine, job):
        raise RuntimeError("source down")

    first = jobs.run_one(engine, {"sync": boom}, "w1")
    run_after = first.run_after if first.run_after.tzinfo else first.run_after.replace(tzinfo=UTC)
    assert first.status == "queued" and run_after > datetime.now(UTC)
    assert jobs.run_one(engine, {"sync": boom}, "w1") is None  # not due yet
    with Session(engine) as session:
        row = session.get(Job, first.id)
        row.run_after = datetime.now(UTC) - timedelta(seconds=1)
        session.add(row)
        session.commit()
    dead = jobs.run_one(engine, {"sync": boom}, "w1")
    assert dead.status == "dead" and "source down" in dead.last_error
    with Session(engine) as session:
        assert session.exec(select(Alert)).one().kind == "job_dead"


def test_a_killed_worker_loses_its_job_to_another_and_its_late_finish_is_ignored(engine):
    job = jobs.enqueue(engine, "sync", "k2", "acme")
    jobs.claim(engine, "dying-worker")  # then the process is killed: no finish, no fail
    with Session(engine) as session:
        row = session.get(Job, job.id)
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)  # the lease runs out
        session.add(row)
        session.commit()
    done = jobs.run_one(engine, {"sync": lambda e, j: "ok"}, "survivor")
    assert done.status == "done" and done.worker == "survivor" and done.attempts == 2
    jobs.fail(engine, job.id, "dying-worker", "zombie wakes up")
    with Session(engine) as session:
        assert session.get(Job, job.id).status == "done"
    assert not jobs.heartbeat(engine, job.id, "dying-worker")


def test_a_cycle_killed_mid_stage_resumes_without_redoing_finished_stages(engine, tmp_path):
    from growthcrew.agents.orchestrator import Orchestrator
    from growthcrew.agents.strategist import StrategistAgent, StrategyInput
    from growthcrew.brain.store import save_brain
    from test_cycle import CRITIQUE, DRAFT, FINAL, REVISION, Stubs, llm_with
    from test_strategy import BRAND, RESEARCH

    llm, _, _ = llm_with([*DRAFT, CRITIQUE, REVISION, *FINAL])
    strategy = StrategistAgent(llm).run(StrategyInput(brand=BRAND, research=RESEARCH))
    save_brain(BRAND, note="t", root=tmp_path, engine=engine)
    stubs = Stubs(engine, tmp_path, strategy)
    killed = {"once": True}
    original = stubs.plan_batch

    def plan_then_die(*args, **kwargs):
        if killed.pop("once", False):
            raise SystemExit("worker killed")  # not an Exception: nothing is recorded
        return original(*args, **kwargs)

    stubs.plan_batch = plan_then_die
    orchestrator = Orchestrator(
        engine=engine, root=tmp_path, research=stubs, strategist=stubs, content=stubs
    )
    cycle = orchestrator.start_cycle("acme")
    jobs.enqueue(engine, "weekly_cycle", f"cycle:{cycle.id}", "acme", {"cycle_id": cycle.id})

    def handler(engine, job):
        return orchestrator.run(json.loads(job.payload)["cycle_id"]).stage

    with pytest.raises(SystemExit):
        jobs.run_one(engine, {"weekly_cycle": handler}, "doomed")
    assert stubs.reports == 1
    with Session(engine) as session:
        row = session.exec(select(Job)).one()
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        session.add(row)
        session.commit()
    done = jobs.run_one(engine, {"weekly_cycle": handler}, "survivor")
    assert done.status == "done" and done.result == "awaiting_approval"
    assert stubs.reports == 1  # research was not paid for twice


# --- the response cache ---


def test_resumed_work_replays_paid_responses_from_the_cache(engine):
    client = ScriptedClient(reply(parsed=REQUEST), reply(parsed=REQUEST), reply(parsed=REQUEST))
    llm = LLM(client=client, engine=engine, wait=wait_none())

    def ask(prompt="u"):
        return llm.call(
            AgentRole.CONTENT,
            system="s",
            user=prompt,
            output_model=ContentRequest,
            workspace="acme",
        )

    with cache_scope("cycle:1"):
        first = ask()
    with cache_scope("cycle:1"):
        replayed = ask()
    with cache_scope("cycle:2"):
        ask()  # a different week is never answered from another week's cache
    assert first == replayed and len(client.requests) == 2
    with Session(engine) as session:
        cached = session.exec(select(LLMCall).where(LLMCall.cached)).all()
    assert len(cached) == 1 and cached[0].cost_usd is None and cached[0].input_tokens == 0


# --- tenancy ---


def test_queries_inside_a_tenant_cannot_see_or_write_another_workspace(engine):
    mine, theirs = add_draft(engine, "acme"), add_draft(engine, "rival")
    with tenancy.tenant("acme"), Session(engine) as session:
        assert session.exec(select(Draft).where(Draft.workspace == "rival")).all() == []
        assert [d.id for d in session.exec(select(Draft))] == [mine.id]
        assert session.get(Draft, theirs.id) is None
        session.add(
            Draft(
                cycle_id=1,
                workspace="rival",
                piece_id="x",
                content_type="ad",
                original_text="x",
                text="x",
                body_json="{}",
                metadata_json="{}",
                min_score=1,
                passed_critic=False,
            )
        )
        with pytest.raises(tenancy.CrossTenantWrite):
            session.flush()
    with Session(engine) as session:
        assert len(session.exec(select(Draft)).all()) == 2  # outside a tenant: everything


# --- roles, audit, kill switch, rate limit, health ---


@pytest.fixture
def api(engine, monkeypatch):
    from growthcrew.api import auth
    from growthcrew.api.main import app, engine_dep

    monkeypatch.setenv("GROWTHCREW_SECRET", "test-secret")
    for email, role in (
        ("owner@acme.test", "owner"),
        ("approver@acme.test", "approver"),
        ("viewer@acme.test", "viewer"),
    ):
        user = auth.create_user(engine, email, "correct-horse-battery", "acme")
        with Session(engine) as session:
            row = session.get(type(user), user.id)
            row.roles = json.dumps({"acme": role})
            session.add(row)
            session.commit()
    app.dependency_overrides.pop(auth.authorize, None)
    app.dependency_overrides[engine_dep] = lambda: engine
    client = TestClient(app)

    def as_(email):
        token = client.post(
            "/auth/login", json={"email": email, "password": "correct-horse-battery"}
        ).json()
        return {"Authorization": f"Bearer {token['token']}"}

    yield client, as_
    app.dependency_overrides.pop(engine_dep, None)


def test_roles_decide_who_may_approve_and_who_may_change_settings(api, engine):
    client, as_ = api
    draft = add_draft(engine)
    viewer, approver, owner = (
        as_("viewer@acme.test"),
        as_("approver@acme.test"),
        as_("owner@acme.test"),
    )
    assert client.get(f"/drafts/{draft.id}", headers=viewer).status_code == 200
    denied = client.post(
        f"/drafts/{draft.id}/decision", headers=viewer, json={"decision": "approved"}
    )
    assert denied.status_code == 403 and "approver role" in denied.json()["detail"]
    ok = client.post(
        f"/drafts/{draft.id}/decision", headers=approver, json={"decision": "approved"}
    )
    assert ok.status_code == 200
    body = {"api_key": "k", "settings": {}}
    assert (
        client.put("/workspaces/acme/connectors/hubspot", headers=approver, json=body).status_code
        == 403
    )
    assert (
        client.put("/workspaces/acme/connectors/hubspot", headers=owner, json=body).status_code
        == 200
    )
    log = client.get("/workspaces/acme/audit", headers=viewer).json()
    assert [entry["action"] for entry in log] == ["connector.saved", "draft.approved"]
    assert log[1]["actor"] == "approver@acme.test"
    members = client.put(
        "/workspaces/acme/members",
        headers=owner,
        json={"email": "viewer@acme.test", "role": "approver"},
    )
    assert {"email": "viewer@acme.test", "role": "approver"} in members.json()


def test_the_kill_switch_stops_every_model_call_until_resumed(api, engine):
    client, as_ = api
    owner = as_("owner@acme.test")
    assert client.post("/workspaces/acme/pause", headers=owner, json={"paused": True}).json()[
        "paused"
    ]
    model = ScriptedClient(reply(parsed=REQUEST))
    llm = LLM(client=model, engine=engine, wait=wait_none())
    with pytest.raises(safety.AgentsPaused, match="owner@acme.test"):
        llm.call(
            AgentRole.CONTENT, system="s", user="u", output_model=ContentRequest, workspace="acme"
        )
    assert model.requests == []
    client.post("/workspaces/acme/pause", headers=owner, json={"paused": False})
    llm.call(AgentRole.CONTENT, system="s", user="u", output_model=ContentRequest, workspace="acme")
    assert len(model.requests) == 1


def test_requests_over_the_rate_limit_get_429(api, monkeypatch):
    from growthcrew.api import auth

    client, as_ = api
    headers = as_("viewer@acme.test")
    monkeypatch.setattr(auth, "RATE_LIMIT", 3)
    auth._requests.clear()
    codes = [client.get("/workspaces/acme/audit", headers=headers).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    auth._requests.clear()


def test_health_reports_the_database_and_queue(api, engine):
    client, _ = api
    jobs.enqueue(engine, "sync", "h", "acme")
    assert client.get("/health").json() == {
        "status": "ok",
        "database": "sqlite",
        "jobs_queued": 1,
        "jobs_dead": 0,
    }


def test_audit_log_is_a_chain_and_append_only_in_the_database(engine):
    audit.protect(engine)
    draft = add_draft(engine)
    workflow.decide(engine, draft.id, "approved", "owner@acme.test")
    audit.record(engine, "acme", "owner@acme.test", "connector.saved", "brevo")
    assert audit.verify(engine) is None
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as connection:
            connection.execute(text("UPDATE auditlog SET actor = 'someone else'"))
    with Session(engine) as session:
        session.add(AuditLog(actor="forger", action="draft.approved", prev_hash="x", hash="y"))
        session.commit()
    assert audit.verify(engine) == 3


# --- secrets ---


def test_secrets_never_reach_the_logs(caplog):
    record = logging.LogRecord(
        "x",
        logging.ERROR,
        __file__,
        1,
        "calling with %s and api_key=%s",
        ("Bearer abcdefghijklmnop1234", "xkeysib-0123456789abcdef"),
        None,
    )
    safety.ScrubFilter().filter(record)
    assert "abcdefghijklmnop" not in record.msg and "0123456789abcdef" not in record.msg
    assert "[REDACTED]" in record.msg
    for secret in (
        "sk-ant-api03-SECRETSECRET",
        "ya29.a0AfH6SMBsecret",
        "1//0gSECRETrefresh",
        "pat-na1-11111111-2222",
        'password="hunter22"',
    ):
        assert secret not in safety.scrub(f"value {secret} end")


# --- migrations and Postgres ---


def test_migrations_create_exactly_the_tables_the_models_describe(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from growthcrew.db.session import upgrade

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade(engine)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), SQLModel.metadata)
    # Any model change without a migration shows up here.
    assert diff == []


@pytest.mark.skipif(
    not os.getenv("GROWTHCREW_TEST_POSTGRES_URL"),
    reason="set GROWTHCREW_TEST_POSTGRES_URL to run against Postgres",
)
def test_postgres_with_pgvector_memory_search():
    from growthcrew.db.models import MemoryPiece
    from growthcrew.db.session import engine_url, upgrade
    from growthcrew.memory.embed import HashingEmbedder
    from growthcrew.memory.store import best_similar

    engine = create_engine(engine_url(os.environ["GROWTHCREW_TEST_POSTGRES_URL"]))
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    upgrade(engine)
    embedder = HashingEmbedder()
    with Session(engine) as session:
        for n, text_ in enumerate(
            [
                "Sourdough delivered fresh every Sunday",
                "Linen sheets that sleep cool",
                "Sourdough starter tips",
            ]
        ):
            session.add(
                MemoryPiece(
                    workspace="acme",
                    draft_id=n + 1,
                    content_type="ad",
                    published_on=datetime(2026, 9, 1, tzinfo=UTC),
                    text=text_,
                    metric="click-through rate",
                    trials=1000,
                    successes=30 + n,
                    rate=0.03,
                    score=1.2,
                    embedding=json.dumps(embedder.embed(text_)),
                )
            )
        session.commit()
    with engine.begin() as connection:
        connection.execute(text("UPDATE memorypiece SET embedding_vec = embedding::vector"))
    found = best_similar(engine, "acme", "ad", "fresh sourdough bread", k=2, pool=2)
    assert [example.text for example in found][0].startswith("Sourdough")
