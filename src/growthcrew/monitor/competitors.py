"""Competitor monitor: weekly snapshots of competitors' pages, diffed, plus their ads.

What changed is found in code: a line diff of the page text, the set of prices on the page,
and the set of links on a blog index. The model only judges whether a non-price change is a
copy tweak or new positioning, and suggests a response. A price change is a price change
whatever the model says.
"""

import csv
import difflib
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.config import AgentRole
from growthcrew.db.models import PageSnapshot, SeenItem
from growthcrew.llm import LLM
from growthcrew.monitor.settings import WatchedPage
from growthcrew.monitor.signals import Finding, Source
from growthcrew.tools.cache import DiskCache
from growthcrew.tools.fetch import Fetcher
from growthcrew.tools.scrape import extract_text
from growthcrew.tools.untrusted import DATA_RULE, suspicious, wrap

# Changes smaller than this many characters (dates, counters) are not worth a signal.
MIN_CHANGE_CHARS = 40
# Changed text shown to the model, per side.
EXCERPT_CHARS = 3000
IMPORTANCE = {
    "price_change": 0.9,
    "positioning": 0.8,
    "new_ads": 0.6,
    "new_post": 0.5,
    "copy_tweak": 0.2,
}
AMOUNT = r"\d+(?:,\d{3})*(?:\.\d+)?"
PRICE = re.compile(rf"(?:[$€£₹]\s?{AMOUNT}|{AMOUNT}\s?(?:USD|EUR|GBP|INR)\b)")

CHANGE_SYSTEM = f"""You review changes to a competitor's web page for a small business's \
marketing team. You are given the lines removed from the page and the lines added since last \
week. Decide whether the change is a copy_tweak (wording, layout, dates, small offers) or \
positioning (a new audience, a new main promise, a new category or a new product line). \
Summarise what changed in one or two plain sentences, and suggest one concrete response for \
the team, or say none is needed. Base everything on the lines shown; do not guess beyond them.

{DATA_RULE}"""

ADS_SYSTEM = f"""You classify competitors' ads for a small business's marketing team. For \
each ad, give its hook (the opening idea that stops the scroll), its angle (outcome, pain, \
social proof, price, novelty, authority, or another single word), and its offer (the concrete \
thing promised, or "none"). Use the ad_id exactly as given. Classify only the ads shown.

{DATA_RULE}"""


class ChangeReview(BaseModel):
    significance: Literal["copy_tweak", "positioning"]
    summary: str
    suggested_response: str


class AdLabel(BaseModel):
    ad_id: str
    hook: str
    angle: str
    offer: str


class AdLabels(BaseModel):
    ads: list[AdLabel]


class Ad(BaseModel):
    id: str
    advertiser: str
    text: str
    url: str
    date: str


def prices(text: str) -> set[str]:
    return {re.sub(r"\s+", "", match) for match in PRICE.findall(text)}


def links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = {urljoin(base, a["href"]).split("#")[0] for a in soup.find_all("a", href=True)}
    return sorted(found)


def post_links(page_url: str, all_links: list[str]) -> set[str]:
    """Links on a blog index that look like posts: same host, below the blog's own path."""
    page = urlparse(page_url)
    prefix = page.path.rstrip("/") + "/"
    return {
        link
        for link in all_links
        if urlparse(link).netloc == page.netloc
        and urlparse(link).path.startswith(prefix)
        and urlparse(link).path.rstrip("/") != page.path.rstrip("/")
    }


def line_diff(old: str, new: str) -> tuple[list[str], list[str]]:
    removed, added = [], []
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("- ") and line[2:].strip():
            removed.append(line[2:])
        elif line.startswith("+ ") and line[2:].strip():
            added.append(line[2:])
    return removed, added


