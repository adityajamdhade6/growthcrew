"""Polite HTTP fetching: robots.txt, per-host rate limit, and a disk cache."""

import ipaddress
import socket
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from growthcrew.tools.cache import DiskCache

USER_AGENT = "GrowthCrewBot/0.1 (marketing research assistant)"
MAX_DELAY = 5.0


class RobotsDisallowed(Exception):
    pass


class BlockedAddress(Exception):
    pass


def assert_public(url: str) -> None:
    """Refuse URLs that point inside the network this server runs on.

    The research agent and the onboarding crawl fetch URLs chosen by a model or typed by a
    user, so without this they could be pointed at localhost or a cloud metadata address.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host:
        raise BlockedAddress(f"Only http and https URLs can be fetched: {url}")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return  # does not resolve; the request itself will fail
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise BlockedAddress(
                f"{host} resolves to a private or local address and is not fetched"
            )


def _guard_request(request: httpx.Request) -> None:
    assert_public(str(request.url))


def polite_client() -> httpx.Client:
    """An HTTP client that identifies itself and checks every request, redirects included."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=20.0,
        follow_redirects=True,
        event_hooks={"request": [_guard_request]},
    )


def load_robots(client: httpx.Client, url: str) -> RobotFileParser:
    robots = RobotFileParser()
    try:
        response = client.get(urljoin(url, "/robots.txt"))
    except httpx.HTTPError:
        robots.parse([])
        return robots
    if response.status_code in (401, 403):
        robots.parse(["User-agent: *", "Disallow: /"])
    elif response.status_code >= 400:
        robots.parse([])
    else:
        robots.parse(response.text.splitlines())
    return robots


class Fetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: DiskCache | None = None,
        min_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client or polite_client()
        self.cache = cache or DiskCache("http")
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    def allowed(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._robots:
            self._robots[host] = load_robots(self.client, url)
        return self._robots[host].can_fetch(USER_AGENT, url)

    def _wait_for_turn(self, url: str) -> None:
        host = urlparse(url).netloc
        crawl_delay = self._robots[host].crawl_delay(USER_AGENT) if host in self._robots else None
        interval = min(max(self.min_interval, float(crawl_delay or 0)), MAX_DELAY)
        last = self._last_request.get(host)
        if last is not None and (remaining := interval - (self._clock() - last)) > 0:
            self._sleep(remaining)
        self._last_request[host] = self._clock()

    def get(self, url: str) -> str:
        """Return the response body for `url`, from cache when fresh."""
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        assert_public(url)
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt does not allow fetching {url}")
        self._wait_for_turn(url)
        response = self.client.get(url)
        response.raise_for_status()
        self.cache.set(url, response.text)
        return response.text
