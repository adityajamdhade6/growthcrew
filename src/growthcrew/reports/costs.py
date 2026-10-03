"""Cost dashboard data: spend per agent, per content piece and per week."""

from collections import defaultdict

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew import config
from growthcrew.budget import week_start
from growthcrew.db.models import LLMCall


def cost_summary(engine: Engine, workspace: str | None = None) -> dict:
    query = select(LLMCall)
    if workspace:
        query = query.where(LLMCall.workspace == workspace)
    with Session(engine) as session:
        calls = list(session.exec(query))

    agents: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost_usd": 0.0})
    weeks: dict[str, dict] = defaultdict(lambda: {"cost_usd": 0.0, "pieces": set()})
    pieces: dict[tuple[str, str], dict] = {}
    unpriced = 0
    for call in calls:
        cost = call.cost_usd or 0.0
        unpriced += call.cost_usd is None and call.success
        agent = agents[call.agent]
        agent["calls"] += 1
        agent["tokens"] += call.input_tokens + call.output_tokens
        agent["cost_usd"] += cost
        week = week_start(call.created_at).date().isoformat()
        weeks[week]["cost_usd"] += cost
        if call.tag and "|" in call.tag:
            content_type, piece_id = call.tag.split("|", 1)
            weeks[week]["pieces"].add((call.workspace, piece_id))
            piece = pieces.setdefault(
                (week, call.tag),
                {"week": week, "piece": piece_id, "content_type": content_type, "calls": 0,
                 "cost_usd": 0.0},
            )  # fmt: skip
            piece["calls"] += 1
            piece["cost_usd"] += cost

    by_type: dict[str, list[float]] = defaultdict(list)
    for piece in pieces.values():
        by_type[piece["content_type"]].append(piece["cost_usd"])
    content_types = []
    for content_type, costs in sorted(by_type.items()):
        average = sum(costs) / len(costs)
        low, high = config.FREELANCER_RATES_USD.get(content_type, (None, None))
        content_types.append(
            {
                "content_type": content_type,
                "pieces": len(costs),
                "avg_cost_usd": round(average, 4),
                "freelancer_low_usd": low,
                "freelancer_high_usd": high,
            }
        )
    return {
        "workspace": workspace,
        "total_cost_usd": round(sum(agent["cost_usd"] for agent in agents.values()), 4),
        "calls_without_pricing": int(unpriced),
        "by_agent": [
            {"agent": name, **{k: round(v, 4) if k == "cost_usd" else v for k, v in row.items()}}
            for name, row in sorted(agents.items(), key=lambda item: -item[1]["cost_usd"])
        ],
        "by_week": [
            {
                "week_start": week,
                "cost_usd": round(row["cost_usd"], 4),
                "pieces": len(row["pieces"]),
                # Everything spent that week (research and strategy included) per piece written.
                "fully_loaded_cost_per_piece_usd": round(row["cost_usd"] / len(row["pieces"]), 4)
                if row["pieces"]
                else None,
            }
            for week, row in sorted(weeks.items(), reverse=True)
        ],
        "by_content_type": content_types,
        "by_piece": sorted(
            ({**piece, "cost_usd": round(piece["cost_usd"], 4)} for piece in pieces.values()),
            key=lambda piece: (piece["week"], piece["piece"]),
            reverse=True,
        ),
    }
