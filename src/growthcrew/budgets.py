"""Per-agent cost and latency limits on single model calls, with alerts.

`budget.py` caps what a workspace spends in a week and stops work. This module watches each
call: a call that costs or takes more than its agent's limit raises an alert, so a prompt
change that doubles an agent's cost is noticed the same day.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew import config
from growthcrew.db.models import Alert, LLMCall

logger = logging.getLogger(__name__)


def check_call(engine: Engine, call: LLMCall) -> str | None:
    """Alert when a call breaks its agent's limit. Returns the alert message, if any."""
    limits = config.AGENT_LIMITS.get(call.agent)
    if limits is None or not call.success:
        return None
    max_cost, max_ms = limits
    problems = []
    if call.cost_usd is not None and call.cost_usd > max_cost:
        problems.append(f"cost ${call.cost_usd:.2f} (limit ${max_cost:.2f})")
    if call.latency_ms > max_ms:
        problems.append(f"took {call.latency_ms / 1000:.0f}s (limit {max_ms / 1000:.0f}s)")
    if not problems:
        return None
    message = f"{call.agent} call over its limit: {' and '.join(problems)}"
    workspace = call.workspace or "*"
    since = datetime.now(UTC) - timedelta(days=1)
    with Session(engine) as session:
        recent = session.exec(
            select(Alert).where(
                Alert.workspace == workspace,
                Alert.kind == f"agent_limit:{call.agent}",
                Alert.created_at >= since,
            )
        ).first()
        if recent is None:
            session.add(
                Alert(workspace=workspace, kind=f"agent_limit:{call.agent}", message=message)
            )
            session.commit()
            logger.warning("ALERT %s", message)
    return message


def report(engine: Engine, days: int = 7) -> list[dict]:
    """Per agent over the last `days`: calls, mean and p95 cost and latency, against limits."""
    since = datetime.now(UTC) - timedelta(days=days)
    with Session(engine) as session:
        calls = session.exec(select(LLMCall).where(LLMCall.created_at >= since)).all()
    out = []
    for agent in sorted({call.agent for call in calls}):
        mine = [c for c in calls if c.agent == agent and c.success]
        if not mine:
            continue
        costs = sorted(c.cost_usd or 0.0 for c in mine)
        latency = sorted(c.latency_ms for c in mine)
        p95 = max(0, int(len(mine) * 0.95) - 1)
        max_cost, max_ms = config.AGENT_LIMITS.get(agent, (None, None))
        out.append({
            "agent": agent, "calls": len(mine),
            "mean_cost_usd": round(sum(costs) / len(costs), 4),
            "p95_cost_usd": round(costs[p95], 4),
            "mean_latency_ms": int(sum(latency) / len(latency)), "p95_latency_ms": latency[p95],
            "cost_limit_usd": max_cost, "latency_limit_ms": max_ms,
        })  # fmt: skip
    return out
