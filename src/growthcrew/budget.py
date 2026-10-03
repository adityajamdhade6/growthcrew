"""Weekly spend guardrail per workspace."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew import config
from growthcrew.db.models import LLMCall, WorkspaceBudget


class BudgetExceeded(RuntimeError):
    pass


def week_start(now: datetime | None = None) -> datetime:
    """Monday 00:00 UTC of the current week."""
    now = now or datetime.now(UTC)
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def weekly_limit(engine: Engine, workspace: str) -> float:
    with Session(engine) as session:
        row = session.get(WorkspaceBudget, workspace)
    return row.weekly_limit_usd if row else config.WEEKLY_BUDGET_USD


def set_weekly_limit(engine: Engine, workspace: str, limit_usd: float) -> None:
    with Session(engine) as session:
        session.merge(WorkspaceBudget(workspace=workspace, weekly_limit_usd=limit_usd))
        session.commit()


def weekly_spend(engine: Engine, workspace: str) -> float:
    query = select(func.sum(LLMCall.cost_usd)).where(
        LLMCall.workspace == workspace, LLMCall.created_at >= week_start()
    )
    with Session(engine) as session:
        return float(session.exec(query).one() or 0.0)


def check(engine: Engine, workspace: str) -> None:
    """Raise if this workspace has used up its budget for the week."""
    if config.TOTAL_BUDGET_USD is not None:
        with Session(engine) as session:
            total = float(session.exec(select(func.sum(LLMCall.cost_usd))).one() or 0.0)
        if total >= config.TOTAL_BUDGET_USD:
            raise BudgetExceeded(
                f"This installation has spent ${total:.2f} in total; its cap is "
                f"${config.TOTAL_BUDGET_USD:.2f}"
            )
    spent, limit = weekly_spend(engine, workspace), weekly_limit(engine, workspace)
    if spent >= limit:
        raise BudgetExceeded(
            f"Workspace '{workspace}' has spent ${spent:.2f} this week; the limit is ${limit:.2f}"
        )
