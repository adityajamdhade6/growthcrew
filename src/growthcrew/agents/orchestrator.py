"""Orchestrator: the weekly cycle as a state machine.

Automated stages run in order until `awaiting_approval`, where the cycle stops. The stages
after that are reached only through the human actions in `workflow.py`.
"""

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew import budget
from growthcrew.agents.content import ContentAgent
from growthcrew.agents.content_models import Batch, BatchPlan
from growthcrew.agents.learning_models import ProposedChange, WeeklyLearnings, apply_changes
from growthcrew.agents.research import ResearchAgent, ResearchInput, load_latest_research
from growthcrew.agents.research_models import ResearchReport
from growthcrew.agents.strategist import StrategistAgent, StrategyInput, load_latest_strategy
from growthcrew.analytics import registry
from growthcrew.brain.store import WORKSPACES_DIR, load_brain
from growthcrew.db.models import (
    AgentRun,
    Alert,
    ChangeDecision,
    Cycle,
    Draft,
    Learnings,
    LLMCall,
    OnboardingJob,
    StrategyComment,
    Task,
)
from growthcrew.db.session import get_engine
from growthcrew.llm import LLM, cache_scope
from growthcrew.memory import miner
from growthcrew.monitor import signals as monitor_signals
from growthcrew.monitor.run import MonitorRun, run_monitors
from growthcrew.naming import display, piece_name, plural
from growthcrew.panel.cycle import pretest_cycle
from growthcrew.reports.content import save_batch
from growthcrew.reports.strategy import save_strategy
from growthcrew.tracing import new_trace_id, span
from growthcrew.versions import strategy_version
from growthcrew.workflow import STAGES

logger = logging.getLogger(__name__)

AUTOMATED = STAGES[: STAGES.index("awaiting_approval")]


def draft_from_piece(piece, cycle_id: int, workspace: str, version: int) -> Draft:
    """The Draft row for one written piece, waiting for a person (or blocked by a guardrail)."""
    final = piece.final
    return Draft(
        cycle_id=cycle_id,
        workspace=workspace,
        piece_id=piece.id,
        content_type=piece.request.content_type,
        angle=piece.angle,
        day=piece.day,
        original_text=final.text,
        text=final.text,
        body_json=json.dumps(final.body),
        metadata_json=final.metadata.model_dump_json(),
        min_score=min(final.critique.scores().values()),
        passed_critic=piece.passed,
        prompt_version=piece.prompt_version,
        strategy_version=version,
        memory_json=piece.memory.model_dump_json(),
        history_json=json.dumps([v.model_dump(mode="json") for v in piece.versions]),
        status="blocked" if piece.blocked else "pending_approval",
    )


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _source_urls(report: ResearchReport | None) -> set[str]:
    return {claim.source_url for claim in report.claims()} if report else set()


