"""Render a StrategyDoc as Markdown and PDF."""

from pathlib import Path

import markdown
from fpdf import FPDF, TextStyle
from pydantic import BaseModel

from growthcrew.agents.strategy_models import CritiquePoint, StrategyDoc
from growthcrew.frameworks.base import Point

# (regular, bold, italic, bold italic). The first set found on this machine is used.
FONT_SETS = (
    tuple(
        f"/System/Library/Fonts/Supplemental/Arial{suffix}.ttf"
        for suffix in ("", " Bold", " Italic", " Bold Italic")
    ),
    tuple(
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf"
        for suffix in ("", "-Bold", "-Oblique", "-BoldOblique")
    ),
)


def _refs(support: list[str]) -> str:
    return f" `[{', '.join(support)}]`" if support else " `[unsupported]`"


def _points(points: list[Point]) -> list[str]:
    return [f"- {point.text}{_refs(point.support)}" for point in points] or ["- (none)"]


def _cell(text: object) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return [*lines, ""]


def _critique(title: str, points: list[CritiquePoint]) -> list[str]:
    lines = [f"### {title}", ""]
    for point in points:
        lines += [
            f"- **{point.section}** ({point.severity}): {point.issue}",
            f"    - Why it matters: {point.why_it_matters}",
            f"    - Suggested fix: {point.suggested_fix}",
        ]
    return [*lines, ""] if points else [*lines, "- (none)", ""]


def to_markdown(doc: StrategyDoc) -> str:
    pos, house = doc.positioning, doc.messaging_house
    out = [
        f"# Marketing strategy: {doc.brand_name}",
        "",
        f"Status: **{doc.status.value}** · drafted {doc.created_at:%d %b %Y} · "
        "codes in brackets refer to the evidence list at the end",
        "",
    ]
    if doc.issues:
        out += ["## Needs attention before approval", "", *(f"- {i}" for i in doc.issues), ""]

    out += ["## Positioning", "", f"**{pos.positioning_statement}**", ""]
    for title, points in (
        ("Competitive alternatives", pos.competitive_alternatives),
        ("Unique attributes", pos.unique_attributes),
        ("Value", pos.value),
        ("Target customers", pos.target_customers),
        ("Market category", [pos.market_category]),
    ):
        out += [f"### {title}", "", *_points(points), ""]

    out += ["## Jobs to be done", ""]
    for title, jobs in (
        ("Functional", doc.jobs.functional_jobs),
        ("Emotional", doc.jobs.emotional_jobs),
        ("Social", doc.jobs.social_jobs),
    ):
        out += [f"### {title}", ""]
        out += [f"- {job.statement}{_refs(job.support)}" for job in jobs] or ["- (none)"]
        out.append("")

    out += ["## Messaging house", "", f"**Core message:** {house.core_message.text}"]
    out[-1] += _refs(house.core_message.support)
    out.append("")
    for number, pillar in enumerate(house.pillars, 1):
        out += [f"### Pillar {number}: {pillar.message}{_refs(pillar.support)}", ""]
        out += [*_points(pillar.proof_points), ""]

    out += ["## ICP priorities", ""]
    for number, icp in enumerate(doc.icp_priorities, 1):
        out.append(f"{number}. **{icp.segment}**: {icp.rationale}{_refs(icp.support)}")
    out.append("")

    out += ["## 90-day channel plan", ""]
    for stage in doc.channel_plan.stages:
        out += [f"### {stage.stage.capitalize()}: {stage.objective}", ""]
        out += _table(
            ["Channel", "Tactic", "Timing", "Budget", "Evidence"],
            [
                [p.channel, p.tactic, p.timing, f"{p.budget_pct}%", ", ".join(p.support) or "none"]
                for p in stage.channels
            ],
        )
        out += [f"- KPI: {kpi.metric}, target {kpi.target}" for kpi in stage.kpis]
        out.append("")

    out += ["## Content pillars", ""]
    for pillar in doc.content_pillars:
        out += [f"- **{pillar.name}**: {pillar.description}{_refs(pillar.support)}"]
        out += [f"    - {topic}" for topic in pillar.example_topics]
    out.append("")

    out += ["## Experiments, ranked by ICE", ""]
    out += _table(
        ["#", "Experiment", "Impact", "Confidence", "Ease", "ICE"],
        [
            [n, e.name, e.impact, e.confidence, e.ease, e.ice]
            for n, e in enumerate(doc.experiments, 1)
        ],
    )
    for number, e in enumerate(doc.experiments, 1):
        out += [
            f"**{number}. {e.name}**{_refs(e.support)}",
            "",
            f"- Hypothesis: {e.hypothesis}",
            f"- Metric: {e.metric}",
            f"- Minimum sample: {e.minimum_sample}",
            f"- Decision rule: {e.decision_rule}",
            "",
        ]

    out += ["## KPIs", ""]
    out += _table(
        ["Metric", "Baseline", "Target", "Timeframe", "Evidence"],
        [[k.metric, k.baseline, k.target, k.timeframe, ", ".join(k.support)] for k in doc.kpis],
    )

    out += ["## Devil's advocate review", "", doc.critique.summary, ""]
    out += _critique("Weak assumptions", doc.critique.weak_assumptions)
    out += _critique("Missing risks", doc.critique.missing_risks)
    out += ["### What changed in response", ""]
    for change in doc.revision.changes:
        decision = change.decision.replace("_", " ")
        out += [
            f"- **{decision.capitalize()}**: {change.critique_issue}",
            f"    - Change: {change.change_made}",
            f"    - Reason: {change.reason}",
        ]
    out.append("")

    cited = {ref for ref in _all_refs(doc)}
    out += ["## Evidence cited", ""]
    for item in doc.evidence:
        if item.id in cited:
            quality = f" ({item.quality})" if item.quality else ""
            source = f" Source: {item.source_url}" if item.source_url else ""
            out.append(f"- `{item.id}`{quality} {item.text}{source}")
    return "\n".join(out) + "\n"


