"""The only place GrowthCrew talks to the model.

Every call goes through `LLM.call`, which retries transient failures, returns a
pydantic-validated object, and writes one `LLMCall` row per attempt with tokens
and cost. `LLM.conversation` does the same for multi-turn tool use.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from growthcrew import budget, config
from growthcrew.config import AgentRole
from growthcrew.db.models import LLMCall, RoleModel
from growthcrew.db.session import get_engine

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
    ) -> T:
        """Run one structured-output request for `role` and return the validated result."""
        response = self.send(
            role,
            workspace,
            tag=tag,
            system=system,
            messages=[{"role": "user", "content": user}],
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
        if workspace:
            # Stop before spending more once the workspace's weekly budget is used up.
            budget.check(self.engine, workspace)
        retrying = Retrying(
            retry=retry_if_exception_type(RETRYABLE),
            stop=stop_after_attempt(config.LLM_MAX_ATTEMPTS),
            wait=self._wait,
            reraise=True,
        )
        return retrying(self._attempt, role, workspace, tag, request)

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

        started = time.monotonic()
        try:
            response = method(**kwargs)
        except Exception as exc:
            self._log(
                LLMCall(
                    agent=role.value,
                    workspace=workspace,
                    tag=tag,
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
        with Session(self.engine) as session:
            session.add(row)
            session.commit()


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