class Orchestrator:
    def __init__(
        self,
        llm: LLM | None = None,
        engine: Engine | None = None,
        root: Path = WORKSPACES_DIR,
        research: ResearchAgent | None = None,
        strategist: StrategistAgent | None = None,
        content: ContentAgent | None = None,
        max_items: int = 5,
        monitors: Callable[[str], MonitorRun] | None = None,
        panel: Callable[[int], list[str]] | None = None,
    ) -> None:
        self.engine = engine or (llm.engine if llm else get_engine())
        self.root = root
        self.research = research or ResearchAgent(llm, root=root)
        self.strategist = strategist or StrategistAgent(llm, root=root)
        self.content = content or ContentAgent(llm, root=root)
        self.max_items = max_items
        # The weekly competitor, SEO and social monitors. Off when there is no model to call.
        if monitors is None and llm is not None:

            def monitors(workspace: str) -> MonitorRun:
                return run_monitors(llm, self.engine, workspace, self.root)

        self.monitors = monitors
        # The synthetic panel's pre-test of each new experiment. Off without a model.
        if panel is None and llm is not None:

            def panel(cycle_id: int) -> list[str]:
                return pretest_cycle(llm, self.engine, cycle_id, self.root)

        self.panel = panel

    def start_cycle(self, workspace: str) -> Cycle:
        with Session(self.engine, expire_on_commit=False) as session:
            cycle = Cycle(
                workspace=workspace, week_start=budget.week_start(), trace_id=new_trace_id()
            )
            session.add(cycle)
            session.commit()
            return cycle

    def run(self, cycle_id: int) -> Cycle:
        """Run automated stages from wherever the cycle is, until approval or a halt.

        Each run is a span in the cycle's trace; each stage is a span inside it.
        """
        with Session(self.engine, expire_on_commit=False) as session:
            cycle = session.get(Cycle, cycle_id)
            if not cycle.trace_id:
                cycle.trace_id = new_trace_id()
                session.add(cycle)
                session.commit()
        with (
            span(self.engine, f"weekly cycle {cycle_id}", "cycle", cycle.workspace,
                 trace_id=cycle.trace_id, cycle_id=cycle_id) as attrs,
            cache_scope(f"cycle:{cycle_id}"),
        ):  # fmt: skip
            result = self._run(cycle_id)
            attrs["stage"] = result.stage
            return result

    def _run(self, cycle_id: int) -> Cycle:
        while True:
            with Session(self.engine, expire_on_commit=False) as session:
                cycle = session.get(Cycle, cycle_id)
            if cycle.stage not in AUTOMATED:
                return cycle
            if not self._step(cycle):
                with Session(self.engine, expire_on_commit=False) as session:
                    return session.get(Cycle, cycle_id)

    # --- one step ---

    def _step(self, cycle: Cycle) -> bool:
        stage, workspace = cycle.stage, cycle.workspace
        with Session(self.engine, expire_on_commit=False) as session:
            task = Task(cycle_id=cycle.id, workspace=workspace, stage=stage)
            session.add(task)
            last_call = session.exec(select(func.max(LLMCall.id))).one() or 0
            session.commit()

        started = time.monotonic()
        with span(self.engine, stage, "stage", workspace) as attrs:
            status, detail, halted, state_json = self._attempt_stage(cycle, stage, workspace)
            attrs.update(status=status, detail=detail[:300])
        self._finish(cycle, task, stage, workspace, status, detail, halted, state_json,
                     last_call, started)  # fmt: skip
        return halted is None

    def _attempt_stage(self, cycle: Cycle, stage: str, workspace: str):
        status, halted, state_json = "done", None, None
        try:
            budget.check(self.engine, workspace)
            result = getattr(self, f"_{stage}")(cycle)
            detail = result if isinstance(result, str) else result[0]
            if isinstance(result, tuple):
                status = result[1] or status
                state_json = result[2] if len(result) > 2 else None
        except budget.BudgetExceeded as exc:
            status, detail, halted = "blocked", str(exc), f"budget: {exc}"
        except Exception as exc:
            logger.exception("Cycle %s failed at %s", cycle.id, stage)
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
            halted = f"{stage} failed: {detail}"
        return status, detail, halted, state_json

    def _finish(self, cycle, task, stage, workspace, status, detail, halted, state_json,
                last_call, started) -> None:  # fmt: skip
        duration = int((time.monotonic() - started) * 1000)
        with Session(self.engine) as session:
            task = session.get(Task, task.id)
            task.status, task.detail, task.finished_at = status, detail, datetime.now(UTC)
            session.add(task)
            self._record_runs(session, task, last_call, duration)
            row = session.get(Cycle, cycle.id)
            row.updated_at = datetime.now(UTC)
            if halted:
                row.halted_reason = halted
                if status == "blocked":
                    session.add(Alert(workspace=workspace, kind="budget", message=detail))
                    logger.warning("ALERT %s", detail)
            else:
                row.halted_reason = None
                row.stage = STAGES[STAGES.index(stage) + 1]
                if state_json is not None:
                    row.state_json = state_json
            session.add(row)
            session.commit()

    def _record_runs(self, session: Session, task: Task, last_call: int, duration: int) -> None:
        """One AgentRun per agent that made LLM calls during this task."""
        rows = session.exec(
            select(
                LLMCall.agent,
                func.count(LLMCall.id),
                func.sum(LLMCall.input_tokens),
                func.sum(LLMCall.output_tokens),
                func.sum(LLMCall.cost_usd),
                func.sum(LLMCall.latency_ms),
            )
            .where(LLMCall.id > last_call, LLMCall.workspace == task.workspace)
            .group_by(LLMCall.agent)
        ).all()
        for agent, calls, tokens_in, tokens_out, cost, latency in rows:
            session.add(
                AgentRun(
                    task_id=task.id,
                    cycle_id=task.cycle_id,
                    workspace=task.workspace,
                    agent=agent,
                    llm_calls=calls,
                    input_tokens=tokens_in or 0,
                    output_tokens=tokens_out or 0,
                    cost_usd=cost or 0.0,
                    duration_ms=latency or 0,
                )
            )
        if not rows:
            session.add(
                AgentRun(
                    task_id=task.id,
                    cycle_id=task.cycle_id,
                    workspace=task.workspace,
                    agent="orchestrator",
                    duration_ms=duration,
                )
            )

    # --- stage handlers: each returns a detail string, or (detail, status, state_json) ---

    def _previous_research(self, workspace: str) -> ResearchReport | None:
        try:
            return load_latest_research(workspace, self.root)
        except FileNotFoundError:
            return None

    def _research(self, cycle: Cycle):
        brand = load_brain(cycle.workspace, root=self.root)
        previous = self._previous_research(cycle.workspace)
        report = self.research.run(ResearchInput(brand=brand), cycle.workspace)
        new = sorted(_source_urls(report) - _source_urls(previous))
        detail = (
            f"{len(report.claims())} cited claims from {report.brief.sources_read} sources; "
            f"{len(new)} sources not seen in the previous research"
        )
        if self.monitors is not None:
            # A monitor failing never halts the cycle: the inbox is extra evidence, not a step
            # the strategy depends on.
            try:
                found = self.monitors(cycle.workspace)
                detail += f"; {plural(found.stored, 'new signal')} in the inbox"
                if found.failures:
                    detail += f" ({plural(len(found.failures), 'monitor')} failed)"
            except Exception as exc:  # noqa: BLE001 — budget included: the next step checks it
                logger.exception("Monitors failed for %s", cycle.workspace)
                detail += f"; monitors failed: {type(exc).__name__}"
        return detail, None, json.dumps({"new_sources": new, "first": previous is None})

    def _strategy_check(self, cycle: Cycle):
        workspace = cycle.workspace
        evidence = json.loads(cycle.state_json or "{}")
        new = evidence.get("new_sources", [])
        try:
            strategy = load_latest_strategy(workspace, self.root)
        except FileNotFoundError:
            strategy = None
        # The weekly memory job: remember measured pieces and re-test every playbook rule.
        miner.mine(self.engine, workspace, as_of=datetime.now(UTC))
        notes = []
        # Signals a person sent from the inbox since the strategy was written are new evidence.
        signals_sent = [
            signal
            for signal in monitor_signals.for_strategist(self.engine, workspace)
            if strategy is not None
            and signal.decided_at is not None
            and _aware(signal.decided_at) > _aware(strategy.created_at)
        ]
        if strategy is None or new or signals_sent:
            brand = load_brain(workspace, root=self.root)
            research = load_latest_research(workspace, self.root)
            reason = (
                "no strategy existed yet"
                if strategy is None
                else ", ".join(
                    part
                    for part in (
                        f"{len(new)} new sources" if new else "",
                        plural(len(signals_sent), "signal") + " sent from the inbox"
                        if signals_sent
                        else "",
                    )
                    if part
                )
            )
            strategy = self.strategist.run(StrategyInput(brand=brand, research=research), workspace)
            save_strategy(strategy, self.root)
            notes.append(f"Strategy written ({reason}); {len(strategy.issues)} open issues")
        notes += self._review_learnings(cycle, strategy)
        if not notes:
            return "No new evidence and no new learnings, so the strategy is unchanged", "skipped"
        return ". ".join(notes), None

    def _review_learnings(self, cycle: Cycle, strategy) -> list[str]:
        """Have the strategist rule on each change the analyst proposed, and log the rulings."""
        with Session(self.engine) as session:
            pending = session.exec(
                select(Learnings)
                .where(Learnings.workspace == cycle.workspace, Learnings.reviewed == False)  # noqa: E712
                .order_by(Learnings.id.desc())
            ).first()
        if pending is None:
            return []
        learnings = WeeklyLearnings.model_validate_json(pending.data)
        rulings, applied = self.strategist.review_changes(learnings, strategy, cycle.workspace)
        changes = {change.id: change for change in applied}
        with Session(self.engine) as session:
            for ruling in rulings:
                session.add(
                    ChangeDecision(
                        workspace=cycle.workspace,
                        learnings_id=pending.id,
                        cycle_id=cycle.id,
                        change_json=changes[ruling.change_id].model_dump_json(),
                        decision=ruling.decision,
                        reason=ruling.reason,
                    )
                )
            row = session.get(Learnings, pending.id)
            row.reviewed = True
            session.add(row)
            session.commit()
        accepted = sum(ruling.decision.startswith("accepted") for ruling in rulings)
        partial = sum(ruling.decision == "accepted_partial" for ruling in rulings)
        note = f" ({partial} in part, to confirm next week)" if partial else ""
        return [
            f"Strategist accepted {accepted} of {plural(len(rulings), 'change')} from last "
            f"week's learnings{note}"
        ]

    def _accepted_changes(self, cycle_id: int) -> list[ProposedChange]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(ChangeDecision).where(
                    ChangeDecision.cycle_id == cycle_id,
                    ChangeDecision.decision.in_(["accepted", "accepted_partial"]),
                )
            ).all()
        return [ProposedChange.model_validate_json(row.change_json) for row in rows]

    def _content_plan(self, cycle: Cycle):
        workspace = cycle.workspace
        brand = load_brain(workspace, root=self.root)
        strategy = load_latest_strategy(workspace, self.root)
        plan = self.content.plan_batch(brand, strategy, weeks=1, max_items=self.max_items)
        shifts = apply_changes(plan, self._accepted_changes(cycle.id))
        kinds = ", ".join(display(item.request.content_type, capital=False) for item in plan.items)
        detail = f"{plural(len(plan.items), 'item')} planned: {kinds}"
        if shifts:
            detail += ". Applied from learnings: " + "; ".join(shifts)
        return detail, None, plan.model_dump_json()

    def _drafting(self, cycle: Cycle):
        workspace = cycle.workspace
        brand = load_brain(workspace, root=self.root)
        strategy = load_latest_strategy(workspace, self.root)
        plan = BatchPlan.model_validate_json(cycle.state_json)
        batch = Batch(workspace=workspace, weeks=1, plan=plan.items, pieces=[])
        for number, item in enumerate(plan.items, 1):
            piece_id = f"{number:02d}-day{item.day:02d}-{item.request.content_type}"
            records, notes = self.content.produce(item.request, brand, strategy, piece_id, item.day)
            batch.pieces += records
            batch.notes += notes
            # Variants of one piece are an A/B test: register it before any results exist.
            angles = [record.angle for record in records if record.angle]
            registry.register_variants(
                self.engine, workspace, cycle.id, piece_id, item.request.content_type, angles,
                records[0].final.metadata.hypothesis if records else "",
            )  # fmt: skip
        save_batch(batch, self.root)
        version = strategy_version(workspace, self.root)
        with Session(self.engine) as session:
            for piece in batch.pieces:
                session.add(draft_from_piece(piece, cycle.id, workspace, version))
            session.commit()
        rounds = sum(len(piece.versions) for piece in batch.pieces)
        return (
            f"{plural(len(batch.pieces), 'piece')} written in "
            f"{plural(rounds, 'writer and editor round')}"
        )

    def _critic(self, cycle: Cycle):
        """Quality gate. The critic already scored each piece during drafting; this sums it up."""
        with Session(self.engine) as session:
            drafts = session.exec(select(Draft).where(Draft.cycle_id == cycle.id)).all()
        if not drafts:
            raise RuntimeError("Drafting produced no pieces")
        failed = [piece_name(draft.piece_id) for draft in drafts if not draft.passed_critic]
        detail = (
            f"{len(drafts) - len(failed)} of {plural(len(drafts), 'piece')} scored 8 or more "
            "on every criterion"
        )
        if failed:
            detail += f"; flagged for the reviewer: {', '.join(failed)}"
        blocked = [piece_name(draft.piece_id) for draft in drafts if draft.status == "blocked"]
        if blocked:
            detail += f"; blocked by guardrails: {', '.join(blocked)}"
        if self.panel is not None:
            # The pre-test is advice for the reviewer; a failure never holds the cycle back.
            try:
                notes = self.panel(cycle.id)
                if notes:
                    detail += "; " + "; ".join(notes)
            except Exception as exc:  # noqa: BLE001 — budget included: the reviewer is next
                logger.exception("Panel pre-test failed for cycle %s", cycle.id)
                detail += f"; panel pre-test failed: {type(exc).__name__}"
        return detail


