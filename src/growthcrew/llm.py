"""The only place GrowthCrew talks to the model.

Every call goes through `LLM.call`, which retries transient failures, returns a
pydantic-validated object, and writes one `LLMCall` row per attempt with tokens
and cost. `LLM.conversation` does the same for multi-turn tool use.
"""

import base64
import contextvars
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from growthcrew import budget, budgets, config, safety, tracing
from growthcrew.config import AgentRole
from growthcrew.db.models import LLMCache, LLMCall, RoleModel, Span
from growthcrew.db.session import get_engine
from growthcrew.versions import prompt_version

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,  # includes timeouts
    anthropic.InternalServerError,  # any 5xx, including 529 overloaded
)


class LLMOutputError(RuntimeError):
    """The model responded but did not produce a usable structured output."""


# --- the response cache for resumed jobs ---

_cache_namespace: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_cache", default=None
)


@contextmanager
def cache_scope(namespace: str):
    """Within this block, identical requests are answered from the cache.

    Used for one weekly cycle: if its worker dies mid-stage, the stage reruns and every model
    call it had already paid for is replayed from the database. The namespace keeps different
    cycles apart, so a new week never reuses last week's answers.
    """
    token = _cache_namespace.set(namespace)
    try:
        yield
    finally:
        _cache_namespace.reset(token)


def _canonical(value: Any) -> Any:
    if isinstance(value, type) and issubclass(value, BaseModel):
        return {"output_schema": value.model_json_schema()}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return str(value)


def _block_dump(block: Any) -> dict:
    if isinstance(block, dict):
        data = dict(block)
    elif hasattr(block, "model_dump"):
        data = block.model_dump(mode="json", exclude_none=True)
    else:
        data = {key: value for key, value in vars(block).items() if value is not None}
    data.pop("parsed_output", None)
    return data