class CompetitorMonitor:
    def __init__(self, llm: LLM, engine: Engine, fetcher: Fetcher | None = None) -> None:
        self.llm = llm
        self.engine = engine
        # Pages are re-read at most twice a day, so a weekly run always sees fresh text.
        self.fetcher = fetcher or Fetcher(cache=DiskCache("monitor", ttl=12 * 3600))

    def _previous(self, session: Session, workspace: str, url: str) -> PageSnapshot | None:
        return session.exec(
            select(PageSnapshot)
            .where(PageSnapshot.workspace == workspace, PageSnapshot.url == url)
            .order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc())
        ).first()

    def check_page(self, workspace: str, page: WatchedPage) -> list[Finding]:
        try:
            html = self.fetcher.get(page.url)
        except Exception:  # noqa: BLE001 — a missing /pricing or a robots refusal is normal
            return []
        text = extract_text(html)
        found_links = links(html, page.url)
        now = datetime.now(UTC)
        with Session(self.engine, expire_on_commit=False) as session:
            previous = self._previous(session, workspace, page.url)
            session.add(
                PageSnapshot(
                    workspace=workspace,
                    competitor=page.competitor,
                    url=page.url,
                    kind=page.kind,
                    fetched_at=now,
                    text=text,
                    links=json.dumps(found_links),
                    digest=hashlib.sha256(text.encode()).hexdigest(),
                )
            )
            session.commit()
        if previous is None:
            return []  # the first snapshot is the baseline
        date = f"{now:%Y-%m-%d}"
        source = [Source(url=page.url, date=date)]
        warning = _warning(text)
        findings: list[Finding] = []

        old_prices, new_prices = prices(previous.text), prices(text)
        if old_prices != new_prices and (old_prices or new_prices):
            gone = ", ".join(sorted(old_prices - new_prices)) or "none"
            added = ", ".join(sorted(new_prices - old_prices)) or "none"
            findings.append(
                Finding(
                    monitor="competitor",
                    category="price_change",
                    title=f"{page.competitor} changed prices on its {page.kind} page",
                    summary=f"Prices no longer shown: {gone}. New prices: {added}.",
                    suggested_response="Check whether our pricing message still holds against "
                    "the new numbers.",
                    sources=source,
                    base_importance=IMPORTANCE["price_change"],
                    warning=warning,
                )
            )

        if page.kind == "blog":
            new_posts = sorted(
                post_links(page.url, found_links)
                - post_links(page.url, json.loads(previous.links or "[]"))
            )
            if new_posts:
                findings.append(
                    Finding(
                        monitor="competitor",
                        category="new_post",
                        title=f"{page.competitor} published "
                        f"{len(new_posts)} new post{'s' if len(new_posts) != 1 else ''}",
                        summary="New posts: " + ", ".join(new_posts[:10]),
                        sources=[Source(url=url, date=date) for url in new_posts[:10]],
                        base_importance=IMPORTANCE["new_post"],
                        warning=warning,
                    )
                )
            return findings

        # Prices are reported above, so they are masked here: a page whose only change is a
        # price does not also go to the model as a copy change.
        removed, added = line_diff(PRICE.sub("<price>", previous.text), PRICE.sub("<price>", text))
        changed = sum(len(line) for line in removed + added)
        if changed < MIN_CHANGE_CHARS:
            return findings
        before = f"{previous.fetched_at:%Y-%m-%d}"
        review = self.llm.call(
            AgentRole.MONITOR,
            system=CHANGE_SYSTEM,
            user=f"Competitor: {page.competitor}\nPage: {page.kind}\n\nRemoved lines:\n"
            f"{wrap(chr(10).join(removed)[:EXCERPT_CHARS], page.url, before)}"
            f"\n\nAdded lines:\n{wrap(chr(10).join(added)[:EXCERPT_CHARS], page.url, date)}",
            output_model=ChangeReview,
            workspace=workspace,
            tag=f"monitor|{page.url}",
        )
        findings.append(
            Finding(
                monitor="competitor",
                category=review.significance,
                title=f"{page.competitor}'s {page.kind} page: "
                + ("new positioning" if review.significance == "positioning" else "copy changed"),
                summary=review.summary,
                suggested_response=review.suggested_response,
                sources=source,
                base_importance=IMPORTANCE[review.significance],
                warning=warning,
            )
        )
        return findings

    def check_ads(self, workspace: str, root: Path) -> list[Finding]:
        """New ads from the exports in `workspaces/<brand>/ads/`, classified by hook and angle."""
        ads = load_ad_exports(workspace, root)
        with Session(self.engine) as session:
            seen = {
                item.key
                for item in session.exec(
                    select(SeenItem).where(SeenItem.workspace == workspace, SeenItem.kind == "ad")
                )
            }
        new = [ad for ad in ads if ad.id not in seen]
        if not new:
            return []
        listing = "\n\n".join(
            wrap(f"ad_id: {ad.id}\nadvertiser: {ad.advertiser}\n{ad.text}", ad.url, ad.date)
            for ad in new[:40]
        )
        labels = self.llm.call(
            AgentRole.MONITOR,
            system=ADS_SYSTEM,
            user=listing,
            output_model=AdLabels,
            workspace=workspace,
            tag="monitor|ads",
        )
        by_id = {ad.id: ad for ad in new}
        # Labels for ads that were not in the list are ignored.
        kept = [label for label in labels.ads if label.ad_id in by_id]
        findings = []
        for advertiser in sorted({ad.advertiser for ad in new}):
            theirs = [label for label in kept if by_id[label.ad_id].advertiser == advertiser]
            count = sum(1 for ad in new if ad.advertiser == advertiser)
            angles = Counter(label.angle.lower() for label in theirs)
            offers = sorted({label.offer for label in theirs if label.offer.lower() != "none"})
            summary = f"{count} new ad{'s' if count != 1 else ''}."
            if angles:
                summary += " Angles: " + ", ".join(f"{a} ({n})" for a, n in angles.most_common())
            if offers:
                summary += ". Offers: " + "; ".join(offers[:5])
            hooks = [label.hook for label in theirs][:3]
            if hooks:
                summary += ". Hooks: " + " | ".join(hooks)
            findings.append(
                Finding(
                    monitor="competitor",
                    category="new_ads",
                    title=f"{advertiser} is running new ads",
                    summary=summary,
                    sources=[
                        Source(url=ad.url, date=ad.date)
                        for ad in new
                        if ad.advertiser == advertiser
                    ][:10],
                    base_importance=IMPORTANCE["new_ads"],
                    warning=" ".join(
                        _warning(ad.text) for ad in new if ad.advertiser == advertiser
                    ).strip(),
                )
            )
        with Session(self.engine) as session:
            for ad in new:
                session.add(SeenItem(workspace=workspace, kind="ad", key=ad.id))
            session.commit()
        return findings