def recover_interrupted(engine: Engine) -> int:
    """Close out work that a server restart cut off, so nothing shows as running forever.

    Background work runs inside the API process. If the process stops mid-step, that step can
    never finish; mark it failed and leave the cycle resumable. Returns how many were closed.
    """
    with Session(engine) as session:
        tasks = session.exec(select(Task).where(Task.status == "running")).all()
        for task in tasks:
            task.status, task.finished_at = "failed", datetime.now(UTC)
            task.detail = "Interrupted by a server restart"
            cycle = session.get(Cycle, task.cycle_id)
            cycle.halted_reason = (
                f"{task.stage} was interrupted by a server restart; resume to retry"
            )
            session.add_all([task, cycle])
        jobs = session.exec(
            select(OnboardingJob).where(OnboardingJob.status.in_(["crawling", "drafting"]))
        ).all()
        for job in jobs:
            job.status, job.detail = "failed", "Interrupted by a server restart. Start it again."
            session.add(job)
        comments = session.exec(
            select(StrategyComment).where(StrategyComment.status == "pending")
        ).all()
        for comment in comments:
            comment.status, comment.response = "failed", "Interrupted by a server restart"
            session.add(comment)
        session.commit()
        return len(tasks) + len(jobs) + len(comments)


def timeline(engine: Engine, cycle_id: int) -> dict:
    """What each agent did in a cycle, what it cost, and what is waiting on a human."""
    with Session(engine) as session:
        cycle = session.get(Cycle, cycle_id)
        if cycle is None:
            raise LookupError(f"Cycle {cycle_id} not found")
        tasks = session.exec(select(Task).where(Task.cycle_id == cycle_id).order_by(Task.id)).all()
        runs = session.exec(select(AgentRun).where(AgentRun.cycle_id == cycle_id)).all()
        drafts = session.exec(select(Draft).where(Draft.cycle_id == cycle_id)).all()
        alerts = session.exec(
            select(Alert).where(
                Alert.workspace == cycle.workspace, Alert.created_at >= cycle.created_at
            )
        ).all()
        steps = [
            {
                "stage": task.stage,
                "status": task.status,
                "detail": task.detail,
                "started_at": task.started_at.isoformat(),
                "seconds": round((task.finished_at - task.started_at).total_seconds(), 1)
                if task.finished_at
                else None,
                "agents": [
                    {
                        "agent": run.agent,
                        "llm_calls": run.llm_calls,
                        "input_tokens": run.input_tokens,
                        "output_tokens": run.output_tokens,
                        "cost_usd": round(run.cost_usd, 4),
                    }
                    for run in runs
                    if run.task_id == task.id
                ],
            }
            for task in tasks
        ]
        return {
            "cycle_id": cycle.id,
            "workspace": cycle.workspace,
            "stage": cycle.stage,
            "halted_reason": cycle.halted_reason,
            "total_cost_usd": round(sum(run.cost_usd for run in runs), 4),
            # Includes the step that is still running, which has no AgentRun yet.
            "live_cost_usd": round(
                session.exec(
                    select(func.sum(LLMCall.cost_usd)).where(
                        LLMCall.workspace == cycle.workspace, LLMCall.created_at >= cycle.created_at
                    )
                ).one()
                or 0.0,
                4,
            ),
            "weekly_spend_usd": round(budget.weekly_spend(engine, cycle.workspace), 4),
            "weekly_limit_usd": budget.weekly_limit(engine, cycle.workspace),
            "steps": steps,
            "awaiting_approval": [
                {
                    "draft_id": draft.id,
                    "piece": draft.piece_id,
                    "lowest_score": draft.min_score,
                    "passed_critic": draft.passed_critic,
                }
                for draft in drafts
                if draft.status == "pending_approval"
            ],
            "blocked_by_guardrails": [
                {"draft_id": draft.id, "piece": draft.piece_id}
                for draft in drafts
                if draft.status == "blocked"
            ],
            "alerts": [alert.message for alert in alerts],
        }


