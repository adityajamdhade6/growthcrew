import random
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import SQLModel, create_engine
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew.agents.research import ResearchAgent, ResearchInput
from growthcrew.agents.research_models import (
    BriefDraft,
    CompetitorTeardown,
    Learning,
    MarketSignals,
    Quote,
    QuoteTheme,
    Teardowns,
    VoiceOfCustomer,
)
from growthcrew.brain.models import Brain, BusinessProfile, Competitor
from growthcrew.config import AgentRole
from growthcrew.llm import LLM, Tool
from growthcrew.schemas import Claim
from growthcrew.tools.cache import DiskCache
from growthcrew.tools.fetch import Fetcher, RobotsDisallowed
from growthcrew.tools.reviews import ReviewsUnavailable, get_reviews

RIVAL = "https://rival.test/pricing"
PAGE_TEXT = "Rival Pro costs $49 per month. Built for busy bakeries."
USAGE = SimpleNamespace(
    input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
)


def reply(*blocks, stop_reason="end_turn", parsed=None):
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        parsed_output=parsed,
        model="claude-opus-5-5",
        usage=USAGE,
    )


def tool_use(id, name, **input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


TEXT = SimpleNamespace(type="text", text="ok")


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        messages = SimpleNamespace(parse=self._send, create=self._send)
        self.messages = messages
        self.beta = SimpleNamespace(messages=messages)

    def _send(self, **kwargs):
        # Snapshot the history: the conversation keeps appending to the same list.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def site(robots="User-agent: *\nDisallow: /private\n"):
    hits = []

    def handler(request):
        hits.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, html=f"<html><body><p>{PAGE_TEXT}</p></body></html>")

    return httpx.Client(transport=httpx.MockTransport(handler)), hits


def make_fetcher(tmp_path, **kwargs):
    client, hits = site()
    cache = DiskCache("http", root=tmp_path / "cache")
    return Fetcher(client=client, cache=cache, **kwargs), hits


# --- tools ---


def test_fetcher_refuses_what_robots_disallows(tmp_path):
    fetcher, hits = make_fetcher(tmp_path)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://rival.test/private/plan")
    assert "/private/plan" not in hits


def test_fetcher_caches_and_rate_limits(tmp_path):
    sleeps = []
    fetcher, hits = make_fetcher(tmp_path, min_interval=2.0, sleep=sleeps.append, clock=lambda: 0)
    fetcher.get(RIVAL)
    fetcher.get(RIVAL)  # served from cache
    assert hits.count("/pricing") == 1 and sleeps == []
    fetcher.get("https://rival.test/about")  # same host, so it waits
    assert sleeps == [2.0]


def test_reviews_come_from_exports_for_sites_that_forbid_scraping(tmp_path):
    with pytest.raises(ReviewsUnavailable):
        get_reviews("g2", "Rival", "acme", root=tmp_path)

    folder = tmp_path / "acme" / "reviews"
    folder.mkdir(parents=True)
    (folder / "g2.csv").write_text("Rating,Review\n4,Setup took a whole weekend\n")
    [review] = get_reviews("g2", "Rival", "acme", root=tmp_path)
    assert review.text == "Setup took a whole weekend"
    assert review.rating == 4
    assert review.url == "workspace://acme/reviews/g2.csv#1"


# --- tool loop ---


def test_conversation_enforces_tool_budget(engine):
    client = ScriptedClient(
        reply(tool_use("a", "echo", text="1"), tool_use("b", "echo", text="2"),
              stop_reason="tool_use"),
    )  # fmt: skip
    ran = []
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    tool = Tool("echo", "Echo", schema, lambda text: ran.append(text) or text)
    chat = LLM(client=client, engine=engine, wait=wait_none()).conversation(
        AgentRole.RESEARCH, system="s", tools=[tool]
    )
    chat.run_tools("go", max_tool_calls=1)

    assert ran == ["1"] and chat.tool_calls_used == 1
    first, second = chat.messages[-1]["content"]
    assert first["is_error"] is False and "remaining: 0" in first["content"]
    assert second["is_error"] is True and "budget exhausted" in second["content"]


def test_tool_errors_are_returned_to_the_model(engine):
    client = ScriptedClient(reply(tool_use("a", "boom"), stop_reason="tool_use"), reply(TEXT))

    def boom():
        raise RobotsDisallowed("nope")

    chat = LLM(client=client, engine=engine, wait=wait_none()).conversation(
        AgentRole.RESEARCH, system="s", tools=[Tool("boom", "Boom", {"type": "object"}, boom)]
    )
    chat.run_tools("go", max_tool_calls=5)
    result = chat.messages[2]["content"][0]
    assert result["is_error"] is True and "RobotsDisallowed: nope" in result["content"]


