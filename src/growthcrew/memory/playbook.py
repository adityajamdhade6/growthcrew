"""The playbook: active rules, their history, and how agents are given them."""

import json
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew.db.models import MemoryPiece, PlaybookRule, RuleEvent
from growthcrew.memory import store
from growthcrew.memory.features import BY_KEY


class MemoryUsed(BaseModel):
    """What the writer was given from memory for one piece. Stored with the draft."""

    examples: list[store.Example] = []
    rules: list[str] = []


def rules(engine: Engine, workspace: str, status: str | None = None) -> list[PlaybookRule]:
    query = select(PlaybookRule).where(PlaybookRule.workspace == workspace)
    if status:
        query = query.where(PlaybookRule.status == status)
    with Session(engine) as session:
        return list(session.exec(query.order_by(PlaybookRule.probability.desc())))


def describe(rule: PlaybookRule) -> str:
    sure = "over 99%" if rule.probability > 0.99 else f"{rule.probability:.0%}"
    return (
        f"{rule.statement}: +{abs(rule.lift_pct):.0f}% across "
        f"{rule.pieces_with + rule.pieces_without} pieces, {sure} probability"
    )


def writer_context(
    engine: Engine, workspace: str, content_type: str, query: str
) -> tuple[str, MemoryUsed]:
    """What the writer is shown before drafting: this brand's own winners and active rules."""
    examples = store.best_similar(engine, workspace, content_type, query)
    active = [r for r in rules(engine, workspace, "active") if r.content_type == content_type]
    used = MemoryUsed(examples=examples, rules=[describe(rule) for rule in active])
    if not examples and not active:
        return "", used
    lines = ["What has worked for this brand before (from its own published results):"]
    for number, example in enumerate(examples, 1):
        result = f"{example.rate_pct}% {example.metric}, {example.score:.1f}x the brand average"
        lines.append(
            f'<past_winner number="{number}" result="{result}">\n{example.text}\n</past_winner>'
        )
    if active:
        lines.append("Playbook rules currently holding:")
        lines += [f"- {text}" for text in used.rules]
    lines.append(
        "Learn from the structure and angle of the winners; do not copy their sentences. Follow "
        "the playbook rules unless the request gives a reason not to."
    )
    return "\n".join(lines), used


def check_draft(engine: Engine, workspace: str, content_type: str, text: str) -> list[str]:
    """Where a draft goes against an active rule. Advice for the editor, not a block."""
    from growthcrew.memory.features import extract

    features = extract(text)
    findings = []
    for rule in rules(engine, workspace, "active"):
        if rule.content_type != content_type:
            continue
        wants = rule.lift_pct >= 0
        if features.get(rule.feature) != wants:
            feature = BY_KEY[rule.feature]
            has = feature.with_it if features.get(rule.feature) else feature.without_it
            findings.append(f"Playbook: this draft uses {has}. {describe(rule)}.")
    return findings


def history(engine: Engine, rule_id: int) -> list[RuleEvent]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(RuleEvent).where(RuleEvent.rule_id == rule_id).order_by(RuleEvent.at)
            )
        )


def by_version(engine: Engine, workspace: str) -> list[dict]:
    """Performance per writer-prompt version and strategy version."""
    with Session(engine) as session:
        rows = session.exec(
            select(
                MemoryPiece.prompt_version,
                MemoryPiece.strategy_version,
                MemoryPiece.content_type,
                func.count(MemoryPiece.id),
                func.sum(MemoryPiece.successes),
                func.sum(MemoryPiece.trials),
            )
            .where(MemoryPiece.workspace == workspace)
            .group_by(
                MemoryPiece.prompt_version, MemoryPiece.strategy_version, MemoryPiece.content_type
            )
        ).all()
    return [
        {
            "prompt_version": prompt or "unversioned",
            "strategy_version": strategy,
            "content_type": content_type,
            "pieces": count,
            "rate_pct": round(successes / trials * 100, 2) if trials else 0.0,
        }
        for prompt, strategy, content_type, count, successes, trials in rows
    ]


def summary(engine: Engine, workspace: str, as_of: datetime | None = None, days: int = 90) -> dict:
    """Everything the Playbook page shows."""
    all_rules = rules(engine, workspace)
    as_of = as_of or max((rule.updated_on for rule in all_rules), default=datetime.now())
    since = as_of - timedelta(days=days)
    remembered = store.pieces(engine, workspace)
    recent = [rule for rule in all_rules if rule.found_on >= since]

    def row(rule: PlaybookRule) -> dict:
        return {
            **json.loads(rule.model_dump_json()),
            "summary": describe(rule),
            "history": [json.loads(event.model_dump_json()) for event in history(engine, rule.id)],
        }

    return {
        "as_of": as_of.date().isoformat(),
        "pieces_remembered": len(remembered),
        "active": [row(rule) for rule in all_rules if rule.status == "active"],
        "weakening": [row(rule) for rule in all_rules if rule.status == "weakening"],
        "candidate": [row(rule) for rule in all_rules if rule.status == "candidate"],
        "retired": [row(rule) for rule in all_rules if rule.status == "retired"],
        "learned": {
            "days": days,
            "found": len(recent),
            "still_active": sum(rule.status == "active" for rule in recent),
            "retired": sum(rule.status == "retired" for rule in recent),
        },
        "by_version": by_version(engine, workspace),
    }
