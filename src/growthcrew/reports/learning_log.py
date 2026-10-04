"""The learning log: how the strategy changed week by week, and why."""

import json

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.db.models import BrainVersion, ChangeDecision, Learnings, Task


def learning_log(engine: Engine, workspace: str) -> list[dict]:
    """Newest first: each week's findings, proposed changes, and the strategist's rulings."""
    with Session(engine) as session:
        learnings = session.exec(
            select(Learnings).where(Learnings.workspace == workspace).order_by(Learnings.id.desc())
        ).all()
        decisions = session.exec(
            select(ChangeDecision).where(ChangeDecision.workspace == workspace)
        ).all()
        rewrites = session.exec(
            select(Task).where(
                Task.workspace == workspace, Task.stage == "strategy_check", Task.status == "done"
            )
        ).all()
        voice = session.exec(
            select(BrainVersion).where(
                BrainVersion.workspace == workspace, BrainVersion.note.startswith("learned from")
            )
        ).all()

    entries = []
    for row in learnings:
        data = json.loads(row.data)
        rulings = {
            json.loads(d.change_json)["id"]: d for d in decisions if d.learnings_id == row.id
        }
        entries.append(
            {
                "kind": "learnings",
                "date": row.created_at.date().isoformat(),
                "window": f"{data['analysis']['window_start']} to {data['analysis']['window_end']}",
                "what_worked": data["what_worked"],
                "what_didnt": data["what_didnt"],
                "significant": [
                    r for r in data["analysis"]["readouts"] if r["status"] == "significant"
                ],
                "undecided": [
                    r for r in data["analysis"]["readouts"] if r["status"] != "significant"
                ],
                "changes": [
                    {
                        **change,
                        "decision": rulings[change["id"]].decision
                        if change["id"] in rulings
                        else "pending",
                        "reason": rulings[change["id"]].reason if change["id"] in rulings else "",
                        # The share actually applied, which is smaller for a partial shift.
                        "applied_share_pct": json.loads(rulings[change["id"]].change_json)[
                            "share_pct"
                        ]
                        if change["id"] in rulings
                        else None,
                    }
                    for change in data["changes"]
                ],
            }
        )
    for task in rewrites:
        entries.append(
            {"kind": "strategy", "date": task.started_at.date().isoformat(), "detail": task.detail}
        )
    for version in voice:
        entries.append(
            {"kind": "voice", "date": version.created_at.date().isoformat(), "detail": version.note}
        )
    return sorted(entries, key=lambda entry: entry["date"], reverse=True)
