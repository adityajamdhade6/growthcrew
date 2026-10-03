"""A small, polite same-site crawler."""

import logging
import time
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from growthcrew.tools.fetch import (
    MAX_DELAY,
    USER_AGENT,
    assert_public,
    load_robots,
    polite_client,
)
from growthcrew.tools.scrape import Page, extract_text

logger = logging.getLogger(__name__)

MAX_PAGES = 20

# Pages that tell us most about the business get fetched first.
PRIORITY = (
    "about", "pricing", "price", "plans", "product", "service", "solution", "customer",
    "case", "testimonial", "review", "story", "industr", "faq", "why", "feature", "collection",
)  # fmt: skip
SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".css", ".js",
    ".xml", ".json", ".ico",
)  # fmt: skip
SKIP_WORDS = (
    "login", "signin", "signup", "cart", "checkout", "account", "privacy", "terms", "cookie",
    "career", "jobs",
)  # fmt: skip


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _score(url: str) -> int:
    path = urlparse(url).path.lower()
    hit = any(word in path for word in PRIORITY)
    # Lower sorts first: priority pages, then shallow ones.
    return (0 if hit else 10) + path.strip("/").count("/")


def crawl(
    start_url: str,
    max_pages: int = MAX_PAGES,
    client: httpx.Client | None = None,
    delay: float = 0.5,
) -> list[Page]:
    """Fetch up to `max_pages` pages from the start URL's site, obeying robots.txt."""
    client = client or polite_client()
    assert_public(start_url)
    robots = load_robots(client, start_url)
    delay = min(float(robots.crawl_delay(USER_AGENT) or delay), MAX_DELAY)
    site = _host(start_url)

    queue, seen, pages, texts = [start_url], {start_url.rstrip("/")}, [], set()
    while queue and len(pages) < max_pages:
        queue.sort(key=_score)
        # The start page always goes first.
        url = queue.pop(0) if pages else queue.pop(queue.index(start_url))
        if not robots.can_fetch(USER_AGENT, url):
            logger.info("robots.txt disallows %s", url)
            continue
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            logger.info("Failed to fetch %s: %s", url, exc)
            continue
        is_html = "html" in response.headers.get("content-type", "")
        if response.status_code != 200 or not is_html or _host(str(response.url)) != site:
            continue

        text = extract_text(response.text)
        if text and text not in texts:
            texts.add(text)
            pages.append(Page(url=str(response.url), text=text))

        for anchor in BeautifulSoup(response.text, "html.parser").find_all("a", href=True):
            link = urldefrag(urljoin(str(response.url), anchor["href"])).url
            parsed = urlparse(link)
            key = link.split("?")[0].rstrip("/")
            if (
                parsed.scheme in ("http", "https")
                and _host(link) == site
                and key not in seen
                and not parsed.path.lower().endswith(SKIP_EXTENSIONS)
                and not any(word in parsed.path.lower() for word in SKIP_WORDS)
            ):
                seen.add(key)
                queue.append(link.split("?")[0])
        if queue and delay:
            time.sleep(delay)
    return pages
