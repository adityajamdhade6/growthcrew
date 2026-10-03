"""Analyst: turns the week's computed numbers into WeeklyLearnings."""

from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.agents.learning_models import LearningsDraft, WeeklyLearnings
from growthcrew.agents.strategy_models import StrategyDoc
from growthcrew.analytics.analysis import Analysis, analyze
from growthcrew.config import AgentRole
from growthcrew.db.models import Learnings
from growthcrew.llm import LLM

WORKSPACES_DIR = Path("workspaces")
CHANGES = 3

SYSTEM = """You are the performance analyst on a small-business marketing team. Each week \
you read the numbers and tell the strategist what was learned and what to change. The \
statistics have already been computed for you; your job is to interpret them honestly.

You are given the strategy's KPIs and experiments, and an analysis with three tables. Each \
row has an ID you must cite:
- performance (p1, p2, ...): rate by pillar, angle, format and channel.
- readouts (r1, r2, ...): experiment results. `status` is the verdict of a two-proportion \
z-test: "significant", "no_significant_difference" or "not_enough_data". `kind` says whether \
it was a controlled A/B test or an observational comparison across different pieces.
- anomalies (a1, a2, ...): days far outside the usual level.

Rules:
- Only a readout with status "significant" has a winner. If the status is anything else, say \
there is no winner yet, however large the gap looks. Never name a winner from the performance \
table alone.
- Treat an observational readout as a lead to confirm with an A/B test, and give it at most \
medium confidence.
- what_worked and what_didnt: specific statements with the numbers, each citing evidence IDs.
- vs_targets: one entry per strategy KPI. If the uploaded data does not measure a KPI, use \
status "no_data"; do not estimate.
- anomaly_notes: for each anomaly, the most likely causes given what was published around \
that date. These are hypotheses, so word them that way.
- changes: exactly 3 concrete changes for next week, each with a number in it. When a \
significant readout has a winning angle, propose shifting a share of that content type to it: \
set prefer_angle, content_type and share_pct. When the data is insufficient, the right change \
is to keep the test running or enlarge it; set prefer_angle to "none" for those. Shift \
gradually (30 to 50 percent), since one week is one week."""


def check(draft: LearningsDraft, analysis: Analysis) -> list[str]:
    """Enforce what the numbers support. Returns notes on anything that was corrected."""
    readouts = {readout.id: readout for readout in analysis.readouts}
    known = set(readouts) | {row.id for row in analysis.performance}
    known |= {anomaly.id for anomaly in analysis.anomalies}
    issues = []

    for finding in [*draft.what_worked, *draft.what_didnt]:
        finding.evidence = [ref for ref in finding.evidence if ref in known]
        cited = [readouts[ref] for ref in finding.evidence if ref in readouts]
        significant = [r for r in cited if r.status == "significant"]
        if not significant and finding.confidence != "low":
            # No significant test behind it: at most a tentative observation.
            finding.confidence = "low"
        elif significant and all(r.kind == "observational" for r in significant):
            if finding.confidence == "high":
                finding.confidence = "medium"

    for number, change in enumerate(draft.changes, 1):
        change.id = f"c{number}"
        change.evidence = [ref for ref in change.evidence if ref in known]
        change.share_pct = min(100, max(0, change.share_pct))
        if change.prefer_angle == "none":
            continue
        backed = any(
            readouts[ref].status == "significant" and readouts[ref].winner == change.prefer_angle
            for ref in change.evidence
            if ref in readouts
        )
        if not backed:
            change.blocked_reason = (
                f"No significant readout shows '{change.prefer_angle}' winning, so this shift "
                "cannot be applied"
            )
            issues.append(f"{change.id}: {change.blocked_reason}")
    if len(draft.changes) != CHANGES:
        issues.append(f"Expected {CHANGES} changes, got {len(draft.changes)}")
    known_anomalies = {anomaly.id for anomaly in analysis.anomalies}
    draft.anomaly_notes = [n for n in draft.anomaly_notes if n.anomaly_id in known_anomalies]
    return issues


class AnalystAgent:
    role = AgentRole.ANALYST

    def __init__(self, llm: LLM, root: Path = WORKSPACES_DIR) -> None:
        self.llm = llm
        self.root = root

    def run(
        self, workspace: str, strategy: StrategyDoc, engine: Engine | None = None
    ) -> WeeklyLearnings:
        engine = engine or self.llm.engine
        analysis = analyze(engine, workspace)
        if not analysis.rows_used:
            raise ValueError(
                "No performance rows matched a published piece. Upload an export first, and "
                "use each piece's tracking key or published URL so rows can be matched."
            )
        kpis = "\n".join(
            f"- {kpi.metric}: baseline {kpi.baseline}, target {kpi.target} ({kpi.timeframe})"
            for kpi in strategy.kpis
        )
        experiments = "\n".join(
            f"- {e.name}: {e.hypothesis} Metric: {e.metric}. Minimum sample: {e.minimum_sample}. "
            f"Decision rule: {e.decision_rule}"
            for e in strategy.experiments
        )
        draft = self.llm.call(
            self.role,
            system=SYSTEM,
            user=f"Strategy KPIs:\n{kpis}\n\nStrategy experiments:\n{experiments}\n\n"
            f"Analysis:\n{analysis.model_dump_json(indent=2)}",
            output_model=LearningsDraft,
            workspace=workspace,
        )
        issues = check(draft, analysis)
        learnings = WeeklyLearnings(
            **draft.model_dump(), workspace=workspace, analysis=analysis, issues=issues
        )
        self.save(learnings, engine)
        return learnings

    def save(self, learnings: WeeklyLearnings, engine: Engine) -> None:
        data = learnings.model_dump_json(indent=2)
        folder = self.root / learnings.workspace / "learnings"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{learnings.created_at:%Y%m%d-%H%M%S}.json").write_text(data)
        with Session(engine) as session:
            session.add(Learnings(workspace=learnings.workspace, data=data))
            session.commit()