def _warning(text: str) -> str:
    phrases = suspicious(text)
    if not phrases:
        return ""
    return (
        "The source contains text that reads like instructions to an AI "
        f"({'; '.join(phrases[:3])}). It was treated as data and not followed."
    )


def _pick(row: dict, *keys: str) -> str:
    lowered = {str(k).lower().strip(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def load_ad_exports(workspace: str, root: Path) -> list[Ad]:
    """Ads from Meta Ad Library (or similar) exports a person saved under `ads/`."""
    folder = root / workspace / "ads"
    ads: list[Ad] = []
    for path in sorted(folder.glob("*")):
        if path.suffix == ".csv":
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        elif path.suffix == ".json":
            rows = json.loads(path.read_text())
        else:
            continue
        for number, row in enumerate(rows, 1):
            text = _pick(row, "ad_creative_bodies", "ad_creative_body", "body", "text", "ad_text")
            if not text:
                continue
            ad_id = _pick(row, "ad_archive_id", "id", "ad_id") or f"{path.name}#{number}"
            ads.append(
                Ad(
                    id=ad_id,
                    advertiser=_pick(row, "page_name", "advertiser", "competitor") or "Unknown",
                    text=text,
                    url=_pick(row, "ad_snapshot_url", "url", "link")
                    or f"workspace://{workspace}/ads/{path.name}#{number}",
                    date=_pick(row, "ad_delivery_start_time", "start_date", "date")
                    or f"{datetime.fromtimestamp(path.stat().st_mtime, UTC):%Y-%m-%d}",
                )
            )
    return ads
