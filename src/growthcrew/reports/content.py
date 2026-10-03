"""Render content pieces and batches as Markdown, and save them."""

from pathlib import Path

from growthcrew.agents.content_models import CRITERIA, Batch, PieceRecord, Version


def _quote(text: str) -> list[str]:
    return [f"> {line}" for line in text.splitlines()]


def _scores(versions: list[Version]) -> list[str]:
    lines = [
        "| Round | " + " | ".join(name.replace("_", " ") for name in CRITERIA) + " | Passed |",
        "|" + "---|" * (len(CRITERIA) + 2),
    ]
    for version in versions:
        scores = version.critique.scores()
        row = " | ".join(str(scores[name]) for name in CRITERIA)
        lines.append(f"| {version.round} | {row} | {'yes' if version.critique.passed else 'no'} |")
    return lines


def piece_history(record: PieceRecord) -> str:
    """Every version of a piece with its scores and edits: the before/after."""
    request, meta = record.request, record.final.metadata
    out = [
        f"# {record.id}",
        "",
        f"{request.content_type} · {request.funnel_stage} · "
        f"angle: {record.angle or 'n/a'} · status: {record.status.value} · "
        f"{'passed' if record.passed else 'did not reach 8 on every criterion'} "
        f"after {len(record.versions)} round(s)",
        "",
        f"- Messaging pillar: {meta.messaging_pillar}",
        f"- Target persona: {meta.target_persona}",
        f"- CTA: {meta.cta}",
        f"- Hypothesis: {meta.hypothesis}",
        "",
        "## Scores by round",
        "",
        *_scores(record.versions),
        "",
    ]
    for version in record.versions:
        label = "first draft" if version.round == 1 else f"revision {version.round - 1}"
        out += [f"## Round {version.round}: {label}", "", *_quote(version.text), ""]
        critique = version.critique
        if critique.code_findings:
            out += ["Automated findings:", *(f"- {item}" for item in critique.code_findings), ""]
        if critique.edits:
            out.append("Editor's line edits:")
            for edit in critique.edits:
                out += [
                    f"- Line {edit.line} ({edit.criterion}): {edit.reason}",
                    f"    - Was: {edit.original}",
                    f"    - Suggested: {edit.suggestion}",
                ]
            out.append("")
    return "\n".join(out) + "\n"


def batch_summary(batch: Batch) -> str:
    out = [
        f"# Content batch: {batch.workspace}, {batch.weeks} weeks",
        "",
        f"{len(batch.pieces)} pieces from {len(batch.plan)} planned items. "
        "All are pending human approval.",
        "",
        "| Day | Piece | Angle | Pillar | Rounds | Lowest score | Passed |",
        "|---|---|---|---|---|---|---|",
    ]
    for piece in batch.pieces:
        lowest = min(piece.final.critique.scores().values())
        out.append(
            f"| {piece.day} | {piece.id} | {piece.angle or ''} | "
            f"{piece.final.metadata.messaging_pillar.replace('|', '/')} | "
            f"{len(piece.versions)} | {lowest} | {'yes' if piece.passed else 'no'} |"
        )
    if batch.notes:
        out += ["", "## Notes", "", *(f"- {note}" for note in dict.fromkeys(batch.notes))]
    out += ["", "## Why each item is in the plan", ""]
    out += [f"- Day {i.day}, {i.request.content_type}: {i.rationale}" for i in batch.plan]
    out += ["", "## Final versions", ""]
    for piece in batch.pieces:
        out += [f"### {piece.id}", "", *_quote(piece.final.text), ""]
    return "\n".join(out) + "\n"


def save_batch(batch: Batch, root: Path) -> Path:
    folder = root / batch.workspace / "content" / batch.created_at.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "batch.json").write_text(batch.model_dump_json(indent=2))
    (folder / "batch.md").write_text(batch_summary(batch))
    for piece in batch.pieces:
        (folder / f"{piece.id}.md").write_text(piece_history(piece))
    return folder
