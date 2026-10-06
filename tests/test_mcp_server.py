"""Phase 7: GrowthCrew as an MCP server, driven through a real MCP client in-process."""

import json

import anyio
import pytest
from mcp import Client
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from growthcrew.analytics.ingest import ingest
from growthcrew.analytics.simulate import seed
from growthcrew.api.auth import create_user
from growthcrew.brain import store
from growthcrew.db.models import ApprovalTokenUse, Draft
from growthcrew.mcp_server import AccessDenied, build_server, mint_approval, spend_approval
from test_strategy import BRAND


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("GROWTHCREW_SECRET", "test-secret")
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def server(engine, tmp_path):
    store.save_brain(BRAND, note="t", root=tmp_path, engine=engine)
    store.save_brain(BRAND.model_copy(update={"workspace": "rival"}), note="t", root=tmp_path,
                     engine=engine)  # fmt: skip
    for source, text in seed(engine, workspace="acme").items():
        ingest(engine, "acme", source, text)
    create_user(engine, "owner@acme.test", "correct-horse-battery", "acme")
    return build_server(engine, "owner@acme.test", root=tmp_path)


def call(server, tool, **arguments):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)

    return anyio.run(go)


def data(result):
    if result.structured_content is not None:
        content = result.structured_content
        return content.get("result", content) if isinstance(content, dict) else content
    return json.loads(result.content[0].text)


def test_tools_are_listed_with_read_only_hints(server):
    async def go():
        async with Client(server) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in anyio.run(go).tools}
    assert {"list_workspaces", "what_did_we_learn", "get_strategy", "list_drafts",
            "get_experiment_results", "get_playbook", "get_signals",
            "propose_content"} <= set(tools)  # fmt: skip
    assert tools["what_did_we_learn"].annotations.read_only_hint is True
    assert tools["propose_content"].annotations.read_only_hint is False


def test_a_user_sees_only_their_workspaces(server):
    assert [w["workspace"] for w in data(call(server, "list_workspaces"))] == ["acme"]
    denied = call(server, "list_drafts", workspace="rival")
    assert denied.is_error and "no access" in denied.content[0].text


def test_experiment_results_carry_their_uncertainty(server):
    results = data(call(server, "get_experiment_results", workspace="acme"))
    ad = next(r for r in results if r["kind"] == "ab_test")
    assert (
        ad["probability_leader_is_best"].endswith("%")
        and "100%" not in ad["probability_leader_is_best"]
    )
    assert len(ad["lift_95_interval_pct"]) == 2 and ad["sample_size"] > 0


def test_what_did_we_learn_answers_even_before_any_analysis(server):
    answer = data(call(server, "what_did_we_learn", workspace="acme"))
    assert answer["summary"] == "No week has been analysed yet."


def test_writing_needs_a_fresh_single_use_token_for_that_workspace(engine, server):
    no_token = call(server, "propose_content", workspace="acme", content_type="linkedin_post",
                    goal="g", pillar="p", audience="a")  # fmt: skip
    assert no_token.is_error and "invalid or has expired" in no_token.content[0].text
    wrong = mint_approval("rival", "propose_content", "owner@acme.test")
    with pytest.raises(AccessDenied, match="only"):
        spend_approval(engine, wrong, "acme", "propose_content")
    token = mint_approval("acme", "propose_content", "owner@acme.test")
    assert spend_approval(engine, token, "acme", "propose_content") == "owner@acme.test"
    with pytest.raises(AccessDenied, match="already been used"):
        spend_approval(engine, token, "acme", "propose_content")
    expired = mint_approval("acme", "propose_content", "owner@acme.test", ttl=-1)
    with pytest.raises(AccessDenied):
        spend_approval(engine, expired, "acme", "propose_content")
    with Session(engine) as session:
        [used] = session.exec(select(ApprovalTokenUse)).all()
        assert used.approved_by == "owner@acme.test"
    # A valid token on a server with no model still drafts nothing.
    fresh = mint_approval("acme", "propose_content", "owner@acme.test")
    result = call(server, "propose_content", workspace="acme", content_type="linkedin_post",
                  goal="g", pillar="p", audience="a", approval_token=fresh)  # fmt: skip
    assert result.is_error and "No model" in result.content[0].text
    with Session(engine) as session:
        assert session.exec(select(Draft).where(Draft.cycle_id > 1)).all() == []


def test_unknown_users_cannot_start_the_server(engine, tmp_path):
    with pytest.raises(LookupError):
        build_server(engine, "nobody@example.com", root=tmp_path)
