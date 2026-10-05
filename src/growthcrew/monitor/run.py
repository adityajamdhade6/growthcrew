"""The weekly monitoring job: run every monitor, file the findings, write the digest."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine

from growthcrew.brain.store import load_brain
from growthcrew.llm import LLM
from growthcrew.monitor import signals
from growthcrew.monitor.competitors import CompetitorMonitor
from growthcrew.monitor.seo import SeoAgent
from growthcrew.monitor.settings import load_settings
from growthcrew.monitor.social import SocialListener
from growthcrew.tools.fetch import Fetcher

logger = logging.getLogger(__name__)


class MonitorRun(BaseModel):
    workspace: str
    found: dict[str, int]
    stored: int
    duplicates: int
    failures: list[str]
    digest_path: str


def run_monitors(
    llm: LLM,
    engine: Engine,
    workspace: str,
    root: Path,
    fetcher: Fetcher | None = None,
) -> MonitorRun:
    brand = load_brain(workspace, root=root)
    settings = load_settings(workspace, root, brand)
    findings: list[signals.Finding] = []
    found: dict[str, int] = {}
    failures: list[str] = []

    def attempt(name: str, job) -> None:
        # One monitor failing (a site down, a malformed export) does not stop the others.
        try:
            result = job()
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s monitor failed for %s", name, workspace)
            failures.append(f"{name}: {type(exc).__name__}: {exc}"[:300])
            return
        found[name] = found.get(name, 0) + len(result)
        findings.extend(result)

    competitors = CompetitorMonitor(llm, engine, fetcher)
    for page in settings.pages:
        attempt("competitor", lambda page=page: competitors.check_page(workspace, page))
    attempt("ads", lambda: competitors.check_ads(workspace, root))
    attempt("seo", lambda: SeoAgent(llm, engine).run(workspace, root)[1])
    listener = SocialListener(llm, engine, fetcher)
    attempt("social", lambda: listener.run(workspace, root, settings.forums, settings.keywords))

    stored, duplicates = signals.file_findings(
        engine, workspace, findings, known=signals.known_texts(workspace, root)
    )
    folder = root / workspace / "signals"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC):%Y-%m-%d}.md"
    path.write_text(signals.digest(engine, workspace))
    return MonitorRun(
        workspace=workspace,
        found=found,
        stored=len(stored),
        duplicates=duplicates,
        failures=failures,
        digest_path=str(path),
    )
