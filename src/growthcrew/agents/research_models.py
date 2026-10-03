"""Research outputs. Every claim carries the URL it came from."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from growthcrew.schemas import Claim

Confidence = Literal["low", "medium", "high"]
Verdict = Literal["supported", "partially_supported", "not_supported"]


class CompetitorTeardown(BaseModel):
    name: str
    url: str
    positioning_statement: Claim | None
    target_audience: Claim | None
    key_messages: list[Claim]
    pricing: list[Claim]
    offers: list[Claim]
    channels: list[Claim]
    content_themes: list[Claim]
    strengths: list[Claim]
    gaps: list[Claim]


class Teardowns(BaseModel):
    competitors: list[CompetitorTeardown]


class Quote(BaseModel):
    """A customer's exact words, copied verbatim from the source."""

    text: str
    source_url: str


class QuoteTheme(BaseModel):
    theme: str
    quotes: list[Quote]

    @computed_field
    @property
    def frequency(self) -> int:
        # Counted from the quotes that survived verification, never estimated.
        return len(self.quotes)


class VoiceOfCustomer(BaseModel):
    top_pains: list[Claim]
    desired_outcomes: list[Claim]
    objections: list[Claim]
    themes: list[QuoteTheme]


class MarketSignals(BaseModel):
    trends: list[Claim]
    seasonality: list[Claim]
    search_demand_themes: list[Claim]


class Learning(BaseModel):
    insight: str
    why_it_matters: str
    confidence: Confidence
    source_urls: list[str]


class BriefDraft(BaseModel):
    headline: str
    learnings: list[Learning]
    open_questions: list[str]


class ClaimCheck(BaseModel):
    statement: str
    source_url: str
    verdict: Verdict
    explanation: str


class ResearchBrief(BriefDraft):
    verification: list[ClaimCheck] = []
    claims_dropped: int = 0
    quotes_dropped: int = 0
    tool_calls_used: int = 0
    sources_read: int = 0

    def to_markdown(self, title: str) -> str:
        lines = [f"# Research brief: {title}", "", f"**{self.headline}**", ""]
        lines.append("## The 5 most important things we learned")
        for number, item in enumerate(self.learnings, 1):
            lines += [
                f"{number}. **{item.insight}** ({item.confidence} confidence)",
                f"   Why it matters: {item.why_it_matters}",
                *(f"   - {url}" for url in item.source_urls),
            ]
        if self.open_questions:
            lines += ["", "## Open questions", *(f"- {q}" for q in self.open_questions)]
        lines += ["", "## Verification"]
        mismatches = [c for c in self.verification if c.verdict != "supported"]
        lines.append(
            f"{len(self.verification)} random claims re-checked against their sources, "
            f"{len(mismatches)} flagged."
        )
        for check in self.verification:
            flag = "OK" if check.verdict == "supported" else "MISMATCH"
            lines.append(f"- [{flag}] {check.statement} ({check.source_url}): {check.explanation}")
        lines += [
            "",
            f"{self.tool_calls_used} tool calls, {self.sources_read} sources read. "
            f"Dropped as uncited or not verbatim: {self.claims_dropped} claims, "
            f"{self.quotes_dropped} quotes.",
        ]
        return "\n".join(lines)


def collect_claims(node: object) -> list[Claim]:
    """Every Claim anywhere inside a research output."""
    if isinstance(node, Claim):
        return [node]
    if isinstance(node, list):
        return [claim for item in node for claim in collect_claims(item)]
    if isinstance(node, BaseModel):
        return [c for name in type(node).model_fields for c in collect_claims(getattr(node, name))]
    return []


class ResearchReport(BaseModel):
    workspace: str
    focus: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    teardowns: list[CompetitorTeardown]
    voice_of_customer: VoiceOfCustomer
    market_signals: MarketSignals
    brief: ResearchBrief

    def claims(self) -> list[Claim]:
        return collect_claims([self.teardowns, self.voice_of_customer, self.market_signals])