def render_timeline(data: dict) -> str:
    lines = [
        f"Cycle {data['cycle_id']} · {data['workspace']} · stage: {data['stage']}",
        f"Cost this cycle ${data['total_cost_usd']:.2f} · this week "
        f"${data['weekly_spend_usd']:.2f} of ${data['weekly_limit_usd']:.2f}",
    ]
    if data["halted_reason"]:
        lines.append(f"HALTED: {data['halted_reason']}")
    for step in data["steps"]:
        lines.append(f"\n[{step['status']}] {step['stage']} ({step['seconds']}s): {step['detail']}")
        for run in step["agents"]:
            lines.append(
                f"    {run['agent']}: {run['llm_calls']} calls, {run['input_tokens']} in / "
                f"{run['output_tokens']} out tokens, ${run['cost_usd']:.4f}"
            )
    lines.append(f"\nWaiting for your approval: {len(data['awaiting_approval'])}")
    for draft in data["awaiting_approval"]:
        flag = "" if draft["passed_critic"] else "  (did not pass the critic)"
        lines.append(
            f"    draft {draft['draft_id']}: {draft['piece']}, lowest score "
            f"{draft['lowest_score']}{flag}"
        )
    for draft in data.get("blocked_by_guardrails", []):
        lines.append(f"Blocked by guardrails: draft {draft['draft_id']} ({draft['piece']})")
    lines += [f"ALERT: {alert}" for alert in data["alerts"]]
    return "\n".join(lines)
