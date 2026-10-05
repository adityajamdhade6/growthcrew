"""SEO agent: keyword gaps against competitors, topic clusters, briefs, and ranking moves.

Inputs are exports a person supplies, in `workspaces/<brand>/seo/`:

- `search_console*.csv`: a Search Console performance export, with columns query, page,
  clicks, impressions, position, and optionally date.
- `keywords.csv`: keyword research in long form: keyword, volume, competitor,
  competitor_position (one row per keyword and competitor).

Gaps, clusters and ranking moves are computed in code. Clustering is lexical (keywords that
share words go together); it does not know that two different words mean the same thing. The
model writes one brief per cluster, and any internal link it suggests that is not one of the
brand's own pages from Search Console is removed.
"""

import csv
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew.config import AgentRole
from growthcrew.db.models import KeywordRank
from growthcrew.llm import LLM
from growthcrew.monitor.signals import Finding, Source
from growthcrew.tools.untrusted import DATA_RULE, wrap

# A competitor ranks for a keyword if it is in the top 10; we "have" it if we are in the top 20.
COMPETITOR_TOP = 10
OUR_TOP = 20
# A tracked keyword moving this many places between exports is worth a signal.
RANK_MOVE = 5
# Two keywords whose word sets overlap this much (Jaccard) go in one cluster.
CLUSTER_OVERLAP = 0.5
STOPWORDS = {"a", "an", "and", "the", "for", "to", "of", "in", "on", "with", "best", "near",
             "me", "how", "what", "is", "vs", "my", "your", "do", "does"}  # fmt: skip

BRIEF_SYSTEM = f"""You are the SEO lead on a small-business marketing team. For the topic \
cluster given, write a content brief: the search intent (informational, commercial, \
transactional or navigational), a working title, an outline of section headings, the \
questions the piece must answer, and internal links. Internal links must be chosen only from \
the list of the brand's own pages provided; if none fits, give none. Do not invent \
statistics.

{DATA_RULE}"""


class Gap(BaseModel):
    keyword: str
    volume: int
    competitor: str
    competitor_position: float
    our_position: float | None


class Cluster(BaseModel):
    head: str
    keywords: list[str]
    volume: int
    gaps: list[Gap]


class Brief(BaseModel):
    search_intent: Literal["informational", "commercial", "transactional", "navigational"]
    title: str
    outline: list[str]
    questions: list[str]
    internal_links: list[str]


class ClusterBrief(BaseModel):
    cluster: Cluster
    brief: Brief


class SeoReport(BaseModel):
    gaps: list[Gap]
    clusters: list[ClusterBrief]
    links_removed: int = 0


