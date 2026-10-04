import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from growthcrew import budget, config
from growthcrew.agents import research as research_module
from growthcrew.agents import strategist as strategist_module
from growthcrew.agents.orchestrator import recover_interrupted
from growthcrew.api import auth, ui
from growthcrew.api.deps import llm_dep
from growthcrew.api.main import app
from growthcrew.db.models import Cycle, LLMCall, OnboardingJob, StrategyComment, Task
from growthcrew.db.session import init_db
from growthcrew.reports.strategy import save_strategy
from growthcrew.tools import reviews, search
from growthcrew.tools.cache import DiskCache
from growthcrew.tools.fetch import BlockedAddress, Fetcher, assert_public
from test_content import llm_with
from test_content import strategy as strategy  # noqa: F401  (fixture)
from test_strategy import BRAND, RESEARCH
from test_web_api import PASSWORD, login
from test_web_api import client as client  # noqa: F401  (fixture)
from test_web_api import engine as engine  # noqa: F401  (fixture)

# --- fetching cannot be pointed at the server's own network ---


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/admin", "http://localhost:8000/", "http://169.254.169.254/latest/meta-data",
     "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/x"],
)  # fmt: skip
def test_private_and_local_addresses_are_never_fetched(url, tmp_path):
    with pytest.raises(BlockedAddress):
        assert_public(url)
    fetcher = Fetcher(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
                      cache=DiskCache("http", root=tmp_path))  # fmt: skip
    with pytest.raises(BlockedAddress):
        fetcher.get(url)


def test_public_and_unresolvable_hosts_pass_the_address_check():
    assert_public("https://93.184.216.34/page")
    assert_public("https://does-not-resolve.invalid/page")


# --- work cut off by a restart does not show as running forever ---


def test_interrupted_work_is_closed_out_and_the_cycle_can_be_resumed(engine):  # noqa: F811
    with Session(engine) as session:
        cycle = Cycle(workspace="acme", week_start=datetime.now(UTC), stage="drafting")
        session.add(cycle)
        session.flush()
        session.add(Task(cycle_id=cycle.id, workspace="acme", stage="drafting"))
        session.add(OnboardingJob(workspace="new", url="https://new.test", status="drafting"))
        session.add(StrategyComment(workspace="acme", section="jobs", comment="c", author="a"))
        session.commit()
    assert recover_interrupted(engine) == 3
    with Session(engine) as session:
        task, cycle = session.get(Task, 1), session.get(Cycle, 1)
        assert task.status == "failed" and "restart" in task.detail
        assert cycle.stage == "drafting" and "resume to retry" in cycle.halted_reason
        assert session.get(OnboardingJob, "new").status == "failed"
        assert session.get(StrategyComment, 1).status == "failed"
    assert recover_interrupted(engine) == 0


# --- an older database gains new columns instead of breaking ---


