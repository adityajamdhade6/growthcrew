import httpx
from pydantic import BaseModel

from growthcrew import config
from growthcrew.tools.cache import DiskCache

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
DAY = 24 * 3600


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class SearchNotConfigured(RuntimeError):
    pass


def web_search(
    query: str,
    limit: int = 5,
    client: httpx.Client | None = None,
    cache: DiskCache | None = None,
) -> list[SearchResult]:
    """Search the web with the Brave Search API. Swap providers by editing this function."""
    if not config.SEARCH_API_KEY:
        raise SearchNotConfigured("SEARCH_API_KEY is not set, so web search is unavailable.")
    cache = cache or DiskCache("search", ttl=DAY)
    key = f"{query}|{limit}"
    results = cache.get(key)
    if results is None:
        response = (client or httpx).get(
            BRAVE_URL,
            params={"q": query, "count": limit},
            headers={"X-Subscription-Token": config.SEARCH_API_KEY, "Accept": "application/json"},
            timeout=20.0,
        )
        response.raise_for_status()
        results = [
            {"title": r.get("title", ""), "url": r["url"], "snippet": r.get("description", "")}
            for r in response.json().get("web", {}).get("results", [])
        ]
        cache.set(key, results)
    return [SearchResult(**result) for result in results]
