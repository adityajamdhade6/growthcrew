"""Tracing: each weekly cycle is one trace, with a span per stage, model call and tool call.

Spans are stored in the `Span` table and can be exported as OpenTelemetry (OTLP/JSON), so a
trace opens in Jaeger, Tempo, Langfuse or any OTLP-compatible tool. No collector is needed to
use it: the API serves a cycle's trace and `growthcrew trace <cycle>` prints it.
"""

import contextvars
import json
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.db.models import Span


@dataclass(frozen=True)
class Context:
    trace_id: str
    span_id: str


_current: contextvars.ContextVar[Context | None] = contextvars.ContextVar("span", default=None)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def current() -> Context | None:
    return _current.get()


@contextmanager
def span(
    engine: Engine,
    name: str,
    kind: str,
    workspace: str = "",
    trace_id: str | None = None,
    **attributes,
) -> Iterator[dict]:
    """Time a block as a span under the current one. Yields a dict for extra attributes."""
    parent = _current.get()
    trace = trace_id or (parent.trace_id if parent else new_trace_id())
    context = Context(trace, secrets.token_hex(8))
    token = _current.set(context)
    started, wall = time.monotonic(), datetime.now(UTC)
    extra: dict = dict(attributes)
    status = "ok"
    try:
        yield extra
    except BaseException as exc:
        status = "error"
        extra["error"] = f"{type(exc).__name__}: {exc}"[:300]
        raise
    finally:
        _current.reset(token)
        record(engine, Span(trace_id=trace, span_id=context.span_id,
                            parent_id=parent.span_id if parent and parent.trace_id == trace else "",
                            name=name, kind=kind, workspace=workspace, started_at=wall,
                            ended_at=datetime.now(UTC),
                            duration_ms=int((time.monotonic() - started) * 1000), status=status,
                            attributes=json.dumps(extra, default=str)))  # fmt: skip


def record(engine: Engine, row: Span) -> None:
    with Session(engine) as session:
        session.add(row)
        session.commit()


def spans(engine: Engine, trace_id: str) -> list[Span]:
    with Session(engine) as session:
        return list(
            session.exec(select(Span).where(Span.trace_id == trace_id).order_by(Span.started_at))
        )


def tree(engine: Engine, trace_id: str) -> list[dict]:
    """The trace as nested spans, for the API and the Evals page."""
    rows = spans(engine, trace_id)
    nodes = {
        row.span_id: {
            "name": row.name, "kind": row.kind, "status": row.status,
            "duration_ms": row.duration_ms, "started_at": row.started_at.isoformat(),
            "attributes": json.loads(row.attributes), "children": [],
        }
        for row in rows
    }  # fmt: skip
    roots = []
    for row in rows:
        node = nodes[row.span_id]
        if row.parent_id in nodes:
            nodes[row.parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def render(nodes: list[dict], depth: int = 0) -> str:
    lines = []
    for node in nodes:
        attrs = node["attributes"]
        extra = ", ".join(
            f"{key} {attrs[key]}" for key in ("model", "input_tokens", "output_tokens", "cost_usd")
            if key in attrs
        )  # fmt: skip
        flag = "" if node["status"] == "ok" else " [error]"
        lines.append(f"{'  ' * depth}{node['kind']}: {node['name']} {node['duration_ms']}ms"
                     f"{f' ({extra})' if extra else ''}{flag}")  # fmt: skip
        lines.append(render(node["children"], depth + 1))
    return "\n".join(line for line in lines if line)


def otlp(engine: Engine, trace_id: str, service: str = "growthcrew") -> dict:
    """The trace in OTLP/JSON, the format OpenTelemetry collectors accept."""

    def nanos(moment: datetime | None) -> str:
        moment = moment or datetime.now(UTC)
        moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
        return str(int(moment.timestamp() * 1_000_000_000))

    def value(item):
        if isinstance(item, bool):
            return {"boolValue": item}
        if isinstance(item, int):
            return {"intValue": str(item)}
        if isinstance(item, float):
            return {"doubleValue": item}
        return {"stringValue": str(item)}

    out = []
    for row in spans(engine, trace_id):
        attributes = {"growthcrew.kind": row.kind, "growthcrew.workspace": row.workspace,
                      **json.loads(row.attributes)}  # fmt: skip
        out.append({
            "traceId": row.trace_id, "spanId": row.span_id, "parentSpanId": row.parent_id,
            "name": row.name, "kind": 1, "startTimeUnixNano": nanos(row.started_at),
            "endTimeUnixNano": nanos(row.ended_at),
            "attributes": [{"key": k, "value": value(v)} for k, v in attributes.items()],
            "status": {"code": 1 if row.status == "ok" else 2},
        })  # fmt: skip
    return {
        "resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name",
                                         "value": {"stringValue": service}}]},
            "scopeSpans": [{"scope": {"name": "growthcrew.tracing"}, "spans": out}],
        }]
    }  # fmt: skip
