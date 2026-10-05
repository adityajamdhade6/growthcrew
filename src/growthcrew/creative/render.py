"""Render HTML to PNG with headless Chromium, and measure the text on the page.

The page is loaded with every network request refused: templates may only use inline styles
and data: URIs, so rendering can never be pointed at an internal address or a tracker.
"""

import glob
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from growthcrew import config

# Width, height, and the space the platform's own buttons cover (top, bottom).
SIZES: dict[str, tuple[int, int, int, int]] = {
    "square": (1080, 1080, 80, 80),
    "portrait": (1080, 1350, 90, 90),
    # Stories: profile bar at the top, reply box and buttons at the bottom.
    "story": (1080, 1920, 250, 340),
}
RATIOS = {"square": "1:1", "portrait": "4:5", "story": "9:16"}

MEASURE = """() => {
  const canvas = document.getElementById('canvas');
  const box = canvas.getBoundingClientRect();
  const safeTop = parseFloat(getComputedStyle(canvas).paddingTop);
  const safeBottom = box.height - parseFloat(getComputedStyle(canvas).paddingBottom);
  const slots = {};
  let textArea = 0, words = 0;
  for (const el of document.querySelectorAll('[data-slot]')) {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    const px = parseFloat(style.fontSize);
    // Glyphs overhang their line box by a few pixels; half a line hidden is real clipping.
    slots[el.dataset.slot] = {
      font_px: px,
      clipped: el.scrollHeight - el.clientHeight > px / 2
               || el.scrollWidth - el.clientWidth > px / 2
               || r.bottom > box.height || r.right > box.width,
      outside_safe_area: r.top < safeTop - 1 || r.bottom > safeBottom + 1,
    };
    textArea += r.width * r.height;
    words += (el.innerText.trim().match(/\\S+/g) || []).length;
  }
  return {slots, text_share: textArea / (box.width * box.height), words};
}"""


class Renderer(Protocol):
    def render(self, html: str, width: int, height: int) -> tuple[bytes, dict]: ...


def chromium_path() -> str | None:
    """A Chromium binary: the configured one, or one under PLAYWRIGHT_BROWSERS_PATH."""
    if config.CHROMIUM_PATH:
        return config.CHROMIUM_PATH
    root = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "")
    found = (
        sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome")))
        if root
        else []
    )
    return found[-1] if found else None


class PlaywrightRenderer:
    """One browser for many renders. Use as a context manager."""

    def __enter__(self) -> "PlaywrightRenderer":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        path = chromium_path()
        try:
            self._browser = self._playwright.chromium.launch(executable_path=path)
        except Exception:
            self._playwright.stop()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._browser.close()
        self._playwright.stop()

    def render(self, html: str, width: int, height: int) -> tuple[bytes, dict]:
        page = self._browser.new_page(viewport={"width": width, "height": height})
        try:
            # Nothing leaves the machine: only data: URIs and the page itself load.
            page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="load")
            measure = page.evaluate(MEASURE) if 'id="canvas"' in html else {}
            png = page.screenshot(clip={"x": 0, "y": 0, "width": width, "height": height})
        finally:
            page.close()
        return png, measure


@contextmanager
def renderer() -> Iterator[Renderer]:
    with PlaywrightRenderer() as instance:
        yield instance