def test_init_db_adds_columns_the_models_have_gained():
    engine = create_engine("sqlite://", poolclass=StaticPool)  # noqa: F811
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE draft DROP COLUMN history_json"))
        connection.execute(text(
            "INSERT INTO draft (cycle_id, workspace, piece_id, content_type, original_text, text,"
            " body_json, metadata_json, min_score, passed_critic, status, created_at,"
            " prompt_version, strategy_version, memory_json)"
            " VALUES (1, 'acme', 'p', 'ad', 'x', 'x', '{}', '{}', 9, 1, 'approved', '2026-01-01',"
            " '', 0, '{}')"
        ))  # fmt: skip
    init_db(engine)
    assert "history_json" in {c["name"] for c in inspect(engine).get_columns("draft")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT history_json FROM draft")).scalar() == "[]"
    init_db(engine)  # running it again changes nothing


# --- sign-in and spending limits ---


def test_repeated_wrong_passwords_lock_the_account_for_a_while(client):  # noqa: F811
    auth._failures.clear()
    body = {"email": "owner@acme.test", "password": "wrong-password"}
    assert [client.post("/auth/login", json=body).status_code for _ in range(5)] == [401] * 5
    right = {"email": "owner@acme.test", "password": PASSWORD}
    assert client.post("/auth/login", json=right).status_code == 429
    auth._failures["owner@acme.test"] = (5, 0.0)  # the lockout has expired
    assert client.post("/auth/login", json=right).status_code == 200
    assert "owner@acme.test" not in auth._failures


def test_total_spending_cap_stops_every_workspace(engine, monkeypatch):  # noqa: F811
    with Session(engine) as session:
        session.add(LLMCall(agent="content", workspace="other", model="m", cost_usd=5.0))
        session.commit()
    budget.check(engine, "acme")
    monkeypatch.setattr(config, "TOTAL_BUDGET_USD", 5.0)
    with pytest.raises(budget.BudgetExceeded, match="its cap is \\$5.00"):
        budget.check(engine, "acme")


# --- web app routes that read research and strategy ---


def test_research_strategy_mission_and_sources_routes(
    client, engine, strategy, tmp_path, monkeypatch
):  # noqa: F811
    monkeypatch.setattr(research_module.load_latest_research, "__defaults__", (tmp_path,))
    monkeypatch.setattr(strategist_module.load_latest_strategy, "__defaults__", (tmp_path,))
    owner = login(client, "owner@acme.test")
    assert client.get("/workspaces/acme/research", headers=owner).status_code == 404
    assert client.get("/workspaces/acme/strategy", headers=owner).status_code == 404

    folder = tmp_path / "acme" / "research"
    folder.mkdir(parents=True)
    (folder / "r.json").write_text(RESEARCH.model_dump_json())
    save_strategy(strategy, tmp_path, pdf=False)
    brief = client.get("/workspaces/acme/research", headers=owner).json()["brief"]
    assert brief["headline"] == "Freshness wins"
    data = client.get("/workspaces/acme/strategy", headers=owner).json()
    assert "first_draft" not in data["strategy"] and "positioning" in data["sections"]

    mission = client.get("/workspaces/acme/mission", headers=owner).json()
    assert mission["timeline"] is None and mission["budget"]["weekly_limit_usd"] == 25.0
    sources = client.get("/workspaces/acme/sources", headers=owner).json()
    assert [s["source"] for s in sources] == ["linkedin", "gsc", "ga4", "email", "ads"]
    assert client.get("/workspaces/acme/analysis", headers=owner).json()["rows_used"] == 0
    assert client.get("/workspaces/acme/onboarding", headers=owner).json()["status"] == "done"

    # A comment asks the strategist to revise one section; the revision is saved as a new doc.
    revised = strategy.positioning.model_copy(update={"positioning_statement": "Sharper."})
    llm, _, _ = llm_with([SimpleNamespace(section=revised, what_changed="Tightened the statement")])
    llm.engine = engine
    app.dependency_overrides[llm_dep] = lambda: llm
    try:
        bad = client.post("/workspaces/acme/strategy/comments", headers=owner,
                          json={"section": "nope", "comment": "x"})  # fmt: skip
        sent = client.post("/workspaces/acme/strategy/comments", headers=owner,
                           json={"section": "positioning", "comment": "Too vague"})  # fmt: skip
    finally:
        app.dependency_overrides.pop(llm_dep, None)
    assert bad.status_code == 400 and sent.status_code == 202
    data = client.get("/workspaces/acme/strategy", headers=owner).json()
    assert data["strategy"]["positioning"]["positioning_statement"] == "Sharper."
    assert data["comments"][0]["status"] == "revised"
    assert data["comments"][0]["author"] == "owner@acme.test"
    assert (
        "Owner comment on positioning"
        in data["strategy"]["revision"]["changes"][-1]["critique_issue"]
    )


def test_onboarding_runs_in_the_background_and_reports_progress(client, engine, monkeypatch):  # noqa: F811
    def fake_onboard(url, answers, llm, *, workspace, engine, progress):
        progress("drafting", "Read 3 pages; drafting the brain")

    monkeypatch.setattr(ui, "onboard", fake_onboard)
    app.dependency_overrides[llm_dep] = lambda: object()
    other = login(client, "other@rival.test")
    try:
        assert (
            client.post("/onboarding", json={"url": "newbiz.test"}, headers=other).status_code
            == 400
        )
        taken = client.post("/onboarding", json={"url": "https://acme.test"}, headers=other)
        started = client.post("/onboarding", json={"url": "https://www.newbiz.test"}, headers=other)
    finally:
        app.dependency_overrides.pop(llm_dep, None)
    assert taken.status_code == 403  # someone else's workspace
    assert started.json() == {"workspace": "newbiz"}
    status = client.get("/workspaces/newbiz/onboarding", headers=other).json()
    assert status["status"] == "done"  # and the creator was given access to it

    def failing(*args, **kwargs):
        raise RuntimeError("No readable text")

    monkeypatch.setattr(ui, "onboard", failing)
    app.dependency_overrides[llm_dep] = lambda: object()
    try:
        client.post("/onboarding", json={"url": "https://broken.test"}, headers=other)
    finally:
        app.dependency_overrides.pop(llm_dep, None)
    job = client.get("/workspaces/broken/onboarding", headers=other).json()
    assert job["status"] == "failed" and "No readable text" in job["detail"]


# --- tools ---


def test_web_search_uses_the_api_key_and_caches(tmp_path, monkeypatch):
    with pytest.raises(search.SearchNotConfigured):
        search.web_search("bread")
    monkeypatch.setattr(config, "SEARCH_API_KEY", "key")
    calls = []

    def get(url, params, headers, timeout):
        calls.append((params, headers))
        payload = {
            "web": {"results": [{"title": "T", "url": "https://a.test", "description": "D"}]}
        }
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    fake, cache = SimpleNamespace(get=get), DiskCache("search", root=tmp_path)
    [result] = search.web_search("bread", client=fake, cache=cache)
    assert (result.url, result.snippet) == ("https://a.test", "D")
    assert calls[0][1]["X-Subscription-Token"] == "key"
    search.web_search("bread", client=fake, cache=cache)
    assert len(calls) == 1


def test_reviews_from_the_app_store_feed_and_from_json_and_text_exports(tmp_path):
    class Feed:
        def get(self, url):
            if "search" in url:
                return json.dumps({"results": [{"trackId": 7, "trackName": "Rival",
                                                "trackViewUrl": "https://apps.apple.com/app/id7?uo=4"}]})  # fmt: skip
            return json.dumps({"feed": {"entry": {"content": {"label": "Crashes on launch"},
                                                  "title": {"label": "Broken"},
                                                  "im:rating": {"label": "1"}}}})  # fmt: skip

    [review] = reviews.get_reviews("app_store", "Rival", fetcher=Feed())
    assert (review.text, review.rating, review.url) == (
        "Crashes on launch",
        1.0,
        "https://apps.apple.com/app/id7",
    )

    class Empty:
        def get(self, url):
            return json.dumps({"results": []})

    assert reviews.get_reviews("app_store", "Nothing", fetcher=Empty()) == []
    with pytest.raises(ValueError, match="Unknown review source"):
        reviews.get_reviews("yelp", "Rival")

    folder = tmp_path / "acme" / "reviews"
    folder.mkdir(parents=True)
    (folder / "reddit.json").write_text(
        json.dumps([{"body": "Too pricey", "link": "https://r.test/1"}])
    )
    (folder / "amazon.txt").write_text("Arrived late.\n\nGreat crust.")
    [reddit] = reviews.get_reviews("reddit", "Rival", "acme", root=tmp_path)
    assert (reddit.text, reddit.url) == ("Too pricey", "https://r.test/1")
    assert [r.text for r in reviews.get_reviews("amazon", "Rival", "acme", root=tmp_path)] == [
        "Arrived late.", "Great crust.",
    ]  # fmt: skip
    assert BRAND.workspace == "acme"
