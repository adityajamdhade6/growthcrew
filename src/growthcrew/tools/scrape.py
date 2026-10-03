import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel

from growthcrew.tools.fetch import Fetcher


class Page(BaseModel):
    url: str
    text: str


def extract_text(html: str) -> str:
    text = trafilatura.extract(html, include_comments=False, favor_recall=True) or ""
    if len(text) < 200:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        fallback = soup.get_text("\n", strip=True)
        text = max(text, fallback, key=len)
    return text


def fetch_page(url: str, fetcher: Fetcher | None = None) -> Page:
    """Fetch a page (robots-checked, rate-limited, cached) and return its main text."""
    return Page(url=url, text=extract_text((fetcher or Fetcher()).get(url)))
