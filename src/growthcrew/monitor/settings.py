"""What to watch for a workspace: `workspaces/<brand>/monitor.json`, or defaults from the brain.

    {
      "pages": [{"competitor": "Rival", "url": "https://rival.com/pricing", "kind": "pricing"}],
      "forums": ["https://forum.example.com/c/baking"],
      "keywords": ["sourdough", "bakery software"]
    }

Without the file, each competitor in the brain is watched at its homepage, `/pricing` and
`/blog`, and the brand's name is the keyword for social listening.
"""

from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

from pydantic import BaseModel

from growthcrew.brain.models import Brain

PageKind = Literal["home", "pricing", "blog"]


class WatchedPage(BaseModel):
    competitor: str
    url: str
    kind: PageKind


class MonitorSettings(BaseModel):
    pages: list[WatchedPage] = []
    forums: list[str] = []
    keywords: list[str] = []


def load_settings(workspace: str, root: Path, brand: Brain | None = None) -> MonitorSettings:
    path = root / workspace / "monitor.json"
    settings = (
        MonitorSettings.model_validate_json(path.read_text()) if path.exists() else None
    ) or MonitorSettings()
    if not settings.pages and brand is not None:
        for competitor in brand.competitors:
            if not competitor.url:
                continue
            base = competitor.url if competitor.url.endswith("/") else f"{competitor.url}/"
            settings.pages += [
                WatchedPage(competitor=competitor.name, url=competitor.url, kind="home"),
                WatchedPage(
                    competitor=competitor.name, url=urljoin(base, "pricing"), kind="pricing"
                ),
                WatchedPage(competitor=competitor.name, url=urljoin(base, "blog"), kind="blog"),
            ]
    if not settings.keywords and brand is not None and brand.business.name:
        settings.keywords = [brand.business.name]
    return settings
