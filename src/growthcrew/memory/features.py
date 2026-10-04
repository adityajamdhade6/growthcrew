"""Features of a piece's text that the pattern miner compares. Each is a yes/no question."""

import re
from collections.abc import Callable
from dataclasses import dataclass

_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


def hook(text: str) -> str:
    """The first line: what shows before "see more"."""
    for line in text.splitlines():
        line = re.sub(r"^(Hook|Subject|Headline|\d+/)\s*:?\s*", "", line.strip())
        if line:
            return line
    return ""


@dataclass(frozen=True)
class Feature:
    key: str
    # How the rule reads, with and without the feature.
    with_it: str
    without_it: str
    test: Callable[[str], bool]


FEATURES: tuple[Feature, ...] = (
    Feature(
        "question_hook",
        "a question as the hook",
        "a statement as the hook",
        lambda text: hook(text).rstrip().endswith("?"),
    ),
    Feature(
        "number_in_hook",
        "a number in the hook",
        "no number in the hook",
        lambda text: bool(re.search(r"\d", hook(text))),
    ),
    Feature(
        "second_person_hook",
        "a hook that addresses the reader as 'you'",
        "a hook that does not",
        lambda text: bool(re.search(r"\b(you|your|you're)\b", hook(text), re.IGNORECASE)),
    ),
    Feature("short", "under 60 words", "60 words or more", lambda text: len(text.split()) < 60),
    Feature(
        "has_quote",
        "a quoted customer line",
        "no customer quote",
        lambda text: bool(re.search(r"[\"“][^\"”]{20,}[\"”]", text)),
    ),
    Feature("has_emoji", "emoji", "no emoji", lambda text: bool(_EMOJI.search(text))),
)
BY_KEY = {feature.key: feature for feature in FEATURES}


def extract(text: str) -> dict[str, bool]:
    return {feature.key: bool(feature.test(text)) for feature in FEATURES}
