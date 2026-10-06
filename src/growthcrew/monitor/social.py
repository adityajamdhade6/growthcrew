"""Social listening: what people say about the category and the brand, and when it spikes.

Reddit and most review sites do not allow automated collection, so their posts are read from
exports a person saves in `workspaces/<brand>/social/` (csv, json or txt, with text, url and
date). Forum pages listed in `monitor.json` are fetched through the polite fetcher, which
honours robots.txt. The model pulls out pains, questions and the words people use; each comes
with a quote, and a quote that is not word for word in its source is dropped. Spikes are
counted in code.
"""

import csv
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.brain.voice import squash
from growthcrew.config import AgentRole
from growthcrew.db.models import SeenItem
from growthcrew.llm import LLM
from growthcrew.monitor.signals import Finding, Source
from growthcrew.tools.fetch import Fetcher
from growthcrew.tools.scrape import fetch_page
from growthcrew.tools.untrusted import DATA_RULE, suspicious, wrap

POST_CHARS = 1500
MAX_POSTS = 60
# A spike: this week's mentions at least this many, and this multiple of the trailing average.
SPIKE_MIN = 5
SPIKE_RATIO = 2.5
TRAILING_WEEKS = 4

LISTEN_SYSTEM = f"""You listen to public discussion for a small business's marketing team. \
From the posts given, pull out: pains (problems people describe), questions (what they ask), \
and language (phrases they use for the problem or the category that the team could reuse). \
For each, copy a quote character for character from one post and give that post's url. A \
paraphrase is removed automatically. Leave a list empty if the posts say nothing relevant.

{DATA_RULE}"""


class Post(BaseModel):
    text: str
    url: str
    date: datetime | None = None
    source: str


class Heard(BaseModel):
    kind: Literal["pain", "question", "language"]
    summary: str
    quote: str
    url: str


class Listening(BaseModel):
    items: list[Heard]


def _parse_date(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromtimestamp(float(text), UTC)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:19] if "T" in text else text[:10], fmt).replace(
                tzinfo=UTC
            )
        except ValueError:
            continue
    return None


def load_exports(workspace: str, root: Path) -> list[Post]:
    posts: list[Post] = []
    folder = root / workspace / "social"
    for path in sorted(folder.glob("*")):
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
            lowered = {str(k).lower(): v for k, v in row.items()}
            text = str(lowered.get("text") or lowered.get("body") or lowered.get("selftext")
                       or lowered.get("title") or "").strip()  # fmt: skip
            if not text:
                continue
            posts.append(
                Post(
                    text=text,
                    url=str(lowered.get("url") or lowered.get("permalink") or "")
                    or f"workspace://{workspace}/social/{path.name}#{number}",
                    date=_parse_date(lowered.get("date") or lowered.get("created_utc")),
                    source=path.stem,
                )
            )
    return posts


def mentions_by_week(posts: list[Post], keyword: str) -> Counter:
    """Posts mentioning `keyword`, counted per ISO week start (Monday)."""
    counts: Counter = Counter()
    needle = keyword.lower()
    for post in posts:
        if post.date and needle in post.text.lower():
            monday = (post.date - timedelta(days=post.date.weekday())).date()
            counts[monday] += 1
    return counts


def spikes(posts: list[Post], keywords: list[str], now: datetime) -> list[tuple[str, int, float]]:
    """Keywords mentioned far more this week than over the weeks before."""
    this_week = (now - timedelta(days=now.weekday())).date()
    found = []
    for keyword in keywords:
        counts = mentions_by_week(posts, keyword)
        current = counts.get(this_week, 0)
        before = [
            counts.get(this_week - timedelta(weeks=n), 0) for n in range(1, TRAILING_WEEKS + 1)
        ]
        average = sum(before) / TRAILING_WEEKS
        if current >= SPIKE_MIN and current >= SPIKE_RATIO * max(average, 1.0):
            found.append((keyword, current, average))
    return found


class SocialListener:
    def __init__(self, llm: LLM, engine: Engine, fetcher: Fetcher | None = None) -> None:
        self.llm = llm
        self.engine = engine
        self.fetcher = fetcher or Fetcher()

    def gather(self, workspace: str, root: Path, forums: list[str]) -> list[Post]:
        posts = load_exports(workspace, root)
        for url in forums:
            try:
                page = fetch_page(url, self.fetcher)
            except Exception:  # noqa: BLE001 — robots refusals and dead pages are skipped
                continue
            posts.append(Post(text=page.text, url=url, date=datetime.now(UTC), source="forum"))
        return posts

    def run(
        self, workspace: str, root: Path, forums: list[str], keywords: list[str]
    ) -> list[Finding]:
        now = datetime.now(UTC)
        posts = self.gather(workspace, root, forums)
        findings: list[Finding] = []
        for keyword, current, average in spikes(posts, keywords, now):
            sample = [p for p in posts if p.date and keyword.lower() in p.text.lower()][:5]
            findings.append(
                Finding(
                    monitor="social",
                    category="spike",
                    title=f"Mentions of '{keyword}' jumped this week",
                    summary=f"{current} posts this week against an average of {average:.1f} "
                    f"over the previous {TRAILING_WEEKS} weeks.",
                    suggested_response="Read the posts behind the spike before reacting.",
                    sources=[Source(url=p.url, date=f"{p.date:%Y-%m-%d}") for p in sample],
                    base_importance=0.75,
                )
            )

        with Session(self.engine) as session:
            seen = {
                item.key
                for item in session.exec(
                    select(SeenItem).where(SeenItem.workspace == workspace, SeenItem.kind == "post")
                )
            }
        fresh = [post for post in posts if _key(post) not in seen][:MAX_POSTS]
        if not fresh:
            return findings
        by_url = {post.url: post for post in fresh}
        listing = "\n\n".join(
            wrap(post.text[:POST_CHARS], post.url, f"{post.date:%Y-%m-%d}" if post.date else "")
            for post in fresh
        )
        heard = self.llm.call(
            AgentRole.MONITOR,
            system=LISTEN_SYSTEM,
            user=f"Category keywords: {', '.join(keywords) or '(none given)'}\n\n{listing}",
            output_model=Listening,
            workspace=workspace,
            tag="monitor|social",
        )
        importance = {"pain": 0.6, "question": 0.5, "language": 0.35}
        for item in heard.items:
            post = by_url.get(item.url)
            # The quote must be word for word in the post it cites.
            if (
                post is None
                or not squash(item.quote)
                or squash(item.quote) not in squash(post.text)
            ):
                continue
            phrases = suspicious(post.text)
            findings.append(
                Finding(
                    monitor="social",
                    category=item.kind,
                    title=item.summary,
                    summary=f'"{item.quote}"',
                    sources=[Source(url=post.url, date=f"{(post.date or now):%Y-%m-%d}")],
                    base_importance=importance[item.kind],
                    warning="The post contains text that reads like instructions to an AI "
                    f"({'; '.join(phrases[:3])}). It was treated as data and not followed."
                    if phrases
                    else "",
                )
            )
        with Session(self.engine) as session:
            for post in fresh:
                session.add(SeenItem(workspace=workspace, kind="post", key=_key(post)))
            session.commit()
        return findings


def _key(post: Post) -> str:
    # Forum pages change, so they are keyed by URL and day; exported posts by URL alone.
    return f"{post.url}|{post.date:%Y-%m-%d}" if post.source == "forum" and post.date else post.url
