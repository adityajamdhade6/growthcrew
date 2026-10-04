"""The pattern miner: find what keeps working for this brand, and notice when it stops.

Patterns are observational: they come from comparing pieces that happened to differ, not from
a controlled test. So the miner is cautious. It compares piece against piece (not pooled
impressions, which would make a few big posts look like certainty), needs the pattern to hold
on two runs before it becomes an active rule, looks only at a rolling window so that newer
results outweigh old ones, and retires a rule when the evidence goes.
"""

import json
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.db.models import MemoryPiece, PlaybookRule, RuleEvent
from growthcrew.memory import store
from growthcrew.memory.features import BY_KEY, FEATURES
from growthcrew.naming import display

# Only pieces published in this window count, so the playbook follows what works now.
WINDOW_WEEKS = 6
MIN_PIECES = 5
# Pieces needed on each side within a group, when controlling for another feature.
MIN_STRATUM = 3
MIN_LIFT_PCT = 10.0
# Evidence needed for a rule to hold on a run.
HOLD_PROBABILITY = 0.9
RUNS_TO_ACTIVATE = 2
RUNS_TO_RETIRE = 3


def compare(
    with_rates: list[float], without_rates: list[float], draws: int = 4000, seed: int = 0
) -> tuple[float, float]:
    """Probability that pieces with the feature do better on average, and the lift in percent.

    A Bayesian bootstrap over pieces: each piece is one observation, however many impressions
    it had, so the spread between pieces is part of the uncertainty.
    """
    rng = np.random.default_rng(seed)
    a, b = np.asarray(with_rates), np.asarray(without_rates)
    mean_a = rng.dirichlet(np.ones(a.size), size=draws) @ a
    mean_b = rng.dirichlet(np.ones(b.size), size=draws) @ b
    lift = (a.mean() / b.mean() - 1) * 100 if b.mean() else 0.0
    return float((mean_a > mean_b).mean()), float(lift)


def compare_within(
    items: list[tuple[float, bool, bool]], draws: int = 4000, seed: int = 0
) -> tuple[float, float] | None:
    """The same comparison, made separately among pieces that have another feature and among
    those that do not, then combined.

    `items` are (rate, has the feature, has the other feature). If two features usually occur
    together, the weaker one can look like it works when it is only riding along. Comparing
    within each group of the stronger one removes that. Returns None when the two cannot be
    told apart: no group has enough pieces on both sides.
    """
    rng = np.random.default_rng(seed)
    difference, weight, base = np.zeros(draws), 0, 0.0
    for stratum in (True, False):
        a = np.asarray([rate for rate, has, other in items if has and other == stratum])
        b = np.asarray([rate for rate, has, other in items if not has and other == stratum])
        if a.size < MIN_STRATUM or b.size < MIN_STRATUM:
            continue
        size = a.size + b.size
        mean_a = rng.dirichlet(np.ones(a.size), size=draws) @ a
        mean_b = rng.dirichlet(np.ones(b.size), size=draws) @ b
        difference += size * (mean_a - mean_b)
        base += size * b.mean()
        weight += size
    if not weight:
        return None
    lift = float(difference.mean() / base * 100) if base else 0.0
    return float((difference > 0).mean()), lift


def _statement(content_type: str, feature_key: str, lift: float, metric: str) -> str:
    feature = BY_KEY[feature_key]
    better, worse = (
        (feature.with_it, feature.without_it)
        if lift >= 0
        else (feature.without_it, feature.with_it)
    )
    return f"On {display(content_type, capital=False)}s, {better} beats {worse} on {metric}"