def _all_refs(node: object) -> list[str]:
    if isinstance(node, list):
        return [ref for item in node for ref in _all_refs(item)]
    if isinstance(node, BaseModel):
        refs = list(getattr(node, "support", []))
        for name in type(node).model_fields:
            if name not in ("support", "first_draft", "evidence"):
                refs += _all_refs(getattr(node, name))
        return refs
    return []


def to_pdf(markdown_text: str, path: Path) -> Path:
    html = markdown.markdown(markdown_text, extensions=["tables", "sane_lists"])
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(auto=True, margin=16)
    fonts = next((s for s in FONT_SETS if all(Path(f).exists() for f in s)), None)
    if fonts:
        for style, file in zip(("", "B", "I", "BI"), fonts, strict=True):
            pdf.add_font("Report", style, file)
        family = "Report"
    else:
        # Core PDF fonts only cover Latin-1, so anything else is replaced.
        html = html.encode("latin-1", "replace").decode("latin-1")
        family = "Helvetica"
    pdf.set_font(family, size=10)
    pdf.add_page()
    ink = (25, 35, 55)
    styles = {
        "h1": TextStyle(color=ink, font_size_pt=22, t_margin=0, b_margin=3),
        "h2": TextStyle(color=ink, font_size_pt=15, t_margin=5, b_margin=2),
        "h3": TextStyle(color=ink, font_size_pt=11.5, font_style="B", t_margin=3, b_margin=1),
    }
    pdf.write_html(
        html, font_family=family, tag_styles=styles, li_prefix_color=ink, table_line_separators=True
    )
    pdf.output(str(path))
    return path


def save_strategy(doc: StrategyDoc, root: Path, pdf: bool = True) -> Path:
    folder = root / doc.workspace / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    stem = doc.created_at.strftime("%Y%m%d-%H%M%S")
    (folder / f"{stem}.json").write_text(doc.model_dump_json(indent=2))
    text = to_markdown(doc)
    report = folder / f"{stem}.md"
    report.write_text(text)
    if pdf:
        to_pdf(text, folder / f"{stem}.pdf")
    return report
