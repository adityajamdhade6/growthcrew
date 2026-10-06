"""Load test: many workspaces running a weekly cycle at once, through the real job queue.

    uv run python -m evals.loadtest --workspaces 20 --workers 6 [--database-url postgresql://...]

The queue, the database, the model wrapper (budget checks, kill switch, logging, tracing,
the response cache) are real; the agents are simulated: each of the five stages makes
model calls to a fake client with a set latency and token count, and drafting writes pieces.
So it measures the backbone's throughput and correctness under concurrency, not model speed,
and its cost figure is what the simulated token counts would cost at configured prices.
The report is written to evals/results/loadtest.md and labelled as simulated.
"""

import argparse
import json
import random
import threading
import time
from types import SimpleNamespace

from sqlalchemy import Engine, func
from sqlmodel import Session, SQLModel, create_engine, select

from growthcrew import budget, jobs, tracing
from growthcrew.config import AgentRole
from growthcrew.db.models import Cycle, Draft, Job, LLMCall, Task
from growthcrew.db.session import engine_url, postgres_extras
from growthcrew.llm import LLM, cache_scope

STAGES = [
    ("research", AgentRole.RESEARCH, 3),
    ("strategy_check", AgentRole.STRATEGIST, 2),
    ("content_plan", AgentRole.CONTENT, 1),
    ("drafting", AgentRole.CONTENT, 5),
    ("critic", AgentRole.CRITIC, 5),
]


class Reply(SimpleNamespace):
    pass


class FakeModel:
    """Answers like the API after `latency` seconds, with realistic token counts."""

    def __init__(self, latency: float, seed: int) -> None:
        self.latency, self.rng = latency, random.Random(seed)
        messages = SimpleNamespace(parse=self._send, create=self._send)
        self.messages, self.beta = messages, SimpleNamespace(messages=messages)

    def _send(self, **kwargs):
        time.sleep(self.latency * self.rng.uniform(0.5, 1.5))
        usage = SimpleNamespace(
            input_tokens=self.rng.randint(2000, 6000),
            output_tokens=self.rng.randint(300, 1500),
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        parsed = kwargs["output_format"](note="ok") if "output_format" in kwargs else None
        return Reply(
            content=[],
            stop_reason="end_turn",
            model="claude-opus-5-5",
            usage=usage,
            parsed_output=parsed,
        )


def handler(latency: float):
    from pydantic import BaseModel

    class Out(BaseModel):
        note: str

    def simulated_cycle(engine: Engine, job: Job) -> str:
        cycle_id = json.loads(job.payload)["cycle_id"]
        llm = LLM(client=FakeModel(latency, cycle_id), engine=engine)
        with Session(engine) as session:
            cycle = session.get(Cycle, cycle_id)
        with (
            tracing.span(
                engine,
                f"weekly cycle {cycle_id}",
                "cycle",
                cycle.workspace,
                trace_id=cycle.trace_id,
            ),
            cache_scope(f"cycle:{cycle_id}"),
        ):
            for stage, role, calls in STAGES:
                with Session(engine) as session:
                    done = session.exec(
                        select(Task).where(
                            Task.cycle_id == cycle_id, Task.stage == stage, Task.status == "done"
                        )
                    ).first()
                if done:
                    continue  # resumed after a crash: this stage is finished
                with tracing.span(engine, stage, "stage", cycle.workspace):
                    for number in range(calls):
                        llm.call(
                            role,
                            system=f"{stage} system prompt",
                            user=f"{cycle.workspace} {stage} {number}",
                            output_model=Out,
                            workspace=cycle.workspace,
                        )
                with Session(engine) as session:
                    if stage == "drafting":
                        for number in range(5):
                            session.add(
                                Draft(
                                    cycle_id=cycle_id,
                                    workspace=cycle.workspace,
                                    piece_id=f"{number:02d}-sim",
                                    content_type="ad",
                                    original_text="sim",
                                    text="sim",
                                    body_json="{}",
                                    metadata_json="{}",
                                    min_score=9,
                                    passed_critic=True,
                                )
                            )
                    session.add(
                        Task(
                            cycle_id=cycle_id, workspace=cycle.workspace, stage=stage, status="done"
                        )
                    )
                    row = session.get(Cycle, cycle_id)
                    row.stage = stage
                    session.add(row)
                    session.commit()
        return f"cycle {cycle_id} done"

    return simulated_cycle


def run(url: str, workspaces: int, workers: int, latency: float) -> str:
    engine = (
        create_engine(engine_url(url), pool_size=workers + 5, max_overflow=10)
        if url.startswith(("postgres", "postgresql"))
        else create_engine(url, connect_args={"check_same_thread": False})
    )
    SQLModel.metadata.create_all(engine)
    postgres_extras(engine)
    names = [f"load-{n:02d}" for n in range(workspaces)]
    with Session(engine, expire_on_commit=False) as session:
        cycles = []
        for name in names:
            budget.set_weekly_limit(engine, name, 1000.0)
            cycle = Cycle(
                workspace=name, week_start=budget.week_start(), trace_id=tracing.new_trace_id()
            )
            session.add(cycle)
            cycles.append(cycle)
        session.commit()
    for cycle in cycles:
        jobs.enqueue(
            engine, "simulated_cycle", f"load:{cycle.id}", cycle.workspace, {"cycle_id": cycle.id}
        )
    handlers = {"simulated_cycle": handler(latency)}
    started = time.monotonic()
    threads = [
        threading.Thread(
            target=jobs.run_worker, args=(engine, handlers), kwargs={"max_jobs": workspaces}
        )
        for _ in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    ids = [c.id for c in cycles]
    with Session(engine) as session:
        statuses = session.exec(
            select(Job.status, func.count())
            .where(Job.kind == "simulated_cycle")
            .group_by(Job.status)
        ).all()
        calls = session.exec(
            select(func.count(LLMCall.id), func.sum(LLMCall.cost_usd)).where(
                LLMCall.workspace.in_(names)
            )
        ).one()
        drafts = session.exec(select(Draft).where(Draft.cycle_id.in_(ids))).all()
        owners = {c.id: c.workspace for c in cycles}
    crossed = sum(draft.workspace != owners[draft.cycle_id] for draft in drafts)
    counts = dict(statuses)
    lines = [
        "# Load test (simulated agents, real queue and database)",
        "",
        f"{workspaces} workspaces, one weekly cycle each, {workers} workers, "
        f"{engine.dialect.name}, simulated model latency {latency}s per call.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Wall time | {elapsed:.1f}s |",
        f"| Cycles completed | {counts.get('done', 0)} of {workspaces} |",
        f"| Failed or dead jobs | {counts.get('dead', 0) + counts.get('queued', 0)} |",
        f"| Model calls logged | {calls[0]} |",
        f"| Cost of the simulated tokens at configured prices | ${(calls[1] or 0):.2f} |",
        f"| Drafts written under the wrong workspace | {crossed} |",
        "",
        "Simulated: the model is a fake with set latency and token counts. Real model "
        "latency and cost per cycle are not measured here.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspaces", type=int, default=20)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--latency", type=float, default=0.2)
    parser.add_argument("--database-url", default="sqlite:///evals/results/loadtest.db")
    args = parser.parse_args()
    report = run(args.database_url, args.workspaces, args.workers, args.latency)
    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / "loadtest.md").write_text(report + "\n")
    print(report)