# --- agent ---


def claim(statement, url=RIVAL):
    return Claim(statement=statement, source_url=url)


def run_agent(tmp_path, engine, verdict="not_supported"):
    made_up = "https://made-up.test/x"
    teardown = CompetitorTeardown(
        name="Rival",
        url="https://rival.test",
        positioning_statement=claim("Built for busy bakeries"),
        target_audience=claim("Enterprises", made_up),
        key_messages=[],
        pricing=[claim("Pro is $49/month"), claim("Free tier exists", made_up)],
        offers=[], channels=[], content_themes=[], strengths=[], gaps=[],
    )  # fmt: skip
    voc = VoiceOfCustomer(
        top_pains=[], desired_outcomes=[], objections=[],
        themes=[
            QuoteTheme(theme="Price", quotes=[
                Quote(text="Rival Pro costs $49 per month.", source_url=RIVAL),
                Quote(text="It is far too expensive", source_url=RIVAL),
            ]),
            QuoteTheme(theme="Invented", quotes=[Quote(text="Never said", source_url=RIVAL)]),
        ],
    )  # fmt: skip
    brief = BriefDraft(
        headline="Rival competes on price",
        learnings=[
            Learning(insight="A", why_it_matters="B", confidence="high", source_urls=[RIVAL]),
            Learning(insight="C", why_it_matters="D", confidence="high", source_urls=[made_up]),
        ],
        open_questions=["Who buys?"],
    )
    signals = MarketSignals(trends=[], seasonality=[], search_demand_themes=[])
    check = SimpleNamespace(verdict=verdict, explanation="source says $49")
    client = ScriptedClient(
        reply(tool_use("t1", "fetch_page", url=RIVAL), stop_reason="tool_use"),
        reply(TEXT),
        reply(TEXT, parsed=Teardowns(competitors=[teardown])),
        reply(TEXT, parsed=voc),
        reply(TEXT, parsed=signals),
        reply(TEXT, parsed=brief),
        reply(TEXT, parsed=check),
        reply(TEXT, parsed=check),
    )
    fetcher, _ = make_fetcher(tmp_path, min_interval=0)
    agent = ResearchAgent(
        LLM(client=client, engine=engine, wait=wait_none()),
        fetcher=fetcher,
        rng=random.Random(0),
        root=tmp_path,
    )
    brand = Brain(
        workspace="acme",
        business=BusinessProfile(name="Acme"),
        competitors=[Competitor(name="Rival", url="https://rival.test")],
    )
    return agent.run(ResearchInput(brand=brand)), client


def test_uncited_claims_and_non_verbatim_quotes_are_dropped(tmp_path, engine):
    report, _ = run_agent(tmp_path, engine)
    teardown = report.teardowns[0]
    assert teardown.positioning_statement.statement == "Built for busy bakeries"
    assert teardown.target_audience is None
    assert [c.statement for c in teardown.pricing] == ["Pro is $49/month"]

    [theme] = report.voice_of_customer.themes
    assert (theme.theme, theme.frequency) == ("Price", 1)
    assert report.model_dump()["voice_of_customer"]["themes"][0]["frequency"] == 1

    brief = report.brief
    assert (brief.claims_dropped, brief.quotes_dropped) == (2, 2)
    assert (brief.tool_calls_used, brief.sources_read) == (1, 1)
    # A learning whose only source was never read is downgraded.
    assert [item.confidence for item in brief.learnings] == ["high", "low"]


def test_verification_flags_mismatches_and_brief_is_saved(tmp_path, engine):
    report, client = run_agent(tmp_path, engine)
    checks = report.brief.verification
    assert len(checks) == 2  # only two claims survived, so both are checked
    assert all(check.verdict == "not_supported" for check in checks)
    # The verifier sees the source text, in a fresh request.
    assert PAGE_TEXT in client.requests[-1]["messages"][0]["content"]

    [markdown] = (tmp_path / "acme" / "research").glob("*-brief.md")
    text = markdown.read_text()
    assert "[MISMATCH]" in text and "2 flagged" in text
    assert len(list((tmp_path / "acme" / "research").glob("*.json"))) == 1


def test_extraction_requests_disable_tools(tmp_path, engine):
    _, client = run_agent(tmp_path, engine)
    extraction = client.requests[2]
    assert extraction["tool_choice"] == {"type": "none"}
    assert extraction["output_format"] is Teardowns
    assert [m["role"] for m in extraction["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
