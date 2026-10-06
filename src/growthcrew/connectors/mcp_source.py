"""Metrics from any MCP server: GrowthCrew as an MCP client.

A workspace can point at an MCP server (for example an analytics vendor's) by its URL, name
the tool that returns rows, and map the row fields to GrowthCrew's. The tool's output is
untrusted data: only the mapped fields are read, numbers are parsed as numbers, and nothing
from it reaches a model. Only Streamable HTTP servers on public addresses are accepted from
the API; a local server needs GROWTHCREW_ALLOW_LOCAL_MCP=1 set by whoever runs the server.
"""

import json
import os
from typing import Any

import anyio

from growthcrew.analytics.ingest import ALIASES, _date, _number
from growthcrew.tools.fetch import assert_public


def check_url(url: str) -> None:
    if not url.startswith(("https://", "http://")):
        raise ValueError("An MCP source must be an http(s) URL (Streamable HTTP)")
    if os.getenv("GROWTHCREW_ALLOW_LOCAL_MCP") != "1":
        assert_public(url)


def rows_from_result(result: Any, mapping: dict[str, str]) -> list[dict]:
    """Normalised rows from a tool result: structured content, or JSON in a text block."""
    data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if data is None:
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    data = json.loads(text)
                    break
                except json.JSONDecodeError:
                    continue
    if isinstance(data, dict):
        data = data.get("rows") or data.get("result") or []
    rows = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get(mapping.get("ref", "ref"), "")).strip()
        if not ref:
            continue
        row: dict = {"refs": [ref], "date": _date(str(item.get(mapping.get("date", "date"), "")))}
        for field in ALIASES:
            if field == "date":
                continue
            source = mapping.get(field, field)
            if source in item:
                row[field] = _number(str(item[source]))
        rows.append(row)
    return rows


async def _call(target: Any, tool: str, arguments: dict) -> Any:
    from mcp import Client

    async with Client(target) as client:
        return await client.call_tool(tool, arguments)


def fetch_rows(settings: dict, server: Any = None) -> list[dict]:
    """Call the configured tool and return normalised rows. `server` replaces the URL in tests."""
    target = server
    if target is None:
        target = settings.get("url", "")
        check_url(target)
    tool = settings.get("tool") or "get_metrics"
    result = anyio.run(_call, target, tool, settings.get("arguments") or {})
    return rows_from_result(result, settings.get("fields") or {})
