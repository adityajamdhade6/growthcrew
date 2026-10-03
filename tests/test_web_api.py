import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from growthcrew import config
from growthcrew.api import auth, ui
from growthcrew.api.main import app, engine_dep
from growthcrew.brain import store
from growthcrew.config import AgentRole
from growthcrew.db.models import Cycle, Draft, RoleModel
from growthcrew.llm import LLM
from test_strategy import BRAND

PASSWORD = "correct-horse-battery"


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, tmp_path, monkeypatch):
    """A client with real auth switched on and workspaces stored in a temp folder."""
    monkeypatch.setenv("GROWTHCREW_SECRET", "test-secret")
    for module in (store, ui):
        monkeypatch.setattr(module, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(store.load_brain, "__defaults__", (None, tmp_path))
    monkeypatch.setattr(store.list_versions, "__defaults__", (tmp_path,))
    monkeypatch.setattr(store.save_brain, "__defaults__", (tmp_path, None))
    monkeypatch.setattr(store.confirm_fields, "__defaults__", (tmp_path, None))
    monkeypatch.setattr(store.update_field, "__defaults__", (tmp_path, None))
    monkeypatch.setattr(ui._workspace_names, "__defaults__", (tmp_path,))
    store.save_brain(BRAND, note="t", root=tmp_path, engine=engine)
    auth.create_user(engine, "owner@acme.test", PASSWORD, "acme")
    auth.create_user(engine, "other@rival.test", PASSWORD, "rival")
    auth.create_user(engine, "admin@growthcrew.test", PASSWORD, "*")
    app.dependency_overrides.pop(auth.authorize, None)
    app.dependency_overrides[engine_dep] = lambda: engine
    yield TestClient(app)
    app.dependency_overrides.pop(engine_dep, None)


def login(client, email):
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_routes_need_a_valid_token(client):
    assert client.get("/health").status_code == 200
    assert client.get("/workspaces").status_code == 401
    assert (
        client.get("/workspaces", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    )
    bad = client.post(
        "/auth/login", json={"email": "owner@acme.test", "password": "wrong-password"}
    )
    unknown = client.post("/auth/login", json={"email": "nobody@x.test", "password": PASSWORD})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()  # does not reveal which emails exist


def test_expired_and_tampered_tokens_are_rejected(client):
    assert auth.read_token(auth.make_token(1)) == 1
    assert auth.read_token(auth.make_token(1, ttl=-1)) is None
    assert auth.read_token(auth.make_token(1)[:-4] + "AAAA") is None
    assert auth.verify_password(PASSWORD, auth.hash_password(PASSWORD))
    assert not auth.verify_password("another-password", auth.hash_password(PASSWORD))


def test_users_only_reach_their_own_workspaces(client, engine):
    owner, other, admin = (
        login(client, e) for e in ("owner@acme.test", "other@rival.test", "admin@growthcrew.test")
    )
    assert [w["workspace"] for w in client.get("/workspaces", headers=owner).json()] == ["acme"]
    assert client.get("/workspaces", headers=other).json() == []
    assert client.get("/workspaces/acme/brain", headers=owner).status_code == 200
    assert client.get("/workspaces/acme/brain", headers=other).status_code == 403

    with Session(engine) as session:
        cycle = Cycle(workspace="acme", week_start=datetime(2026, 9, 28, tzinfo=UTC))
        session.add(cycle)
        session.flush()
        session.add(
            Draft(
                cycle_id=cycle.id,
                workspace="acme",
                piece_id="p1",
                day=2,
                content_type="linkedin_post",
                original_text="Hi",
                text="Hi",
                body_json="{}",
                metadata_json="{}",
                min_score=9,
                passed_critic=True,
            )
        )
        session.commit()
    # Routes addressed by id are checked against the row's workspace.
    assert client.get("/drafts/1/review", headers=other).status_code == 403
    assert client.get("/drafts/1/review", headers=owner).status_code == 200
    assert client.get("/drafts/99/review", headers=owner).status_code == 404
    # Admin-only routes.
    assert client.get("/settings/models", headers=owner).status_code == 403
    assert client.get("/settings/models", headers=admin).status_code == 200
    assert client.get("/costs/summary.json", headers=owner).status_code == 403
    assert client.get("/costs/summary.json?workspace=acme", headers=owner).status_code == 200

    # The reviewer on a decision is the signed-in user, whatever the body says.
    body = {"decision": "approved", "reviewer": "someone-else"}
    approval = client.post("/drafts/1/decision", json=body, headers=owner).json()["approval"]
    assert approval["reviewer"] == "owner@acme.test"


def test_brain_review_confirm_and_edit(client):
    owner = login(client, "owner@acme.test")
    data = client.get("/workspaces/acme/brain", headers=owner).json()
    assert {"path": "business.pricing", "reason": "nothing found"} in data["weakest"]

    edited = client.put(
        "/workspaces/acme/brain/field",
        headers=owner,
        json={"path": "business.pricing", "value": "From $9 a loaf"},
    ).json()
    assert edited["brain"]["business"]["pricing"] == "From $9 a loaf"
    assert edited["brain"]["fields"]["business.pricing"]["status"] == "confirmed"
    assert edited["brain"]["version"] == 2

    confirmed = client.post(
        "/workspaces/acme/brain/confirm", headers=owner, json={"paths": ["icp.pains"]}
    ).json()
    assert confirmed["brain"]["fields"]["icp.pains"]["status"] == "confirmed"
    bad = client.put(
        "/workspaces/acme/brain/field",
        headers=owner,
        json={"path": "business.stage", "value": "enormous"},
    )
    assert bad.status_code == 400


def test_board_review_and_reschedule(client, engine):
    owner = login(client, "owner@acme.test")
    history = [
        {
            "round": 1,
            "text": "First",
            "guardrail_violations": [],
            "critique": {
                **{
                    k: {"score": 9, "reason": ""}
                    for k in (
                        "voice",
                        "clarity",
                        "persuasion",
                        "accuracy",
                        "channel_fit",
                        "ai_cliche",
                    )
                },
                "edits": [],
            },
        }
    ]
    with Session(engine) as session:
        cycle = Cycle(workspace="acme", week_start=datetime(2026, 9, 28, tzinfo=UTC))
        session.add(cycle)
        session.flush()
        for angle in ("pain", "outcome"):
            session.add(
                Draft(
                    cycle_id=cycle.id,
                    workspace="acme",
                    piece_id=f"01-day02-ad-{angle}",
                    content_type="ad",
                    angle=angle,
                    day=2,
                    original_text="Hi",
                    text="Hi",
                    body_json="{}",
                    metadata_json=json.dumps({"cta": "Buy"}),
                    min_score=9,
                    passed_critic=True,
                    history_json=json.dumps(history),
                )
            )
        session.commit()

    drafts = client.get("/workspaces/acme/board", headers=owner).json()["drafts"]
    assert [(d["date"], d["status"]) for d in drafts] == [("2026-09-29", "pending_approval")] * 2
    assert drafts[0]["group"] == drafts[1]["group"]

    review = client.get("/drafts/1/review", headers=owner).json()
    assert review["scores"]["voice"] == 9 and review["metadata"]["cta"] == "Buy"
    assert [v["angle"] for v in review["variants"]] == ["outcome"]

    moved = client.patch("/drafts/1/schedule", json={"date": "2026-10-01"}, headers=owner)
    assert moved.status_code == 200
    assert (
        client.get("/workspaces/acme/board", headers=owner).json()["drafts"][0]["date"]
        == "2026-10-01"
    )


def test_actions_that_need_the_model_say_so_clearly(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    owner = login(client, "owner@acme.test")
    response = client.post("/onboarding", json={"url": "https://new.test"}, headers=owner)
    assert response.status_code == 503 and "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_model_choice_in_settings_overrides_config(client, engine):
    admin = login(client, "admin@growthcrew.test")
    body = {"role": "critic", "model": config.HAIKU}
    roles = client.put("/settings/models", json=body, headers=admin).json()["roles"]
    assert next(r for r in roles if r["role"] == "critic")["model"] == config.HAIKU
    assert (
        client.put(
            "/settings/models", json={"role": "critic", "model": "gpt-9"}, headers=admin
        ).status_code
        == 400
    )

    cfg = LLM(client=object(), engine=engine).role_config(AgentRole.CRITIC)
    assert (cfg.model, cfg.effort) == (config.HAIKU, None)  # Haiku takes no effort parameter
    assert LLM(client=object(), engine=engine).role_config(AgentRole.CONTENT).model == config.OPUS
    with Session(engine) as session:
        assert session.exec(select(RoleModel)).one().role == "critic"