def _number(value: str | None) -> float:
    try:
        return float(str(value or "0").replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0


def _date(value: str | None, fallback: datetime) -> datetime:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
    return fallback


def _rows(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return [
            {str(k).lower().strip(): v for k, v in row.items()} for row in csv.DictReader(handle)
        ]


def ingest_search_console(engine: Engine, workspace: str, root: Path) -> int:
    """Load Search Console exports into `KeywordRank`. Re-loading the same file adds nothing."""
    added = 0
    with Session(engine) as session:
        existing = {
            (row.keyword, row.page, row.date.date())
            for row in session.exec(select(KeywordRank).where(KeywordRank.workspace == workspace))
        }
        for path in sorted((root / workspace / "seo").glob("search_console*.csv")):
            fallback = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            for row in _rows(path):
                keyword = (row.get("query") or row.get("top queries") or "").strip().lower()
                if not keyword:
                    continue
                when = _date(row.get("date"), fallback)
                page = (row.get("page") or row.get("top pages") or "").strip()
                if (keyword, page, when.date()) in existing:
                    continue
                existing.add((keyword, page, when.date()))
                session.add(
                    KeywordRank(
                        workspace=workspace,
                        keyword=keyword,
                        page=page,
                        date=when,
                        position=_number(row.get("position")),
                        clicks=_number(row.get("clicks")),
                        impressions=_number(row.get("impressions")),
                    )
                )
                added += 1
        session.commit()
    return added


def our_positions(engine: Engine, workspace: str) -> tuple[dict[str, float], set[str]]:
    """Our latest best position per keyword, and the brand's own pages seen in Search Console."""
    best: dict[str, tuple[datetime, float]] = {}
    pages: set[str] = set()
    with Session(engine) as session:
        for row in session.exec(select(KeywordRank).where(KeywordRank.workspace == workspace)):
            if row.page:
                pages.add(row.page)
            current = best.get(row.keyword)
            if current is None or row.date > current[0]:
                best[row.keyword] = (row.date, row.position)
            elif row.date == current[0]:
                best[row.keyword] = (row.date, min(current[1], row.position))
    return {keyword: position for keyword, (_, position) in best.items()}, pages


def find_gaps(keyword_rows: list[dict], ours: dict[str, float]) -> list[Gap]:
    """Keywords a competitor ranks top 10 for where we are absent or below 20th."""
    gaps: dict[str, Gap] = {}
    for row in keyword_rows:
        keyword = (row.get("keyword") or "").strip().lower()
        position = _number(row.get("competitor_position"))
        if not keyword or not 0 < position <= COMPETITOR_TOP:
            continue
        mine = ours.get(keyword)
        if mine is not None and mine <= OUR_TOP:
            continue
        gap = Gap(
            keyword=keyword,
            volume=int(_number(row.get("volume"))),
            competitor=(row.get("competitor") or "").strip(),
            competitor_position=position,
            our_position=mine,
        )
        if keyword not in gaps or position < gaps[keyword].competitor_position:
            gaps[keyword] = gap
    return sorted(gaps.values(), key=lambda gap: (-gap.volume, gap.keyword))


def _words(keyword: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", keyword.lower()) if w not in STOPWORDS}


def cluster(gaps: list[Gap]) -> list[Cluster]:
    """Group gaps that share words. Greedy, highest volume first; each gap joins one cluster."""
    clusters: list[tuple[set[str], list[Gap]]] = []
    for gap in sorted(gaps, key=lambda g: -g.volume):
        words = _words(gap.keyword)
        for head_words, members in clusters:
            union = words | head_words
            if union and len(words & head_words) / len(union) >= CLUSTER_OVERLAP:
                members.append(gap)
                break
        else:
            clusters.append((words, [gap]))
    out = [
        Cluster(
            head=members[0].keyword,
            keywords=[g.keyword for g in members],
            volume=sum(g.volume for g in members),
            gaps=members,
        )
        for _, members in clusters
    ]
    return sorted(out, key=lambda c: -c.volume)


def ranking_moves(engine: Engine, workspace: str) -> list[tuple[str, float, float, datetime]]:
    """Keywords whose best position moved by RANK_MOVE or more between the last two dates."""
    by_keyword: dict[str, dict[datetime, float]] = defaultdict(dict)
    with Session(engine) as session:
        for row in session.exec(select(KeywordRank).where(KeywordRank.workspace == workspace)):
            day = row.date.replace(hour=0, minute=0, second=0, microsecond=0)
            current = by_keyword[row.keyword].get(day)
            by_keyword[row.keyword][day] = (
                row.position if current is None else min(current, row.position)
            )
    moves = []
    for keyword, history in by_keyword.items():
        if len(history) < 2:
            continue
        (_, before), (when, after) = sorted(history.items())[-2:]
        if abs(after - before) >= RANK_MOVE:
            moves.append((keyword, before, after, when))
    return sorted(moves, key=lambda move: -abs(move[2] - move[1]))


def ranking_history(engine: Engine, workspace: str, keyword: str) -> list[dict]:
    with Session(engine) as session:
        rows = session.exec(
            select(KeywordRank)
            .where(KeywordRank.workspace == workspace, KeywordRank.keyword == keyword.lower())
            .order_by(KeywordRank.date)
        ).all()
    return [{"date": f"{row.date:%Y-%m-%d}", "position": row.position, "page": row.page}
            for row in rows]  # fmt: skip


class SeoAgent:
    def __init__(self, llm: LLM, engine: Engine) -> None:
        self.llm = llm
        self.engine = engine

    def run(
        self, workspace: str, root: Path, max_briefs: int = 3
    ) -> tuple[SeoReport, list[Finding]]:
        ingest_search_console(self.engine, workspace, root)
        ours, pages = our_positions(self.engine, workspace)
        keyword_file = root / workspace / "seo" / "keywords.csv"
        gaps = find_gaps(_rows(keyword_file), ours) if keyword_file.exists() else []
        source_url = f"workspace://{workspace}/seo/keywords.csv"
        today = f"{datetime.now(UTC):%Y-%m-%d}"
        briefs: list[ClusterBrief] = []
        removed = 0
        for group in cluster(gaps)[:max_briefs]:
            listing = "\n".join(
                f"- {g.keyword} (volume {g.volume}; {g.competitor} ranks "
                f"{g.competitor_position:g}; we rank "
                f"{'nowhere' if g.our_position is None else f'{g.our_position:g}'})"
                for g in group.gaps
            )
            brief = self.llm.call(
                AgentRole.MONITOR,
                system=BRIEF_SYSTEM,
                user=f"Topic cluster '{group.head}':\n{wrap(listing, source_url, today)}\n\n"
                "The brand's own pages (the only allowed internal links):\n"
                + (wrap("\n".join(sorted(pages)), "search console pages") if pages else "(none)"),
                output_model=Brief,
                workspace=workspace,
                tag=f"monitor|seo|{group.head}",
            )
            kept = [link for link in brief.internal_links if link in pages]
            removed += len(brief.internal_links) - len(kept)
            brief.internal_links = kept
            briefs.append(ClusterBrief(cluster=group, brief=brief))

        findings = [
            Finding(
                monitor="seo",
                category="keyword_gap",
                title=f"Content gap: {item.cluster.head}",
                summary=f"{len(item.cluster.keywords)} keyword"
                f"{'s' if len(item.cluster.keywords) != 1 else ''} with "
                f"{item.cluster.volume:,} monthly searches where a competitor ranks and we do "
                f"not ({', '.join(item.cluster.keywords[:6])}). Brief: '{item.brief.title}' "
                f"({item.brief.search_intent}).",
                suggested_response="Outline: " + "; ".join(item.brief.outline[:6]),
                sources=[Source(url=source_url, date=today)],
                base_importance=min(0.85, 0.4 + item.cluster.volume / 20000),
            )
            for item in briefs
        ]
        for keyword, before, after, when in ranking_moves(self.engine, workspace)[:10]:
            better = after < before
            findings.append(
                Finding(
                    monitor="seo",
                    category="ranking_move",
                    title=f"'{keyword}' {'rose' if better else 'fell'} from "
                    f"{before:g} to {after:g}",
                    summary=f"Our best position for '{keyword}' moved from {before:g} to "
                    f"{after:g} in the latest Search Console export.",
                    suggested_response=""
                    if better
                    else "Check the ranking page for changes and refresh it if it has gone stale.",
                    sources=[
                        Source(
                            url=f"workspace://{workspace}/seo/search_console.csv",
                            date=f"{when:%Y-%m-%d}",
                        )
                    ],  # fmt: skip
                    base_importance=0.45 if better else 0.65,
                )
            )
        return SeoReport(gaps=gaps, clusters=briefs, links_removed=removed), findings
