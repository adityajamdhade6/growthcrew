"""Guardrails: content that must not reach a reviewer as approvable, whatever it scores.

Every rule here is a pattern check, so it is fast, repeatable and conservative. It will miss
defamation or risky claims phrased in ways the patterns do not cover; the critic and the
human reviewer remain the checks on those.
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.brain.models import Brain
from growthcrew.content.checks import unverified_facts
from growthcrew.db.models import GuardrailBlock

logger = logging.getLogger(__name__)

Rule = Literal["invented_proof", "competitor_defamation", "sensitive_claim", "off_brand"]

_DEFAMATORY = (
    r"scam\w*|fraud\w*|liars?|lying|lies\b|rip-?offs?|steals?|stealing|illegal\w*|criminals?|"
    r"crooks?|cheats?|cheating|dishonest|incompetent|useless|garbage|trash|worst|"
    r"can't be trusted|cannot be trusted|don't trust|going bankrupt|going out of business"
)
SENSITIVE: dict[str, tuple[str, ...]] = {
    "health": (
        r"\b(cures?|heals?)\b",
        r"\btreats?\b[^.!?]{0,40}\b(disease|condition|illness|pain|anxiety|depression|acne|"
        r"eczema|infections?|insomnia|symptoms?|sweats|allergies|arthritis)",
        r"\bprevents? (cancer|disease|illness|infections?|heart)",
        r"\b(clinically|medically|scientifically) proven\b",
        r"\bFDA[- ]approved\b",
        r"\bdoctor[- ]recommended\b",
        r"\bno side effects\b",
        r"\blose \d+ ?(pounds|lbs|kg|kilos)\b",
    ),
    "finance": (
        r"\bguaranteed (returns?|income|profits?|roi|savings)\b",
        r"\b(risk[- ]free|no[- ]risk|zero[- ]risk)\b",
        r"\bget rich\b",
        r"\bdouble your (money|income|revenue)\b",
        r"\bfinancial freedom\b",
        r"\bcan't lose\b",
    ),
    "legal": (
        r"\bguaranteed? (to )?(win|settlement|approval|outcome|visa|acquittal)",
        r"\b(win|wins|won) your (case|claim|lawsuit)\b",
        r"\b(100%|fully|completely) (legal|compliant|lawsuit[- ]proof)\b",
        r"\bno (legal )?liability\b",
        r"\bavoid (all )?(taxes|tax entirely|prosecution)\b",
    ),
}


class Violation(BaseModel):
    rule: Rule
    line: int
    excerpt: str
    reason: str


def check(
    lines: list[str], brand: Brain | None = None, facts: str | None = None
) -> list[Violation]:
    """Return every guardrail violation in a draft.

    `facts` is the text a piece may draw numbers and testimonials from (see
    content.checks.allowed_facts). Without it, the invented-proof rule is skipped.
    """
    found: list[Violation] = []
    if facts is not None:
        for number, fact in unverified_facts(lines, facts):
            found.append(
                Violation(
                    rule="invented_proof",
                    line=number,
                    excerpt=fact,
                    reason="Statistic or testimonial not found in the brand's proof",
                )  # fmt: skip
            )
    competitors = [c.name for c in brand.competitors if c.name] if brand else []
    banned = [*brand.voice.guide.banned_phrases, *brand.voice.dont_words] if brand else []
    for number, line in enumerate(lines, 1):
        for name in competitors:
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", line, re.IGNORECASE):
                term = re.search(rf"\b({_DEFAMATORY})", line, re.IGNORECASE)
                if term:
                    found.append(
                        Violation(
                            rule="competitor_defamation",
                            line=number,
                            excerpt=line[:160],
                            reason=f"Disparages {name} ('{term.group(1)}')",
                        )  # fmt: skip
                    )
        for topic, patterns in SENSITIVE.items():
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    found.append(
                        Violation(
                            rule="sensitive_claim",
                            line=number,
                            excerpt=match.group(0),
                            reason=f"{topic.capitalize()} claim that needs substantiation",
                        )  # fmt: skip
                    )
        for phrase in banned:
            if phrase and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", line, re.IGNORECASE):
                found.append(
                    Violation(
                        rule="off_brand",
                        line=number,
                        excerpt=phrase,
                        reason="Uses a word or phrase the brand has banned",
                    )  # fmt: skip
                )
    return found


def log_blocks(
    engine: Engine, workspace: str, piece_id: str, stage: str, violations: list[Violation]
) -> None:
    """Record every block, in the database and the application log."""
    if not violations:
        return
    with Session(engine) as session:
        for violation in violations:
            session.add(
                GuardrailBlock(
                    workspace=workspace,
                    piece_id=piece_id,
                    stage=stage,
                    rule=violation.rule,
                    line=violation.line,
                    excerpt=violation.excerpt,
                    reason=violation.reason,
                )  # fmt: skip
            )
            logger.warning(
                "GUARDRAIL %s %s line %d: %s (%s)",
                violation.rule, piece_id, violation.line, violation.reason, violation.excerpt,
            )  # fmt: skip
        session.commit()
