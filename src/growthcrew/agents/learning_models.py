import math
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from growthcrew.agents.content_models import BatchPlan
from growthcrew.analytics.analysis import Analysis
from growthcrew.content.templates import TEMPLATES
from growthcrew.content.types import ContentType

Confidence = Literal["low", "medium", "high"]


class Finding(BaseModel):
    statement: str
    # IDs from the analysis: readouts (r1), performance rows (p1), anomalies (a1).
    evidence: list[str]
    confidence: Confidence


class AnomalyNote(BaseModel):
    anomaly_id: str
    likely_causes: list[str]


class TargetStatus(BaseModel):
    kpi: str
    target: str
    actual: str
    status: Literal["ahead", "on_track", "behind", "no_data"]
    note: str


class ProposedChange(BaseModel):
    id: str = ""
    change: str
    rationale: str
    evidence: list[str]
    expected_effect: str
    # For a shift toward one angle; "none" for any other kind of change.
    prefer_angle: Literal["none", "pain", "outcome", "social_proof"]
    # Which content type the shift applies to; "any" for all.
    content_type: ContentType | Literal["any"]
    # Share of that content type's pieces to move, 0-100.
    share_pct: int
    # Set by code when the evidence does not support the change as written.
    blocked_reason: str = ""


class LearningsDraft(BaseModel):
    what_worked: list[Finding]
    what_didnt: list[Finding]
    anomaly_notes: list[AnomalyNote]
    vs_targets: list[TargetStatus]
    changes: list[ProposedChange]


class WeeklyLearnings(LearningsDraft):
    workspace: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    analysis: Analysis
    issues: list[str] = []


class Ruling(BaseModel):
    change_id: str
    decision: Literal["accepted", "rejected"]
    reason: str


class Rulings(BaseModel):
    rulings: list[Ruling]


def apply_changes(plan: BatchPlan, accepted: list[ProposedChange]) -> list[str]:
    """Apply accepted angle shifts to a content plan. Returns a note per shift made."""
    notes = []
    for change in accepted:
        if change.prefer_angle == "none":
            continue
        eligible = [
            item
            for item in plan.items
            if change.content_type in ("any", item.request.content_type)
            # A/B-tested types already get one variant per angle.
            and not TEMPLATES[item.request.content_type].ab_tested
            and item.request.ab_test is not True
            and item.request.angle is None
        ]
        count = min(len(eligible), math.ceil(len(eligible) * change.share_pct / 100))
        for item in eligible[:count]:
            item.request.angle = change.prefer_angle
        notes.append(
            f"{count} of {len(eligible)} eligible pieces moved to the {change.prefer_angle} "
            f"angle ({change.id}: {change.change})"
        )
    return notes
