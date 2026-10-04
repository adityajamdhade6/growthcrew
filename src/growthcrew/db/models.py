from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class LLMCall(SQLModel, table=True):
    """One row per request sent to the model, including failed attempts."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now, index=True)
    agent: str = Field(index=True)
    workspace: str | None = Field(default=None, index=True)
    # What the call was for, e.g. "linkedin_post|01-day02-linkedin_post". Prices each piece.
    tag: str | None = Field(default=None, index=True)
    # Short hash of the system prompt, so results can be compared across prompt changes.
    prompt_version: str = ""
    # The model that served the response, which differs from the requested
    # model when a refusal fallback ran.
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # None when the model has no entry in config.PRICING.
    cost_usd: float | None = None
    latency_ms: int = 0
    stop_reason: str | None = None
    success: bool = True
    error: str | None = None


class BrainVersion(SQLModel, table=True):
    """One row per saved version of a workspace's brain."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    version: int
    note: str = ""
    # Comma-separated field paths that differ from the previous version.
    changed_fields: str = ""
    data: str


class ContentRevision(SQLModel, table=True):
    """One row per critic round of a content piece."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    piece_id: str = Field(index=True)
    content_type: str
    angle: str | None = None
    round: int
    min_score: int
    passed: bool
    scores: str
    text: str


class Cycle(SQLModel, table=True):
    """One weekly cycle for a workspace. `stage` is the state machine's current state."""

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(index=True)
    week_start: datetime
    stage: str = "research"
    # Set when the cycle stopped early (budget, error, everything rejected).
    halted_reason: str | None = None
    # Data one stage hands to the next (new research sources, then the content plan).
    state_json: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Task(SQLModel, table=True):
    """One step of a cycle."""

    id: int | None = Field(default=None, primary_key=True)
    cycle_id: int = Field(index=True)
    workspace: str
    stage: str
    # running, done, skipped, failed, blocked
    status: str = "running"
    detail: str = ""
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class AgentRun(SQLModel, table=True):
    """What one agent spent during one task."""

    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(index=True)
    cycle_id: int = Field(index=True)
    workspace: str
    agent: str
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


class Draft(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    cycle_id: int = Field(index=True)
    workspace: str = Field(index=True)
    piece_id: str
    content_type: str
    angle: str | None = None
    day: int | None = None
    # The text as the agents left it, and the current text after any human edit.
    original_text: str
    text: str
    body_json: str
    metadata_json: str
    min_score: int
    passed_critic: bool
    # Every version with its critique, as JSON, for the review panel.
    history_json: str = "[]"
    # Which writer prompt and which strategy produced this draft.
    prompt_version: str = ""
    strategy_version: int = 0
    # The past winners and playbook rules the writer was given, as JSON.
    memory_json: str = "{}"
    # pending_approval, approved, rejected
    status: str = Field(default="pending_approval", index=True)
    created_at: datetime = Field(default_factory=_now)

    @property
    def tracking_key(self) -> str:
        """Put this in utm_content, the campaign name or the ad name to tie metrics back."""
        return f"gc-{self.cycle_id}-{self.piece_id}"


class Approval(SQLModel, table=True):
    """A human decision on a draft."""

    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True)
    # approved, rejected, edited (edited means approved with the reviewer's changes)
    decision: str
    reviewer: str
    comment: str = ""
    diff: str = ""
    created_at: datetime = Field(default_factory=_now)


class CalendarItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True)
    cycle_id: int = Field(index=True)
    workspace: str = Field(index=True)
    scheduled_for: datetime
    channel: str
    # scheduled, published, measured
    status: str = "scheduled"
    published_at: datetime | None = None
    published_via: str | None = None
    published_by: str | None = None
    # Where it went live; used to match analytics rows back to the piece.
    published_url: str | None = None
    metrics_json: str = ""


class EditPattern(SQLModel, table=True):
    """A kind of change the reviewer keeps making to drafts."""

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(index=True)
    key: str
    rule: str
    count: int = 0
    # counting, proposed (waiting for a person), accepted (written into the voice guide),
    # rejected
    status: str = "counting"
    # True once the rule has been written into the brand voice guide.
    applied: bool = False


class WorkspaceBudget(SQLModel, table=True):
    workspace: str = Field(primary_key=True)
    weekly_limit_usd: float


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    kind: str
    message: str


