from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from growthcrew.frameworks.funnel import FunnelPlan
from growthcrew.frameworks.jtbd import JobsToBeDone
from growthcrew.frameworks.messaging_house import MessagingHouse
from growthcrew.frameworks.positioning import Positioning
from growthcrew.frameworks.test_and_learn import Experiment
from growthcrew.schemas import ApprovalStatus


class EvidenceItem(BaseModel):
    """A brain fact or research finding the strategy may cite by `id`."""

    id: str
    text: str
    source_url: str = ""
    # confirmed / inferred for brain facts; high / medium / low for research learnings.
    quality: str = ""


class ICPPriority(BaseModel):
    segment: str
    rationale: str
    support: list[str]


class ContentPillar(BaseModel):
    name: str
    description: str
    example_topics: list[str]
    support: list[str]


class KPI(BaseModel):
    metric: str
    baseline: str
    target: str
    timeframe: str
    support: list[str]


class Priorities(BaseModel):
    """The parts of the strategy that no single framework produces."""

    icp_priorities: list[ICPPriority]
    content_pillars: list[ContentPillar]
    kpis: list[KPI]


class CritiquePoint(BaseModel):
    section: str
    issue: str
    why_it_matters: str
    suggested_fix: str
    severity: Literal["low", "medium", "high"]


class Critique(BaseModel):
    summary: str
    weak_assumptions: list[CritiquePoint]
    missing_risks: list[CritiquePoint]


class Change(BaseModel):
    critique_issue: str
    decision: Literal["accepted", "partly_accepted", "rejected"]
    change_made: str
    reason: str


class RevisionLog(BaseModel):
    changes: list[Change]


class StrategyCore(BaseModel):
    jobs: JobsToBeDone
    positioning: Positioning
    messaging_house: MessagingHouse
    channel_plan: FunnelPlan
    experiments: list[Experiment]
    icp_priorities: list[ICPPriority]
    content_pillars: list[ContentPillar]
    kpis: list[KPI]


class StrategyDoc(StrategyCore):
    workspace: str
    brand_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: ApprovalStatus = ApprovalStatus.PENDING
    evidence: list[EvidenceItem]
    # The first draft, kept so the effect of the critique is visible.
    first_draft: StrategyCore
    critique: Critique
    revision: RevisionLog
    # Problems found by code checks that survived the revision.
    issues: list[str] = []
