"""Public reviews.

Only the Apple App Store is fetched live, through Apple's public customer-review feed and
subject to robots.txt like every other fetch. G2, Google, Amazon, Google Play and Reddit do
not permit automated collection, so for those a human exports or pastes the reviews into
workspaces/<brand>/reviews/<source>*.csv|json|txt and they are read from there.
"""

import csv
import json
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import quote_plus

from pydantic import BaseModel

from growthcrew.tools.fetch import Fetcher

Source = Literal["app_store", "g2", "google", "amazon", "play_store", "reddit"]
SOURCES: tuple[str, ...] = get_args(Source)
WORKSPACES_DIR = Path("workspaces")

TEXT_KEYS = ("text", "review", "body", "content", "comment")
RATING_KEYS = ("rating", "stars", "score")


class Review(BaseModel):
    source: str
    product: str
    text: str
    title: str = ""
    rating: float | None = None
    url: str


class ReviewsUnavailable(RuntimeError):
    pass


def _first(row: dict, keys: tuple[str, ...]) -> str:
    lowered = {str(k).lower().strip(): v for k, v in row.items()}
    return next((str(lowered[k]) for k in keys if lowered.get(k) not in (None, "")), "")


def _rating(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def load_exports(source: str, product: str, workspace: str, root: Path) -> list[Review]:
    folder = root / workspace / "reviews"
    reviews: list[Review] = []
    for path in sorted(folder.glob(f"{source}*")):
        if path.suffix == ".csv":
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        elif path.suffix == ".json":
            rows = json.loads(path.read_text())
        elif path.suffix == ".txt":
            rows = [{"text": chunk} for chunk in path.read_text().split("\n\n")]
        else:
            continue
        for number, row in enumerate(rows, 1):
            text = _first(row, TEXT_KEYS).strip()
            if text:
                reviews.append(
                    Review(
                        source=source,
                        product=product,
                        text=text,
                        title=_first(row, ("title",)),
                        rating=_rating(_first(row, RATING_KEYS)),
                        url=_first(row, ("url", "link"))
                        or f"workspace://{workspace}/reviews/{path.name}#{number}",
                    )
                )
    return reviews


def app_store_reviews(product: str, fetcher: Fetcher, country: str = "us") -> list[Review]:
    lookup = json.loads(
        fetcher.get(
            f"https://itunes.apple.com/search?term={quote_plus(product)}"
            f"&entity=software&limit=1&country={country}"
        )
    )
    if not lookup.get("results"):
        return []
    app = lookup["results"][0]
    feed = json.loads(
        fetcher.get(
            f"https://itunes.apple.com/{country}/rss/customerreviews/page=1/"
            f"id={app['trackId']}/sortby=mostrecent/json"
        )
    )
    entries = feed.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    return [
        Review(
            source="app_store",
            product=app.get("trackName", product),
            text=entry["content"]["label"],
            title=entry.get("title", {}).get("label", ""),
            rating=_rating(entry.get("im:rating", {}).get("label", "")),
            url=app["trackViewUrl"].split("?")[0],
        )
        for entry in entries
        if "content" in entry
    ]


def get_reviews(
    source: str,
    product: str,
    workspace: str | None = None,
    fetcher: Fetcher | None = None,
    root: Path = WORKSPACES_DIR,
) -> list[Review]:
    if source not in SOURCES:
        raise ValueError(f"Unknown review source '{source}'. Use one of: {', '.join(SOURCES)}")
    reviews = load_exports(source, product, workspace, root) if workspace else []
    if not reviews and source == "app_store":
        reviews = app_store_reviews(product, fetcher or Fetcher())
    if not reviews and source != "app_store":
        raise ReviewsUnavailable(
            f"{source} does not allow automated collection and no export was found. Ask the "
            f"user to add one at workspaces/{workspace}/reviews/{source}.csv (or .json/.txt)."
        )
    return reviews