class PerformanceRow(SQLModel, table=True):
    """One normalised row from an uploaded analytics export."""

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(index=True)
    source: str
    # What the export called this item (title, URL, campaign or ad name).
    ref: str
    # None for exports that only give lifetime totals per item.
    date: datetime | None = None
    # The content piece this row belongs to, or None if it could not be matched.
    draft_id: int | None = Field(default=None, index=True)
    impressions: float = 0
    clicks: float = 0
    engagements: float = 0
    sessions: float = 0
    conversions: float = 0
    sends: float = 0
    opens: float = 0
    replies: float = 0
    unsubscribes: float = 0
    spend: float = 0
    uploaded_at: datetime = Field(default_factory=_now)


class Learnings(SQLModel, table=True):
    """A saved WeeklyLearnings and whether the strategist has ruled on its changes."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    data: str
    reviewed: bool = False


class ChangeDecision(SQLModel, table=True):
    """The strategist's ruling on one proposed change."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    learnings_id: int = Field(index=True)
    cycle_id: int | None = Field(default=None, index=True)
    change_json: str
    # accepted, rejected
    decision: str
    reason: str


class GuardrailBlock(SQLModel, table=True):
    """One guardrail violation that stopped a draft from passing."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    piece_id: str
    # "draft" (caught in the writer/critic loop) or "edit" (caught on a human edit)
    stage: str
    rule: str
    line: int
    excerpt: str
    reason: str


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    # Comma-separated workspace names, or "*" for every workspace (admin).
    workspaces: str = ""
    created_at: datetime = Field(default_factory=_now)

    def can_access(self, workspace: str) -> bool:
        return self.is_admin or workspace in self.workspaces.split(",")

    @property
    def is_admin(self) -> bool:
        return self.workspaces.strip() == "*"


class OnboardingJob(SQLModel, table=True):
    """Progress of building a workspace's brain, for the onboarding screen."""

    workspace: str = Field(primary_key=True)
    url: str
    # crawling, drafting, done, failed
    status: str = "crawling"
    detail: str = ""
    updated_at: datetime = Field(default_factory=_now)


class StrategyComment(SQLModel, table=True):
    """A reviewer's comment on a strategy section, asking the strategist to revise it."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    section: str
    comment: str
    author: str
    # pending, revised, failed
    status: str = "pending"
    response: str = ""


class RoleModel(SQLModel, table=True):
    """A model choice made in Settings, overriding config.AGENT_MODELS for one role."""

    role: str = Field(primary_key=True)
    model: str


class ExperimentRegistration(SQLModel, table=True):
    """An experiment's pre-registration: what was committed to before it started."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    workspace: str = Field(index=True)
    cycle_id: int = Field(index=True)
    # The piece the variants belong to, e.g. "07-day03-ad".
    experiment: str
    # A growthcrew.experiments.Preregistration, as JSON. Never edited after the test starts.
    data: str
    # The readout from the first judgement at the planned sample. Once set, it is the answer.
    verdict: str | None = None


class MemoryPiece(SQLModel, table=True):
    """A published piece with its final performance: the brand's long-term content memory."""

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(index=True)
    draft_id: int = Field(index=True, unique=True)
    content_type: str = Field(index=True)
    published_on: datetime
    text: str
    pillar: str = ""
    angle: str | None = None
    persona: str = ""
    hypothesis: str = ""
    metric: str
    trials: int
    successes: int
    rate: float
    # This piece's rate divided by the average for its content type in the workspace.
    score: float = 1.0
    # Features of the text the pattern miner looks at, as JSON {name: bool}.
    features: str = "{}"
    # The text's embedding, as a JSON list of floats.
    embedding: str = "[]"
    prompt_version: str = ""
    strategy_version: int = 0


class PlaybookRule(SQLModel, table=True):
    """A pattern the miner found in this brand's own results."""

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(index=True)
    content_type: str
    feature: str
    statement: str
    # candidate, active, weakening, retired
    status: str = "candidate"
    lift_pct: float = 0.0
    probability: float = 0.0
    pieces_with: int = 0
    pieces_without: int = 0
    found_on: datetime
    updated_on: datetime
    # Consecutive runs in which the evidence held, or failed to.
    held_runs: int = 0
    failed_runs: int = 0


class RuleEvent(SQLModel, table=True):
    """One entry in a rule's history: what the miner saw on one run."""

    id: int | None = Field(default=None, primary_key=True)
    rule_id: int = Field(index=True)
    at: datetime
    status: str
    lift_pct: float
    probability: float
    pieces_with: int
    pieces_without: int
    note: str = ""
