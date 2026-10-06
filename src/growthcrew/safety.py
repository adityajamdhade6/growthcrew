"""The kill switch, and the log scrubber that keeps secrets out of logs."""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.budget import BudgetExceeded
from growthcrew.db.models import SystemFlag


class AgentsPaused(BudgetExceeded):
    """Raised before any model call while the kill switch is on. A halted cycle resumes later."""


def _key(workspace: str | None) -> str:
    return f"paused:{workspace or '*'}"


def set_paused(engine: Engine, paused: bool, person: str, workspace: str | None = None) -> None:
    with Session(engine) as session:
        session.merge(SystemFlag(key=_key(workspace), value="1" if paused else "0",
                                 set_by=person, set_at=datetime.now(UTC)))  # fmt: skip
        session.commit()


def paused(engine: Engine, workspace: str | None = None) -> str | None:
    """Who paused agents (everywhere, or in this workspace), or None if they may run."""
    with Session(engine) as session:
        for key in (_key(None), _key(workspace) if workspace else None):
            flag = session.get(SystemFlag, key) if key else None
            if flag and flag.value == "1":
                return f"{flag.set_by} ({'all workspaces' if key == _key(None) else workspace})"
    return None


def check(engine: Engine, workspace: str | None) -> None:
    if who := paused(engine, workspace):
        raise AgentsPaused(f"All agents are paused by {who}. Resume them in Settings.")


SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "sk-[REDACTED]"),
    (re.compile(r"xkeysib-[A-Za-z0-9\-]{8,}"), "xkeysib-[REDACTED]"),
    (re.compile(r"\bpat-[a-z0-9]{2,}-[A-Za-z0-9\-]{8,}"), "pat-[REDACTED]"),
    (re.compile(r"ya29\.[A-Za-z0-9_\-.]{8,}"), "ya29.[REDACTED]"),
    (re.compile(r"1//[A-Za-z0-9_\-]{8,}"), "1//[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-.=]{12,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|password|secret|token|authorization)[\"']?\s*[:=]\s*[\"']?)"
            r"[^\s\"',}]{6,}"
        ),
        r"\1[REDACTED]",
    ),  # fmt: skip
]


def scrub(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class ScrubFilter(logging.Filter):
    """Redacts keys and tokens from every log record, arguments and tracebacks included."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


def install_scrubber() -> None:
    """Attach the scrubber to every handler on the root logger (idempotent)."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    for handler in root.handlers:
        if not any(isinstance(f, ScrubFilter) for f in handler.filters):
            handler.addFilter(ScrubFilter())
