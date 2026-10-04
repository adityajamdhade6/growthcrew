"""Names people read, in one place: content types, pieces, plurals."""

import re

CONTENT_NAMES = {
    "linkedin_post": "LinkedIn post",
    "x_thread": "X thread",
    "blog_article": "blog article",
    "cold_email_sequence": "cold email sequence",
    "ad": "ad",
    "landing_hero": "landing page hero",
    "newsletter": "newsletter",
}
_OTHER = {
    "pending_approval": "needs approval",
    "accepted_partial": "accepted in part",
    "ai_cliche": "no clichés",
    "strategy_check": "strategy check",
    "social_proof": "social proof",
    "gsc": "Search Console",
    "ga4": "GA4",
}
_PIECE = re.compile(r"^\d+-day(\d+)-(.+?)(?:-(pain|outcome|social_proof))?$")


def display(value: str, capital: bool = True) -> str:
    """A code-style value such as `linkedin_post` as words: "LinkedIn post"."""
    text = CONTENT_NAMES.get(value) or _OTHER.get(value)
    if text is None:
        text = value
        # Longest names first, so "linkedin_post by angle" keeps its proper name.
        for key in sorted(CONTENT_NAMES, key=len, reverse=True):
            text = text.replace(key, CONTENT_NAMES[key])
        text = text.replace("_", " ")
    return text[0].upper() + text[1:] if capital and text else text


def piece_name(piece_id: str) -> str:
    """`03-day03-newsletter` -> "Day 3 newsletter"; variants add "(pain angle)"."""
    match = _PIECE.match(piece_id)
    if not match:
        return display(piece_id)
    day, content_type, angle = match.groups()
    name = f"Day {int(day)} {display(content_type, capital=False)}"
    return f"{name} ({display(angle, capital=False)} angle)" if angle else name


def plural(count: int, word: str, many: str | None = None) -> str:
    """`plural(1, "model call")` -> "1 model call"; `plural(3, "model call")` -> "3 model calls"."""
    return f"{count:,} {word if count == 1 else many or word + 's'}"
