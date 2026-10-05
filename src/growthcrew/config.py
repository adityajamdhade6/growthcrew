"""Central configuration. Swap models, effort and pricing here and nowhere else."""

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Model IDs, checked against the Anthropic model list on 2026-10-03.
FABLE = "claude-fable-5-1"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5"


class AgentRole(StrEnum):
    RESEARCH = "research"
    STRATEGIST = "strategist"
    CONTENT = "content"
    CRITIC = "critic"
    ANALYST = "analyst"
    ORCHESTRATOR = "orchestrator"
    ONBOARDING = "onboarding"
    VERIFIER = "verifier"
    JUDGE = "judge"
    MONITOR = "monitor"
    CREATIVE = "creative"
    VISION_CRITIC = "vision_critic"
    PANEL = "panel"


@dataclass(frozen=True)
class RoleConfig:
    model: str
    max_tokens: int = 16000
    # Haiku 4.5 rejects the effort parameter, so set effort=None when using it.
    effort: Effort | None = "medium"


# Every role starts on Opus 5.5. Change one line to move a role to SONNET or
# HAIKU once the evals show quality holds.
AGENT_MODELS: dict[AgentRole, RoleConfig] = {
    AgentRole.RESEARCH: RoleConfig(model=OPUS, effort="medium"),
    AgentRole.STRATEGIST: RoleConfig(model=OPUS, max_tokens=20000, effort="high"),
    AgentRole.CONTENT: RoleConfig(model=OPUS, effort="medium"),
    AgentRole.CRITIC: RoleConfig(model=OPUS, effort="high"),
    AgentRole.ANALYST: RoleConfig(model=OPUS, effort="medium"),
    AgentRole.ORCHESTRATOR: RoleConfig(model=OPUS, effort="low"),
    AgentRole.ONBOARDING: RoleConfig(model=OPUS, effort="medium"),
    AgentRole.VERIFIER: RoleConfig(model=OPUS, effort="medium"),
    # The eval judge. Keep it fixed between runs, or scores are not comparable.
    AgentRole.JUDGE: RoleConfig(model=OPUS, effort="high"),
    # The always-on competitor, SEO and social monitors.
    AgentRole.MONITOR: RoleConfig(model=OPUS, effort="medium"),
    # Fills the slots of ad templates, and the critic that looks at the rendered images.
    AgentRole.CREATIVE: RoleConfig(model=OPUS, effort="medium"),
    AgentRole.VISION_CRITIC: RoleConfig(model=OPUS, effort="high"),
    # The synthetic panel: one call per persona per test, so the cheapest role to move to
    # SONNET or HAIKU once its calibration shows quality holds.
    AgentRole.PANEL: RoleConfig(model=OPUS, effort="low"),
}


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        # 5-minute cache writes cost 1.25x base input.
        return self.input * 1.25


PRICING: dict[str, ModelPrice] = {
    FABLE: ModelPrice(input=10.00, output=50.00, cache_read=0.25),
    OPUS: ModelPrice(input=4.00, output=20.00, cache_read=0.20),
    SONNET: ModelPrice(input=2.00, output=10.00, cache_read=0.20),
    HAIKU: ModelPrice(input=1.00, output=5.00, cache_read=0.10),
}

# When a safety classifier declines a request, let the API re-run it on a
# fallback model inside the same call. Cost is logged against the model that
# actually served the response.
REFUSAL_FALLBACKS = True

LLM_MAX_ATTEMPTS = 4

# Default spend cap per workspace per week, in USD. Override per workspace via the API.
WEEKLY_BUDGET_USD = float(os.getenv("GROWTHCREW_WEEKLY_BUDGET_USD", "25"))

# Optional hard cap on all model spend, ever, across every workspace. Set it on a public demo.
_total = os.getenv("GROWTHCREW_TOTAL_BUDGET_USD")
TOTAL_BUDGET_USD: float | None = float(_total) if _total else None

# What you have actually been quoted per piece, in USD (low, high), for the cost dashboard.
# Empty by default: the dashboard makes no comparison until you put real quotes here, e.g.
#   {"blog_article": (250, 400), "linkedin_post": (60, 120)}
FREELANCER_RATES_USD: dict[str, tuple[float, float]] = {}

# How A/B tests are pre-registered when the variants are drafted: the primary metric, the rate
# assumed for planning, and the guardrail metrics. The rates are planning assumptions, not
# measurements; a workspace's own baseline should replace them once it has one.
EXPERIMENT_DEFAULTS: dict[str, tuple[str, float, tuple[str, ...]]] = {
    "ad": ("click-through rate", 0.01, ("cost per click",)),
    "landing_hero": ("conversion rate", 0.03, ()),
    "cold_email_sequence": ("reply rate", 0.03, ("unsubscribe rate",)),
}
# The smallest relative lift worth detecting, used to plan the sample size.
EXPERIMENT_MDE = 0.3

# Personas in the synthetic panel, and how many are written per model call.
PANEL_SIZE = int(os.getenv("GROWTHCREW_PANEL_SIZE", "24"))
PANEL_BATCH = 8

# Hard cap on tool calls in one research run.
RESEARCH_MAX_TOOL_CALLS = 25

# Optional AI imagery for ad backgrounds. Off unless GROWTHCREW_AI_IMAGES=1 and a key is set.
# Every generated image is labelled as AI-generated in its metadata and in the review panel.
AI_IMAGES = os.getenv("GROWTHCREW_AI_IMAGES", "") == "1"
IMAGE_API_KEY = os.getenv("OPENAI_API_KEY", "")
IMAGE_MODEL = os.getenv("GROWTHCREW_IMAGE_MODEL", "gpt-image-1")

# A Chromium binary for rendering creatives, when Playwright's own is not installed.
CHROMIUM_PATH = os.getenv("GROWTHCREW_CHROMIUM_PATH", "")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///growthcrew.db")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "")


def price_for(model: str) -> ModelPrice | None:
    """Look up pricing, tolerating date-suffixed IDs returned by the API."""
    if model in PRICING:
        return PRICING[model]
    matches = [key for key in PRICING if model.startswith(key)]
    return PRICING[max(matches, key=len)] if matches else None