def mine(engine: Engine, workspace: str, as_of: datetime) -> list[PlaybookRule]:
    """One weekly run. Updates every rule's status and history, and returns the rules."""
    store.sync(engine, workspace)
    since = as_of - timedelta(weeks=WINDOW_WEEKS)
    recent = [p for p in store.pieces(engine, workspace, since=since) if p.published_on <= as_of]
    by_type: dict[str, list[MemoryPiece]] = {}
    for piece in recent:
        by_type.setdefault(piece.content_type, []).append(piece)

    with Session(engine, expire_on_commit=False) as session:
        rules = {
            (rule.content_type, rule.feature): rule
            for rule in session.exec(
                select(PlaybookRule).where(PlaybookRule.workspace == workspace)
            )
        }
        seen = set()
        for content_type, items in by_type.items():
            flags = {piece.id: json.loads(piece.features) for piece in items}
            raw = {}
            for feature in FEATURES:
                has = [p.rate for p in items if flags[p.id].get(feature.key)]
                lacks = [p.rate for p in items if not flags[p.id].get(feature.key)]
                if len(has) >= MIN_PIECES and len(lacks) >= MIN_PIECES:
                    probability, lift = compare(has, lacks)
                    raw[feature.key] = (probability, lift, len(has), len(lacks))
            if not raw:
                continue
            # The feature with the strongest evidence stands on its own. Every other feature
            # must show its effect within groups of that one, so a feature that merely tends
            # to appear alongside the strong one does not become a rule of its own.
            strongest = max(raw, key=lambda key: abs(raw[key][0] - 0.5))
            for feature_key, (probability, lift, with_count, without_count) in raw.items():
                key = (content_type, feature_key)
                rule = rules.get(key)
                if feature_key != strongest:
                    adjusted = compare_within(
                        [
                            (
                                p.rate,
                                bool(flags[p.id].get(feature_key)),
                                bool(flags[p.id].get(strongest)),
                            )
                            for p in items
                        ]
                    )
                    if adjusted is None:
                        # Cannot be told apart from the stronger feature: no new rule, and an
                        # existing one is not credited with this run.
                        if rule is not None:
                            seen.add(key)
                            _update(
                                session,
                                rule,
                                as_of,
                                False,
                                0.5,
                                rule.lift_pct,
                                with_count,
                                without_count,
                                note="Cannot be separated from a stronger pattern",
                            )
                        continue
                    probability, lift = adjusted
                # A rule is stated in whichever direction wins; its evidence is measured that way.
                strength = probability if lift >= 0 else 1 - probability
                holds = strength >= HOLD_PROBABILITY and abs(lift) >= MIN_LIFT_PCT
                direction = 1.0
                if rule is None:
                    if not holds:
                        continue
                    rule = PlaybookRule(
                        workspace=workspace,
                        content_type=content_type,
                        feature=feature_key,
                        statement=_statement(content_type, feature_key, lift, items[0].metric),
                        found_on=as_of,
                        updated_on=as_of,
                    )
                    rules[key] = rule
                elif (lift >= 0) != (rule.lift_pct >= 0):
                    # The effect now points the other way from when the rule was found.
                    direction, strength, holds = -1.0, 1 - strength, False
                seen.add(key)
                _update(
                    session,
                    rule,
                    as_of,
                    holds,
                    strength,
                    lift if direction > 0 else rule.lift_pct,
                    with_count,
                    without_count,
                    flipped=direction < 0,
                )
        # A rule with too little recent evidence to test is not kept alive by silence.
        for key, rule in rules.items():
            if key not in seen and rule.status != "retired":
                _update(
                    session,
                    rule,
                    as_of,
                    False,
                    0.0,
                    rule.lift_pct,
                    0,
                    0,
                    note="Too few recent pieces to test",
                )
        session.commit()
        return sorted(rules.values(), key=lambda rule: (rule.status != "active", -rule.probability))


def _update(
    session: Session,
    rule: PlaybookRule,
    as_of: datetime,
    holds: bool,
    strength: float,
    lift: float,
    with_count: int,
    without_count: int,
    flipped: bool = False,
    note: str = "",
) -> None:
    before = rule.status
    rule.probability, rule.updated_on = round(strength, 3), as_of
    if with_count:
        rule.lift_pct, rule.pieces_with, rule.pieces_without = (
            round(lift, 1),
            with_count,
            without_count,
        )
    if holds:
        rule.held_runs, rule.failed_runs = rule.held_runs + 1, 0
        if rule.status == "retired":
            rule.status, rule.held_runs = "candidate", 1  # it has to earn its place again
        if rule.status in ("candidate", "weakening") and rule.held_runs >= RUNS_TO_ACTIVATE:
            rule.status = "active"
    else:
        rule.held_runs, rule.failed_runs = 0, rule.failed_runs + 1
        if rule.status == "candidate":
            # A candidate that does not hold on its second look was probably noise.
            rule.status = "retired"
        elif rule.status in ("active", "weakening"):
            # One miss takes a rule out of use; agents only follow active rules. Repeated
            # misses, or the effect reversing, retire it.
            gone = flipped or rule.failed_runs >= RUNS_TO_RETIRE
            rule.status = "retired" if gone else "weakening"
    session.add(rule)
    session.flush()
    if not note:
        note = {
            (True, "active"): "Evidence held",
            (False, "weakening"): "Newer results no longer clearly support it",
            (False, "retired"): "Stopped holding in recent results",
        }.get((holds, rule.status), "Evidence held" if holds else "Evidence weaker this run")
    if rule.status != before:
        note = f"{before} → {rule.status}. {note}"
    session.add(
        RuleEvent(
            rule_id=rule.id,
            at=as_of,
            status=rule.status,
            lift_pct=rule.lift_pct,
            probability=rule.probability,
            pieces_with=with_count,
            pieces_without=without_count,
            note=note,
        )
    )
