"""Deterministic checks on a draft. The critic's scores can only be lowered by these."""

import re

from growthcrew.agents.strategy_models import StrategyDoc
from growthcrew.brain.models import Brain
from growthcrew.brain.voice import squash

AI_CLICHES: tuple[str, ...] = (
    "in today's fast-paced world", "in today's digital age", "ever-evolving", "ever-changing",
    "unlock", "unleash", "game-changer", "game changer", "game-changing", "revolutionize",
    "revolutionary", "seamless", "seamlessly", "leverage", "elevate", "supercharge", "delve",
    "dive in", "deep dive", "look no further", "to the next level", "cutting-edge",
    "state-of-the-art", "harness the power", "navigate the landscape", "in the realm of",
    "the world of", "say goodbye to", "empower", "robust", "transform your", "in conclusion",
    "it's not just", "more than just", "whether you're", "stand out from the crowd",
    "at the end of the day", "best-in-class", "synergy", "circle back", "touch base",
)  # fmt: skip

_STAT = re.compile(
    r"[$€£₹]\s?\d[\d,.]*\s?(?:k|m|bn|million|billion)?\b"
    r"|\d[\d,.]*\s?(?:%|percent\b|x\b|k\b|million\b|billion\b)"
    # Ratings ("4.9 stars", "4.9/5", "4.9 out of 5").
    r"|\b\d(?:\.\d)?\s?(?:stars?\b|/\s?5\b|out of 5\b)"
    # Head-counts of people or reviews ("12,000 happy sleepers", "500+ clients").
    r"|\b\d{1,3}(?:,\d{3})+\+?(?=\s+(?:\w+\s+){0,2}(?:customers|clients|users|people|buyers|"
    r"sleepers|members|subscribers|businesses|companies|reviews|orders|families|homes)\b)"
    r"|\b\d{3,}\+(?=\s+(?:\w+\s+){0,2}(?:customers|clients|users|people|buyers|members|"
    r"subscribers|businesses|companies|reviews|orders|families|homes)\b)",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[\"“]([^\"”]{25,})[\"”]")


def _find(phrase: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase.lower())}(?!\w)", text.lower()) is not None


def banned_phrases(brand: Brain) -> list[str]:
    guide = brand.voice.guide
    return list(dict.fromkeys([*AI_CLICHES, *guide.banned_phrases, *brand.voice.dont_words]))


def cliche_hits(lines: list[str], brand: Brain) -> list[tuple[int, str]]:
    """(line number, phrase) for every banned phrase found."""
    return [
        (number, phrase)
        for number, line in enumerate(lines, 1)
        for phrase in banned_phrases(brand)
        if phrase and _find(phrase, line)
    ]


def allowed_facts(brand: Brain, strategy: StrategyDoc) -> str:
    """Everything a piece may quote numbers or testimonials from."""
    proof = brand.proof
    items = [*proof.case_studies, *proof.testimonials, *proof.stats]
    parts = [f"{item.summary} {item.quote} {item.attribution}" for item in items]
    parts += [f"{p.name} {p.description} {p.price}" for p in brand.products]
    parts += [brand.business.pricing, brand.business.what_they_sell, brand.business.one_liner]
    parts += [item.text for item in strategy.evidence]
    return " ".join(parts)


def unverified_facts(lines: list[str], facts: str) -> list[tuple[int, str]]:
    """(line number, text) for stats and quoted testimonials that are not in the brain."""
    compact = re.sub(r"\s+", "", facts.lower())
    squashed = squash(facts)
    found = []
    for number, line in enumerate(lines, 1):
        for match in _STAT.finditer(line):
            # The pattern can swallow sentence punctuation after a number ("$189.").
            stat = match.group().strip().rstrip(".,")
            if re.sub(r"\s+", "", stat.lower()) not in compact:
                found.append((number, stat))
        for match in _QUOTED.finditer(line):
            if squash(match.group(1)) not in squashed:
                found.append((number, f'"{match.group(1)}"'))
    return found
