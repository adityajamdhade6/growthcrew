"""Shared FastAPI dependencies."""

from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import Engine

from growthcrew import budget, workflow
from growthcrew.agents.analyst import AnalystAgent
from growthcrew.agents.orchestrator import Orchestrator
from growthcrew.db.session import get_engine
from growthcrew.llm import LLM

templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def engine_dep() -> Engine:
    return get_engine()


def llm_dep(engine: Engine = Depends(engine_dep)) -> LLM:
    llm = LLM(engine=engine)
    client = llm.client
    if not (client.api_key or client.auth_token or getattr(client, "credentials", None)):
        raise HTTPException(
            503, "No Anthropic API key is configured on the server. Add ANTHROPIC_API_KEY to .env."
        )
    return llm


def orchestrator_dep(llm: LLM = Depends(llm_dep)) -> Orchestrator:
    return Orchestrator(llm)


def analyst_dep(llm: LLM = Depends(llm_dep)) -> AnalystAgent:
    return AnalystAgent(llm)


def guard(action: Callable):
    """Translate domain errors into HTTP errors."""
    try:
        return action()
    except (LookupError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except budget.BudgetExceeded as exc:
        raise HTTPException(402, str(exc)) from exc
    except workflow.WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