class _Block(dict):
    """A content block replayed from the cache: a dict the SDK accepts, read like an object."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _Cached:
    def __init__(self, data: dict, output_format: type[BaseModel] | None) -> None:
        self.content = [_Block(block) for block in data["content"]]
        self.stop_reason = data["stop_reason"]
        self.model = data["model"]
        self.usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                                     cache_creation_input_tokens=0)  # fmt: skip
        parsed = data.get("parsed")
        self.parsed_output = (
            output_format.model_validate(parsed) if output_format and parsed is not None else None
        )


def compute_cost(model: str, usage: Any) -> float | None:
    price = config.price_for(model)
    if price is None:
        logger.warning("No pricing configured for model %s; cost not recorded", model)
        return None
    return (
        (usage.input_tokens or 0) * price.input
        + (usage.output_tokens or 0) * price.output
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * price.cache_read
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * price.cache_write
    ) / 1_000_000


@dataclass(frozen=True)
class Tool:
    """A function the model may call. `fn` receives the tool input as keyword arguments."""

    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


class LLM:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        engine: Engine | None = None,
        wait: Callable | None = None,
    ) -> None:
        # Retries are handled here with tenacity, so the SDK's own are turned off.
        self.client = client or anthropic.Anthropic(max_retries=0)
        self.engine = engine or get_engine()
        self._wait = wait or wait_exponential_jitter(initial=1, max=30)

    def call(
        self,
        role: AgentRole,
        *,
        system: str,
        user: str,
        output_model: type[T],
        workspace: str | None = None,
        tag: str | None = None,
        images: Sequence[bytes] = (),
    ) -> T:
        """Run one structured-output request for `role` and return the validated result.

        `images` are PNG bytes shown to the model before the text, for the vision critic.
        """
        content: str | list[dict[str, Any]] = user
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(image).decode(),
                    },
                }
                for image in images
            ]
            content.append({"type": "text", "text": user})
        response = self.send(
            role,
            workspace,
            tag=tag,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
        )
        return response.parsed_output

    def conversation(
        self,
        role: AgentRole,
        *,
        system: str,
        tools: Sequence[Tool] = (),
        workspace: str | None = None,
        tag: str | None = None,
    ) -> "Conversation":
        return Conversation(self, role, system, tools, workspace, tag)

    def send(
        self, role: AgentRole, workspace: str | None, tag: str | None = None, **request: Any
    ) -> Any:
        """Send one request with retries. Every attempt is logged."""
        # The kill switch: a person can pause every agent, everywhere or in one workspace.
        safety.check(self.engine, workspace)
        if workspace:
            # Stop before spending more once the workspace's weekly budget is used up.
            budget.check(self.engine, workspace)
        namespace = _cache_namespace.get()
        key = None
        if namespace:
            body = json.dumps({"ns": namespace, "role": role.value,
                               "model": self.role_config(role).model, "request": request},
                              default=_canonical, sort_keys=True)  # fmt: skip
            key = hashlib.sha256(body.encode()).hexdigest()
            with Session(self.engine) as session:
                hit = session.get(LLMCache, key)
            if hit is not None:
                self._log(LLMCall(agent=role.value, workspace=workspace, tag=tag, model=json.loads(
                    hit.response)["model"], cached=True, stop_reason=json.loads(
                    hit.response)["stop_reason"],
                    prompt_version=prompt_version(str(request.get("system", "")))))  # fmt: skip
                return _Cached(json.loads(hit.response), request.get("output_format"))
        retrying = Retrying(
            retry=retry_if_exception_type(RETRYABLE),
            stop=stop_after_attempt(config.LLM_MAX_ATTEMPTS),
            wait=self._wait,
            reraise=True,
        )
        response = retrying(self._attempt, role, workspace, tag, request)
        if key:
            parsed = getattr(response, "parsed_output", None)
            if parsed is not None and not hasattr(parsed, "model_dump"):
                parsed = None
            stored = {
                "content": [_block_dump(block) for block in response.content],
                "stop_reason": response.stop_reason,
                "model": response.model,
                "parsed": parsed.model_dump(mode="json") if parsed is not None else None,
            }
            with Session(self.engine) as session:
                session.merge(LLMCache(key=key, namespace=namespace, response=json.dumps(stored)))
                session.commit()
        return response

    def _attempt(
        self, role: AgentRole, workspace: str | None, tag: str | None, request: dict[str, Any]
    ) -> Any:
        cfg = self.role_config(role)
        kwargs: dict[str, Any] = {"model": cfg.model, "max_tokens": cfg.max_tokens, **request}
        if cfg.effort:
            kwargs["output_config"] = {"effort": cfg.effort}

        messages = self.client.messages
        if config.REFUSAL_FALLBACKS:
            messages = self.client.beta.messages
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        structured = "output_format" in kwargs
        method = messages.parse if structured else messages.create

        version = prompt_version(str(request.get("system", "")))
        started = time.monotonic()
        try:
            response = method(**kwargs)
        except Exception as exc:
            self._log(
                LLMCall(
                    agent=role.value,
                    workspace=workspace,
                    tag=tag,
                    prompt_version=version,
                    model=cfg.model,
                    latency_ms=_elapsed_ms(started),
                    success=False,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            )
            raise

        error = None
        if response.stop_reason == "refusal":
            error = "model declined the request"
        elif response.stop_reason == "max_tokens":
            error = f"output truncated at max_tokens={cfg.max_tokens}"
        elif structured and response.parsed_output is None:
            error = "response contained no structured output"

        usage = response.usage
        self._log(
            LLMCall(
                agent=role.value,
                workspace=workspace,
                tag=tag,
                prompt_version=version,
                model=response.model,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cost_usd=compute_cost(response.model, usage),
                latency_ms=_elapsed_ms(started),
                stop_reason=response.stop_reason,
                success=error is None,
                error=error,
            )
        )
        if error:
            raise LLMOutputError(f"{role.value}: {error}")
        return response

    def role_config(self, role: AgentRole) -> config.RoleConfig:
        """config.AGENT_MODELS, unless Settings has chosen a different model for this role."""
        base = config.AGENT_MODELS[role]
        with Session(self.engine) as session:
            choice = session.get(RoleModel, role.value)
        if choice is None or choice.model == base.model:
            return base
        # Haiku 4.5 rejects the effort parameter.
        effort = None if choice.model == config.HAIKU else (base.effort or "medium")
        return replace(base, model=choice.model, effort=effort)

    def _log(self, row: LLMCall) -> None:
        context = tracing.current()
        if context:
            row.trace_id, row.span_id = context.trace_id, secrets.token_hex(8)
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(row)
            session.commit()
        if context:
            # Each model call is a span in the cycle's trace, with its tokens and cost.
            ended = datetime.now(UTC)
            tracing.record(self.engine, Span(
                trace_id=context.trace_id, span_id=row.span_id, parent_id=context.span_id,
                name=row.agent, kind="llm", workspace=row.workspace or "",
                started_at=ended - timedelta(milliseconds=row.latency_ms), ended_at=ended,
                duration_ms=row.latency_ms, status="ok" if row.success else "error",
                attributes=json.dumps({
                    "model": row.model, "input_tokens": row.input_tokens,
                    "output_tokens": row.output_tokens, "cost_usd": row.cost_usd,
                    "stop_reason": row.stop_reason, "tag": row.tag, "error": row.error,
                }),
            ))  # fmt: skip
        budgets.check_call(self.engine, row)


class Conversation:
    """A multi-turn exchange: a budgeted tool-use phase, then structured extractions.

    History is append-only, and the growing prefix is cached between requests.
    """

    def __init__(
        self,
        llm: LLM,
        role: AgentRole,
        system: str,
        tools: Sequence[Tool] = (),
        workspace: str | None = None,
        tag: str | None = None,
    ) -> None:
        self.tag = tag
        self.llm = llm
        self.role = role
        self.system = system
        self.tools = {tool.name: tool for tool in tools}
        self.workspace = workspace
        self.messages: list[dict[str, Any]] = []
        self.tool_calls_used = 0

    def _send(self, **extra: Any) -> Any:
        request: dict[str, Any] = {
            "system": self.system,
            "messages": self.messages,
            "cache_control": {"type": "ephemeral"},
            **extra,
        }
        if self.tools:
            request["tools"] = [tool.spec() for tool in self.tools.values()]
        response = self.llm.send(self.role, self.workspace, tag=self.tag, **request)
        self.messages.append({"role": "assistant", "content": response.content})
        return response

    def _add_user_text(self, text: str) -> None:
        block = {"type": "text", "text": text}
        last = self.messages[-1] if self.messages else None
        # Tool results and the next instruction share one user turn.
        if last and last["role"] == "user":
            last["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})

    def run_tools(self, user: str, max_tool_calls: int) -> None:
        """Let the model work with its tools until it stops or the budget is spent."""
        self._add_user_text(user)
        while True:
            response = self._send()
            calls = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                return
            results = [self._run_tool(block, max_tool_calls) for block in calls]
            self.messages.append({"role": "user", "content": results})
            if self.tool_calls_used >= max_tool_calls:
                return

    def _run_tool(self, block: Any, max_tool_calls: int) -> dict[str, Any]:
        is_error = False
        if self.tool_calls_used >= max_tool_calls:
            content, is_error = "Tool budget exhausted; this call was not run.", True
        elif block.name not in self.tools:
            content, is_error = f"Unknown tool: {block.name}", True
        else:
            self.tool_calls_used += 1
            try:
                if tracing.current():
                    with tracing.span(self.llm.engine, block.name, "tool", self.workspace or ""):
                        content = self.tools[block.name].fn(**block.input)
                else:
                    content = self.tools[block.name].fn(**block.input)
            except Exception as exc:
                content, is_error = f"{type(exc).__name__}: {exc}", True
        remaining = max(0, max_tool_calls - self.tool_calls_used)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"{content}\n\n[tool calls remaining: {remaining}]",
            "is_error": is_error,
        }

    def extract(self, user: str, output_model: type[T]) -> T:
        """Ask for one structured output based on the conversation so far."""
        self._add_user_text(user)
        extra: dict[str, Any] = {"output_format": output_model}
        if self.tools:
            extra["tool_choice"] = {"type": "none"}
        return self._send(**extra).parsed_output


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
