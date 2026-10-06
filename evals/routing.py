"""Model-routing experiment: does a cheaper model for drafting keep quality, and at what cost?

    uv run python -m evals.routing --limit 6       (calls the model: costs money)

Each arm writes the same golden requests with a different model per role; the fixed judge
scores every piece, and each arm's cost per piece is read from the logged calls. The report
gives each arm's mean judge score with a 95% bootstrap interval and its cost per piece, and
the difference against the all-default arm. Results go to evals/results/routing.md.
"""

import argparse
import random
import statistics
import sys
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from growthcrew import budget, config
from growthcrew.config import AgentRole

RESULTS = Path(__file__).parent / "results"

# Arm name -> {role: model}. Roles not named keep config.AGENT_MODELS.
ARMS: dict[str, dict[AgentRole, str]] = {
    "default": {},
    "cheaper_drafts": {AgentRole.CONTENT: config.SONNET},
    "cheapest_drafts": {AgentRole.CONTENT: config.HAIKU},
}


def bootstrap_mean(values: list[float], draws: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws) - 1]


def run(limit: int) -> str:
    from evals.golden.brands import BRANDS
    from evals.judge import judge_content
    from evals.suites import golden_items, live_strategy
    from growthcrew.agents.content import ContentAgent
    from growthcrew.content.types import ContentRequest
    from growthcrew.db.models import LLMCall, RoleModel
    from growthcrew.llm import LLM

    RESULTS.mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{RESULTS / 'routing.db'}")
    SQLModel.metadata.create_all(engine)
    for brand in BRANDS.values():
        budget.set_weekly_limit(engine, brand.workspace, 1000.0)
    llm = LLM(engine=engine)
    root = RESULTS / "routing_workspaces"
    _, strategies = live_strategy(llm, root)
    items = golden_items()[:limit]
    rows = {}
    for arm, models in ARMS.items():
        with Session(engine) as session:
            for row in session.exec(select(RoleModel)):
                session.delete(row)
            for role, model in models.items():
                session.add(RoleModel(role=role.value, model=model))
            session.commit()
            before = session.exec(select(func.max(LLMCall.id))).one() or 0
        agent = ContentAgent(llm, root=root)
        scores, pieces = [], 0
        for item in items:
            brand = BRANDS[item["brand"]]
            request = ContentRequest.model_validate(item["request"])
            records, _ = agent.produce(request, brand, strategies[item["brand"]], item["id"])
            for record in records:
                pieces += 1
                scores.append(float(judge_content(llm, brand, request.content_type,
                                                  record.final.text).overall))  # fmt: skip
        with Session(engine) as session:
            cost = (
                session.exec(
                    select(func.sum(LLMCall.cost_usd)).where(
                        LLMCall.id > before,
                        LLMCall.tag.is_not(None),
                        ~LLMCall.tag.startswith("eval"),
                    )
                ).one()
                or 0.0
            )
        low, high = bootstrap_mean(scores)
        rows[arm] = (statistics.mean(scores), low, high, cost / max(pieces, 1), pieces)
    base = rows["default"]
    lines = ["# Model routing experiment", "",
             f"{len(items)} golden requests per arm; judge model fixed.", "",
             "| Arm | Models | Judge score (95% CI) | Cost per piece | vs default |",
             "|---|---|---|---|---|"]  # fmt: skip
    for arm, (mean, low, high, cost, _pieces) in rows.items():
        models = ", ".join(f"{r.value}: {m}" for r, m in ARMS[arm].items()) or "config defaults"
        lines.append(f"| {arm} | {models} | {mean:.2f} ({low:.2f} to {high:.2f}) | ${cost:.3f} | "
                     f"{mean - base[0]:+.2f} score, {cost - base[3]:+.3f} $ |")  # fmt: skip
    lines += ["", "Overlapping intervals mean the arms cannot be told apart at this sample."]
    text = "\n".join(lines)
    (RESULTS / "routing.md").write_text(text + "\n")
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=6)
    print(run(parser.parse_args().limit))
    sys.exit(0)
