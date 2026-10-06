"""Workspace isolation at the query layer.

Inside `tenant(workspace)` (every API request that names a workspace or a row runs in one),
every ORM query on a table with a `workspace` column is filtered to that workspace, and
saving a row for another workspace raises. A route that forgets its `where workspace ==`
still cannot read or write another client's data.
"""

import contextvars
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import with_loader_criteria
from sqlmodel import SQLModel

_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant", default=None)


class CrossTenantWrite(PermissionError):
    pass


def current() -> str | None:
    return _tenant.get()


def set_tenant(workspace: str | None) -> contextvars.Token:
    return _tenant.set(workspace)


@contextmanager
def tenant(workspace: str | None):
    token = _tenant.set(workspace)
    try:
        yield
    finally:
        _tenant.reset(token)


def _scoped_models() -> list[type]:
    return [
        mapper.class_
        for mapper in SQLModel._sa_registry.mappers
        if "workspace" in mapper.columns and mapper.class_.__name__ != "User"
    ]


@event.listens_for(OrmSession, "do_orm_execute")
def _filter(state) -> None:
    workspace = _tenant.get()
    if workspace is None or not (state.is_select or state.is_update or state.is_delete):
        return
    for model in _scoped_models():
        allowed = model.workspace.in_([workspace, "*", ""])
        state.statement = state.statement.options(
            with_loader_criteria(model, allowed, include_aliases=True)
        )


@event.listens_for(OrmSession, "before_flush")
def _guard_writes(session, flush_context, instances) -> None:
    workspace = _tenant.get()
    if workspace is None:
        return
    for row in list(session.new) + list(session.dirty):
        owner = getattr(row, "workspace", None)
        if owner not in (None, "", "*", workspace):
            raise CrossTenantWrite(
                f"Refused to write a {type(row).__name__} for '{owner}' while serving '{workspace}'"
            )
