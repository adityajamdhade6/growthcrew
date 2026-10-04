"""Twelve simulated weeks of LinkedIn posts, to test the miner against known answers.

Two patterns are built in:
- Real: a question as the hook lifts click-through by 40%, every week.
- Not real: a number in the hook looks 50% better for the first four weeks, then has no
  effect at all. That is what a lucky streak, or a pattern that stops holding, looks like.
"""

import json
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.db.models import CalendarItem, Cycle, Draft, PerformanceRow
from growthcrew.memory.miner import mine

START = datetime(2026, 6, 29, tzinfo=UTC)
WEEKS, POSTS_PER_WEEK = 12, 8
BASE_CTR = 0.025
TOPICS = (
    "how linen breathes",
    "washing linen",
    "why linen wrinkles",
    "choosing a duvet cover",
    "sleeping cooler in summer",
    "what flax is",
    "our 60-night trial",
    "linen that lasts",
)


def _post(rng: random.Random, topic: str, question: bool, number: bool) -> str:
    count = rng.choice([3, 5, 7])
    if question and number:
        first = f"Do you know the {count} things nobody tells you about {topic}?"
    elif question:
        first = f"Ever wondered about {topic}?"
    elif number:
        first = f"{count} things we have learned about {topic}."
    else:
        first = f"A short note about {topic}."
    return f"{first}\nWe make stonewashed linen bedding, and customers ask us about {topic} every week.\nHere is what we tell them."


def run(
    engine: Engine, workspace: str = "acme", seed: int = 11, mine_weekly: bool = True
) -> list[dict]:
    """Publish and measure twelve weeks of posts, mining after each. Returns one entry per
    week: {week, rules: {feature: status}}."""
    rng = random.Random(seed)
    log = []
    for week in range(1, WEEKS + 1):
        monday = START + timedelta(weeks=week - 1)
        with Session(engine) as session:
            cycle = Cycle(workspace=workspace, week_start=monday, stage="measured")
            session.add(cycle)
            session.flush()
            for number in range(POSTS_PER_WEEK):
                question, has_number = number % 2 == 0, (number // 2) % 2 == 0
                rate = BASE_CTR * (1.4 if question else 1.0) * rng.lognormvariate(0, 0.15)
                if has_number and week <= 4:
                    rate *= 1.5
                impressions = rng.randint(2500, 3500)
                clicks = sum(rng.random() < rate for _ in range(impressions))
                day = monday + timedelta(days=number % 5)
                text = _post(rng, TOPICS[(week + number) % len(TOPICS)], question, has_number)
                draft = Draft(
                    cycle_id=cycle.id,
                    workspace=workspace,
                    piece_id=f"{number + 1:02d}-day{number % 5 + 1:02d}-linkedin_post",
                    content_type="linkedin_post",
                    day=number % 5 + 1,
                    original_text=text,
                    text=text,
                    body_json="{}",
                    min_score=9,
                    passed_critic=True,
                    status="approved",
                    metadata_json=json.dumps(
                        {
                            "messaging_pillar": "Sleeps cooler",
                            "target_persona": "Hot sleepers",
                            "cta": "Shop sheet sets",
                            "hypothesis": "",
                        }
                    ),
                    prompt_version="p-sim00001" if week <= 6 else "p-sim00002",
                    strategy_version=1,
                )
                session.add(draft)
                session.flush()
                session.add(
                    CalendarItem(
                        draft_id=draft.id,
                        cycle_id=cycle.id,
                        workspace=workspace,
                        scheduled_for=day,
                        channel="LinkedIn",
                        status="measured",
                        published_at=day,
                        published_via="manual",
                        published_by="simulation",
                    )
                )
                session.add(
                    PerformanceRow(
                        workspace=workspace,
                        source="linkedin",
                        ref=f"sim-{week}-{number}",
                        date=day,
                        draft_id=draft.id,
                        impressions=impressions,
                        clicks=clicks,
                    )
                )
            session.commit()
        if mine_weekly:
            rules = mine(engine, workspace, as_of=monday + timedelta(days=6))
            log.append({"week": week, "rules": {rule.feature: rule.status for rule in rules}})
    return log
