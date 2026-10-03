from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew import config
from growthcrew.config import AgentRole
from growthcrew.db.models import LLMCall
from growthcrew.llm import LLM, LLMOutputError, compute_cost


class Answer(BaseModel):
    text: str


def usage(**overrides):
    base = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    return SimpleNamespace(**(base | overrides))


def response(stop_reason="end_turn", parsed=Answer(text="hi"), model=config.OPUS):  # noqa: B008
    return SimpleNamespace(
        parsed_output=parsed, stop_reason=stop_reason, model=model, usage=usage()
    )


def rate_limit_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


class FakeClient:
    """Returns or raises each queued item in turn."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        messages = SimpleNamespace(parse=self._parse, create=self._parse)
        self.messages = messages
        self.beta = SimpleNamespace(messages=messages)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


def rows(engine):
    with Session(engine) as session:
        return list(session.exec(select(LLMCall).order_by(LLMCall.id)))


def make_llm(client, engine):
    return LLM(client=client, engine=engine, wait=wait_none())


def call(llm):
    return llm.call(AgentRole.RESEARCH, system="s", user="u", output_model=Answer, workspace="acme")


def test_compute_cost_includes_cache_tokens():
    cost = compute_cost(
        config.OPUS,
        usage(cache_read_input_tokens=10_000, cache_creation_input_tokens=2_000),
    )
    # 1000*4 + 500*20 + 10000*0.20 + 2000*5 per million
    assert cost == pytest.approx(0.026)


def test_compute_cost_unknown_model_is_none():
    assert compute_cost("some-other-model", usage()) is None


def test_price_lookup_accepts_dated_id():
    assert config.price_for("claude-haiku-4-5-20251001") == config.PRICING[config.HAIKU]


def test_every_role_has_a_priced_model():
    assert set(config.AGENT_MODELS) == set(AgentRole)
    for cfg in config.AGENT_MODELS.values():
        assert config.price_for(cfg.model) is not None


def test_call_returns_parsed_output_and_logs_cost(engine):
    client = FakeClient(response())
    assert call(make_llm(client, engine)) == Answer(text="hi")

    sent = client.calls[0]
    assert sent["model"] == config.AGENT_MODELS[AgentRole.RESEARCH].model
    assert sent["output_format"] is Answer

    [row] = rows(engine)
    assert (row.agent, row.workspace, row.success) == ("research", "acme", True)
    assert (row.input_tokens, row.output_tokens) == (1000, 500)
    assert row.cost_usd == pytest.approx(0.014)


def test_cost_uses_the_model_that_served_the_response(engine):
    call(make_llm(FakeClient(response(model=config.SONNET)), engine))
    [row] = rows(engine)
    assert row.model == config.SONNET
    assert row.cost_usd == pytest.approx(0.007)


def test_retries_rate_limit_and_logs_each_attempt(engine):
    client = FakeClient(rate_limit_error(), response())
    assert call(make_llm(client, engine)) == Answer(text="hi")
    first, second = rows(engine)
    assert first.success is False and "RateLimitError" in first.error
    assert second.success is True


def test_gives_up_after_max_attempts(engine):
    client = FakeClient(*[rate_limit_error() for _ in range(config.LLM_MAX_ATTEMPTS)])
    with pytest.raises(anthropic.RateLimitError):
        call(make_llm(client, engine))
    assert len(rows(engine)) == config.LLM_MAX_ATTEMPTS


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_output_raises_without_retry_and_is_logged(engine, stop_reason):
    client = FakeClient(response(stop_reason=stop_reason, parsed=None))
    with pytest.raises(LLMOutputError):
        call(make_llm(client, engine))
    [row] = rows(engine)
    assert row.success is False
    assert row.cost_usd is not None
