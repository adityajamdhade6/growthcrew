from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator

from growthcrew.content.types import Angle, ContentRequest, Outline, PieceMetadata, SEOBrief
from growthcrew.guardrails import Violation
from growthcrew.memory.playbook import MemoryUsed
from growthcrew.schemas import ApprovalStatus

PASS_SCORE = 8
CRITERIA = ("voice", "clarity", "persuasion", "accuracy", "channel_fit", "ai_cliche")


class Score(BaseModel):
    score: int
    reason: str

    @field_validator("score")
    @classmethod
    def _clamp(cls, value: int) -> int:
        return min(10, max(1, value))


class LineEdit(BaseModel):
    line: int
    criterion: str
    # Filled in from the draft by code, so it always matches the real line.
    original: str = ""
    suggestion: str
    reason: str


class CritiqueDraft(BaseModel):
    """What the critic model returns."""

    voice: Score
    clarity: Score
    persuasion: Score
    accuracy: Score
    channel_fit: Score
    ai_cliche: Score
    edits: list[LineEdit]


class Critique(CritiqueDraft):
    # Findings from deterministic checks, which may have lowered the scores above.
    code_findings: list[str] = []

    def scores(self) -> dict[str, int]:
        return {name: getattr(self, name).score for name in CRITERIA}

    @computed_field
    @property
    def passed(self) -> bool:
        return min(self.scores().values()) >= PASS_SCORE


class Version(BaseModel):
    round: int
    metadata: PieceMetadata
    body: dict[str, Any]
    text: str
    critique: Critique | None = None
    guardrail_violations: list[Violation] = []


class PieceRecord(BaseModel):
    id: str
    day: int | None = None
    request: ContentRequest
    angle: Angle | None = None
    seo_brief: SEOBrief | None = None
    outline: Outline | None = None
    versions: list[Version] = []
    # The past winners and playbook rules the writer was shown, and the prompt that wrote it.
    memory: MemoryUsed = MemoryUsed()
    prompt_version: str = ""
    # Nothing is published automatically; a human approves the final version.
    status: ApprovalStatus = ApprovalStatus.PENDING

    @property
    def final(self) -> Version:
        return self.versions[-1]

    @computed_field
    @property
    def blocked(self) -> bool:
        """True when the final version still breaks a guardrail. It cannot be approved as is."""
        return bool(self.final.guardrail_violations)

    @computed_field
    @property
    def passed(self) -> bool:
        return bool(self.final.critique and self.final.critique.passed) and not self.blocked


class PlannedItem(BaseModel):
    day: int
    request: ContentRequest
    rationale: str


class BatchPlan(BaseModel):
    items: list[PlannedItem]


class Batch(BaseModel):
    workspace: str
    weeks: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    plan: list[PlannedItem]
    pieces: list[PieceRecord]
    notes: list[str] = []
