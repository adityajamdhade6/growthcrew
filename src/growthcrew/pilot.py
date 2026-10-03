"""A 60-day pilot: baseline, plan, weekly tracker, day-30/60 reports, and a testimonial.

Everything here is arithmetic and templates; nothing calls the model. A pilot is a
before/after comparison, not an experiment, and the reports say so.
"""

import csv
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from growthcrew.agents.strategist import load_latest_strategy
from growthcrew.db.models import (
    Approval,
    BrainVersion,
    ChangeDecision,
    Draft,
    Learnings,
    LLMCall,
    PerformanceRow,
)

WORKSPACES_DIR = Path("workspaces")
PILOT_DAYS = 60
WEEKS = 9  # eight full weeks plus the last four days
# Below this many events in a period, a change is noise, not a result.
SMALL_COUNT = 30

Kind = Literal["stock", "flow", "rate", "hours"]
# key, label, kind, what to record for the 30 days before the pilot
METRICS: tuple[tuple[str, str, Kind, str], ...] = (
    ("followers", "Followers", "stock", "Total across the channels in scope, on the day before the pilot starts"),
    ("impressions", "Impressions", "flow", "Total over the 30 days"),
    ("engagement_rate_pct", "Engagement rate (%)", "rate", "Average over the 30 days: engagements / impressions"),
    ("sessions_from_social", "Website sessions from social", "flow", "Total over the 30 days (GA4: session source = social)"),
    ("leads", "Leads or inquiries", "flow", "Total over the 30 days, from every source you can count"),
    ("email_reply_rate_pct", "Email reply rate (%)", "rate", "Replies / emails sent over the 30 days"),
    ("hours_on_marketing", "Hours on marketing per week", "hours", "Your honest average per week"),
)  # fmt: skip
USES = ("website", "case_study", "social", "sales_material")


class Pilot(BaseModel):
    workspace: str
    business: str
    start: date
    owner: str = ""

    @property
    def end(self) -> date:
        return self.start + timedelta(days=PILOT_DAYS - 1)

    def week_start(self, week: int) -> date:
        return self.start + timedelta(days=7 * (week - 1))


class Testimonial(BaseModel):
    quote: str
    name: str
    role: str = ""
    business: str = ""
    # How the person may be credited.
    attribution: Literal["full_name", "first_name_only", "anonymous"]
    # Where the quote may be used. Anything not listed is not permitted.
    allowed_uses: list[str]
    # The owner confirmed this exact wording.
    wording_confirmed_by_owner: bool
    permission_given_on: date
    recorded_by: str = ""


def pilot_dir(workspace: str, root: Path = WORKSPACES_DIR) -> Path:
    return root / workspace / "pilot"


def load_pilot(workspace: str, root: Path = WORKSPACES_DIR) -> Pilot:
    path = pilot_dir(workspace, root) / "pilot.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No pilot found for '{workspace}'. Run `growthcrew pilot init` first"
        )
    return Pilot.model_validate_json(path.read_text())


# --- 1-3, 6: the kit ---


def _plan(pilot: Pilot, root: Path) -> str:
    try:
        strategy = load_latest_strategy(pilot.workspace, root)
    except FileNotFoundError:
        strategy = None
    lines = [
        f"# 60-day pilot plan: {pilot.business}",
        "",
        f"Pilot runs {pilot.start:%d %b %Y} to {pilot.end:%d %b %Y}. "
        f"Day-30 report due {pilot.start + timedelta(days=30):%d %b}; "
        f"day-60 report due {pilot.end + timedelta(days=1):%d %b}.",
        "",
        "Agree this page with the owner before day 1. Anything in _italics_ is for you to fill in.",
        "",
        "## Channels in scope",
        "",
    ]
    if strategy:
        lines += [
            "| Channel | Tactic | Funnel stage | Budget share | Posting cadence |",
            "|---|---|---|---|---|",
        ]
        for stage in strategy.channel_plan.stages:
            for play in stage.channels:
                lines.append(
                    f"| {play.channel} | {play.tactic} | {stage.stage} | {play.budget_pct}% | _e.g. 3 per week_ |"
                )
        lines += [
            "",
            "Keep the pilot to two or three channels. Cross out the rest; a pilot spread across six proves nothing.",
        ]
    else:
        lines += [
            "_No strategy exists for this workspace yet. Run research and strategy first, then "
            "re-run `growthcrew pilot init --refresh-plan`, or list the channels by hand:_",
            "",
            "| Channel | Tactic | Posting cadence |",
            "|---|---|---|",
            "| _channel_ | _tactic_ | _cadence_ |",
        ]
    lines += ["", "## Experiments to run", ""]
    if strategy:
        lines.append(
            "The three highest-ranked experiments from the strategy. Run them one at a time unless they are on different channels."
        )
        lines.append("")
        for number, experiment in enumerate(strategy.experiments[:3], 1):
            lines += [
                f"### {number}. {experiment.name} (ICE {experiment.ice})",
                "",
                f"- Hypothesis: {experiment.hypothesis}",
                f"- Metric: {experiment.metric}",
                f"- Minimum sample: {experiment.minimum_sample}",
                f"- Decision rule: {experiment.decision_rule}",
                "- Weeks: _e.g. weeks 1 to 3_",
                "",
            ]
    else:
        lines += [
            "_Fill in from the strategy: name, hypothesis, metric, minimum sample, decision rule._",
            "",
        ]
    lines += [
        "## Success metrics and targets",
        "",
        "Set each target against the baseline you recorded, before the pilot starts. A target set "
        "after seeing results is not a target.",
        "",
        "| Metric | Baseline (30 days before) | Day-30 target | Day-60 target | Why this target |",
        "|---|---|---|---|---|",
        *(
            f"| {name} | _from baseline.csv_ | _target_ | _target_ | _reason_ |"
            for _, name, _, _ in METRICS
        ),
        "",
    ]
    if strategy:
        lines += ["Targets already in the strategy, for reference:", ""]
        lines += [
            f"- {kpi.metric}: baseline {kpi.baseline}; target {kpi.target} ({kpi.timeframe})"
            for kpi in strategy.kpis
        ]
        lines.append("")
    lines += [
        "## What would make us stop early",
        "",
        "- _e.g. the owner spends more hours per week than before, three weeks running_",
        "- _e.g. a draft that should have been blocked reaches the owner_",
        "",
        "## What else is happening during these 60 days",
        "",
        "List anything that could move the numbers on its own: a sale, a trade show, a price "
        "change, a new hire, PR, seasonality. Add to this list every week in `weekly.csv`.",
        "",
        "- _known in advance_",
        "",
    ]
    return "\n".join(lines)


CHECKLIST = """# Weekly ritual: {business}

About 30 minutes, same day each week. Tick each step in order; the tracker shows which weeks
were complete.

## 1. Upload data (5 min)
- [ ] Export last week's numbers from each channel in scope and upload the CSVs
      (web app: Settings, Connected data sources).
- [ ] Check how many rows matched a piece. Fix unmatched ones by adding the tracking key or
      the published link.

## 2. Review WeeklyLearnings (10 min)
- [ ] Run this week's analysis (web app: Results, "Write this week's learnings").
- [ ] Read what worked and what did not. Check every "winner" says significant; ignore the rest.
- [ ] Read the three proposed changes and the strategist's ruling on each. Disagree? Comment
      on the strategy section.

## 3. Approve content (10 min)
- [ ] Start this week's cycle if it has not run.
- [ ] Approve, edit or reject every draft. Do not approve to be polite; edits teach the system.
- [ ] Publish what is scheduled and mark it published, with its link.

## 4. Log the week (5 min)
- [ ] Fill in this week's row in `weekly.csv`: the seven metrics.
- [ ] Log your hours on marketing this week, including time spent reviewing in GrowthCrew.
- [ ] Note anything else that happened which could have moved the numbers (`other_activity`).
- [ ] Run `growthcrew pilot track {workspace}` and glance at the tracker.
"""

TESTIMONIAL_REQUEST = """# Asking {business} for a testimonial

Ask after the day-60 report, once the owner has seen the results, caveats included. Do not
ask before, and do not ask if the pilot did not help them.

## The ask

> Would you be willing to say a few words about how the pilot went? It can be as short as two
> sentences, and I will only use it where you say I can.

## Questions that get a useful answer

1. What was marketing like for you before the pilot?
2. What changed in your week?
3. Was there a moment you thought "this is working", or "this is not"?
4. What would you tell another business owner who is considering it?
5. What should I fix?

## Rules

- The words are theirs. Do not write the quote for them. You may trim for length; if you do,
  send them the trimmed version and get a yes on that exact wording.
- Do not put a number in their mouth. If they quote a result, check it against the day-60
  report; if it does not match, ask them to drop or correct it.
- Ask separately for each use: website, case study, social posts, sales material.
- Ask how they want to be credited: full name and business, first name only, or anonymous.
- They can withdraw permission at any time. If they do, remove the quote everywhere.

## Recording it

Save their words to a text file, then:

    growthcrew pilot testimonial {workspace} --quote-file quote.txt --name "Their Name" \\
        --role "Owner" --attribution full_name --allow website,case_study --confirmed

`--confirmed` means the owner has approved this exact wording. Without it, nothing is saved.
"""


def init_pilot(
    workspace: str, business: str, start: date, owner: str = "", root: Path = WORKSPACES_DIR
) -> Path:
    """Create the pilot kit. Files a human has started filling in are never overwritten."""
    pilot = Pilot(workspace=workspace, business=business, start=start, owner=owner)
    folder = pilot_dir(workspace, root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pilot.json").write_text(pilot.model_dump_json(indent=2))

    def write_once(name: str, text: str) -> None:
        if not (folder / name).exists():
            (folder / name).write_text(text)

    before = f"{start - timedelta(days=30):%d %b %Y} to {start - timedelta(days=1):%d %b %Y}"
    rows = [
        [
            "metric",
            "label",
            "what_to_record",
            "baseline_value",
            "same_period_last_year",
            "source",
            "notes",
        ]
    ]
    rows += [[key, name, f"{how} ({before})", "", "", "", ""] for key, name, _, how in METRICS]
    with_csv = [",".join(f'"{cell}"' if "," in cell else cell for cell in row) for row in rows]
    write_once("baseline.csv", "\n".join(with_csv) + "\n")

    header = ["week", "week_start", *(key for key, *_ in METRICS), "other_activity", "notes"]
    weekly = [header] + [
        [str(week), pilot.week_start(week).isoformat(), *[""] * (len(header) - 2)]
        for week in range(1, WEEKS + 1)
    ]
    write_once("weekly.csv", "\n".join(",".join(row) for row in weekly) + "\n")
    write_once("plan.md", _plan(pilot, root))
    write_once("weekly-checklist.md", CHECKLIST.format(business=business, workspace=workspace))
    write_once(
        "testimonial-request.md", TESTIMONIAL_REQUEST.format(business=business, workspace=workspace)
    )
    return folder


# --- reading what the human logged ---


def _number(value: str) -> float | None:
    cleaned = (value or "").replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def read_baseline(workspace: str, root: Path = WORKSPACES_DIR) -> dict[str, dict]:
    with (pilot_dir(workspace, root) / "baseline.csv").open(newline="") as handle:
        return {
            row["metric"]: {
                "value": _number(row["baseline_value"]),
                "last_year": _number(row["same_period_last_year"]),
                "source": row["source"],
            }
            for row in csv.DictReader(handle)
        }


def read_weekly(workspace: str, root: Path = WORKSPACES_DIR) -> list[dict]:
    with (pilot_dir(workspace, root) / "weekly.csv").open(newline="") as handle:
        rows = []
        for row in csv.DictReader(handle):
            parsed = {key: _number(row.get(key, "")) for key, *_ in METRICS}
            rows.append(
                {**parsed, "week": int(row["week"]), "other_activity": row.get("other_activity", "").strip(),
                 "notes": row.get("notes", "").strip()}
            )  # fmt: skip
        return rows


# --- 4: tracker ---


def system_week(engine: Engine, pilot: Pilot, week: int) -> dict:
    """What the system recorded in one pilot week: approvals, edits, cost, strategy changes."""
    start = datetime.combine(pilot.week_start(week), datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=7)
    name = pilot.workspace

    def between(column):
        return column >= start, column < end

    with Session(engine) as session:
        decisions = session.exec(
            select(Approval.decision, func.count(Approval.id))
            .join(Draft, Draft.id == Approval.draft_id)
            .where(Draft.workspace == name, *between(Approval.created_at))
            .group_by(Approval.decision)
        ).all()
        cost = session.exec(
            select(func.sum(LLMCall.cost_usd)).where(
                LLMCall.workspace == name, *between(LLMCall.created_at)
            )
        ).one()
        rulings = session.exec(
            select(ChangeDecision).where(
                ChangeDecision.workspace == name, *between(ChangeDecision.created_at)
            )
        ).all()
        voice = session.exec(
            select(BrainVersion.note).where(
                BrainVersion.workspace == name,
                BrainVersion.note.startswith("learned from"),
                *between(BrainVersion.created_at),
            )
        ).all()
        uploaded = session.exec(
            select(func.count(PerformanceRow.id)).where(
                PerformanceRow.workspace == name, *between(PerformanceRow.uploaded_at)
            )
        ).one()
        learnings = session.exec(
            select(func.count(Learnings.id)).where(
                Learnings.workspace == name, *between(Learnings.created_at)
            )
        ).one()
    counts = dict(decisions)
    decided = sum(counts.values())
    changes = [f"{row.decision}: {json.loads(row.change_json)['change']}" for row in rulings] + [
        str(note) for note in voice
    ]
    return {
        "approved": counts.get("approved", 0),
        "edited": counts.get("edited", 0),
        "rejected": counts.get("rejected", 0),
        "edit_rate_pct": round(counts.get("edited", 0) / decided * 100) if decided else None,
        "model_cost_usd": round(cost or 0.0, 2),
        "strategy_changes": changes,
        "ritual": {
            "data_uploaded": uploaded > 0,
            "learnings_reviewed": learnings > 0,
            "content_approved": decided > 0,
        },
    }


def build_tracker(
    engine: Engine, workspace: str, root: Path = WORKSPACES_DIR, today: date | None = None
) -> str:
    """Write tracker.md and tracker.json: every week's metrics, approvals, edits, cost, changes."""
    pilot = load_pilot(workspace, root)
    today = today or datetime.now(UTC).date()
    weekly = {row["week"]: row for row in read_weekly(workspace, root)}
    weeks = []
    for week in range(1, WEEKS + 1):
        if pilot.week_start(week) > today:
            break
        logged = weekly.get(week, {})
        system = system_week(engine, pilot, week)
        system["ritual"]["time_logged"] = logged.get("hours_on_marketing") is not None
        weeks.append(
            {
                "week": week,
                "week_start": pilot.week_start(week).isoformat(),
                "logged": logged,
                **system,
            }
        )

    def cell(value) -> str:
        return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)

    lines = [
        f"# Pilot tracker: {pilot.business}",
        "",
        f"Day {min(PILOT_DAYS, max(0, (today - pilot.start).days + 1))} of {PILOT_DAYS} · "
        f"{pilot.start:%d %b %Y} to {pilot.end:%d %b %Y}",
        "",
        "## Metrics you logged",
        "",
        "| Week | " + " | ".join(name for _, name, _, _ in METRICS) + " |",
        "|---|" + "---|" * len(METRICS),
    ]
    for item in weeks:
        lines.append(
            f"| {item['week']} | "
            + " | ".join(cell(item["logged"].get(key)) for key, *_ in METRICS)
            + " |"
        )
    lines += [
        "",
        "## What the system recorded",
        "",
        "| Week | Approved | Edited | Rejected | Edit rate | Model cost | Ritual complete |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in weeks:
        done = sum(item["ritual"].values())
        rate = "" if item["edit_rate_pct"] is None else f"{item['edit_rate_pct']}%"
        lines.append(
            f"| {item['week']} | {item['approved']} | {item['edited']} | {item['rejected']} | {rate} | "
            f"${item['model_cost_usd']:.2f} | {done} of 4 steps |"
        )
    lines += ["", "## Strategy changes, week by week", ""]
    for item in weeks:
        for change in item["strategy_changes"]:
            lines.append(f"- Week {item['week']}: {change}")
    if not any(item["strategy_changes"] for item in weeks):
        lines.append("- None yet.")
    lines += ["", "## Other things that happened (possible confounders)", ""]
    noted = [
        f"- Week {item['week']}: {item['logged']['other_activity']}"
        for item in weeks
        if item["logged"].get("other_activity")
    ]
    lines += noted or [
        "- Nothing logged. If that is because nothing was written down, fix that before the report."
    ]
    missing = [str(item["week"]) for item in weeks if not item["ritual"]["time_logged"]]
    if missing:
        lines += [
            "",
            f"Hours not logged for week(s): {', '.join(missing)}. The time-saved comparison depends on them.",
        ]

    folder = pilot_dir(workspace, root)
    text = "\n".join(lines) + "\n"
    (folder / "tracker.md").write_text(text)
    (folder / "tracker.json").write_text(json.dumps(weeks, indent=2, default=str))
    return text


# --- 5: day-30 and day-60 reports ---


def _period(rows: list[dict], kind: Kind, key: str, days: int) -> tuple[float | None, int]:
    """One metric's value over some weeks, put on the same footing as its 30-day baseline."""
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return None, 0
    if kind == "stock":
        return values[-1], len(values)
    if kind == "flow":
        # Weeks are 7 days, so four weeks is 28 days: scale to 30 to match the baseline.
        return sum(values) / (7 * len(values)) * 30, len(values)
    return sum(values) / len(values), len(values)


def build_report(
    engine: Engine, workspace: str, day: Literal[30, 60], root: Path = WORKSPACES_DIR
) -> str:
    pilot = load_pilot(workspace, root)
    baseline = read_baseline(workspace, root)
    weekly = read_weekly(workspace, root)
    first, last = (1, 4) if day == 30 else (5, 8)
    rows = [row for row in weekly if first <= row["week"] <= last]
    span = f"weeks {first} to {last} (days {(first - 1) * 7 + 1} to {last * 7})"

    table, flags = [], []
    for key, name, kind, _ in METRICS:
        base = baseline.get(key, {}).get("value")
        value, weeks_logged = _period(rows, kind, key, 28)
        if base is None or value is None:
            table.append(f"| {name} | {'' if base is None else f'{base:g}'} | not recorded | | |")
            flags.append(
                f"{name}: {'no baseline' if base is None else 'not logged during the pilot'}, so no comparison is possible."
            )
            continue
        change = (value - base) / base * 100 if base else None
        note = []
        if weeks_logged < last - first + 1:
            note.append(f"only {weeks_logged} of {last - first + 1} weeks logged")
        if kind == "flow" and min(base, value) < SMALL_COUNT:
            note.append("too few to read anything into")
        last_year = baseline[key]["last_year"]
        if last_year is not None and kind in ("flow", "stock"):
            note.append(f"same period last year: {last_year:g}")
        shown = "n/a" if change is None else f"{change:+.0f}%"
        table.append(f"| {name} | {base:g} | {value:.1f} | {shown} | {'; '.join(note)} |")

    systems = [system_week(engine, pilot, week) for week in range(first, last + 1)]
    approved = sum(item["approved"] + item["edited"] for item in systems)
    edited = sum(item["edited"] for item in systems)
    rejected = sum(item["rejected"] for item in systems)
    cost = sum(item["model_cost_usd"] for item in systems)
    changes = [change for item in systems for change in item["strategy_changes"]]
    activity = [
        f"Week {row['week']}: {row['other_activity']}" for row in rows if row["other_activity"]
    ]
    hours_before = baseline.get("hours_on_marketing", {}).get("value")
    hours_now, _ = _period(rows, "hours", "hours_on_marketing", 28)
    no_last_year = all(item["last_year"] is None for item in baseline.values())

    lines = [
        f"# Day-{day} pilot report: {pilot.business}",
        "",
        f"Pilot started {pilot.start:%d %b %Y}. This report compares {span} with the 30 days before "
        "the pilot. Counts are scaled to 30 days so the two periods are comparable.",
        "",
        "## Read this first",
        "",
        "This is a before-and-after comparison of one business, not an experiment. There was no "
        "control group, so it can show that numbers moved during the pilot. It cannot show that "
        "GrowthCrew moved them.",
        "",
        "## Before and after",
        "",
        "| Metric | Baseline | Pilot period | Change | Notes |",
        "|---|---|---|---|---|",
        *table,
        "",
    ]
    if flags:
        lines += ["Gaps in the data:", "", *(f"- {flag}" for flag in flags), ""]
    lines += [
        "## What the owner's time went on",
        "",
        f"- Hours on marketing per week: {'not recorded' if hours_before is None else f'{hours_before:g}'} before, "
        f"{'not logged' if hours_now is None else f'{hours_now:.1f}'} during this period. This is self-reported.",
        f"- Drafts approved: {approved} ({edited} needed edits), rejected: {rejected}.",
        f"- Model cost: ${cost:.2f}"
        + (f", or ${cost / approved:.2f} per approved piece." if approved else "."),
        "",
        "## What changed in the strategy",
        "",
        *(
            [f"- {change}" for change in changes]
            or ["- No changes were accepted or rejected in this period."]
        ),
        "",
        "## Why these numbers may not mean what they seem to",
        "",
        "- **No control group.** We do not know what these 30 days would have looked like without "
        "the pilot.",
        "- **Seasonality.** "
        + (
            "No same-period figures from last year were recorded, so a seasonal rise or dip cannot be ruled out."
            if no_last_year
            else "Last year's figures for the same period are in the table; compare the change with them before crediting the pilot."
        ),
        "- **Other activity at the same time.** "
        + (
            "The owner logged: "
            + "; ".join(activity)
            + ". Any of these could explain part of the change."
            if activity
            else "Nothing else was logged. That is rarely true of a real business; ask the owner before relying on it."
        ),
        "- **More attention.** The owner looked at marketing every week, perhaps for the first time. "
        "Some of any improvement comes from that attention, whatever tool was used.",
        "- **Small numbers.** Metrics marked as too few (under "
        f"{SMALL_COUNT} in a period) swing by chance; a change from 4 leads to 7 is not +75% growth.",
        "- **Delayed effects.** Search traffic and followers build slowly; "
        + (
            "30 days is too soon to judge them."
            if day == 30
            else "even 60 days undercounts content published late in the pilot."
        ),
        "- **Self-reported and platform-reported data.** Hours are an estimate, and each platform "
        "defines impressions and engagement its own way.",
        "",
        "## What we can say",
        "",
        "_Write two or three sentences here yourself, after talking to the owner. Say what moved, "
        "what did not, what you cannot attribute, and whether the owner would continue._",
        "",
    ]
    text = "\n".join(lines)
    (pilot_dir(workspace, root) / f"report-day{day}.md").write_text(text)
    return text


# --- 6: testimonial, with permission ---


def record_testimonial(
    workspace: str, testimonial: Testimonial, root: Path = WORKSPACES_DIR
) -> Path:
    unknown = [use for use in testimonial.allowed_uses if use not in USES]
    if unknown:
        raise ValueError(f"Unknown use(s): {unknown}. Choose from: {', '.join(USES)}")
    if not testimonial.quote.strip():
        raise ValueError("The quote is empty")
    if not testimonial.wording_confirmed_by_owner:
        raise ValueError("Not saved: the owner has not confirmed this exact wording")
    if not testimonial.allowed_uses:
        raise ValueError("Not saved: the owner has not allowed any use of the quote")
    path = pilot_dir(workspace, root) / "testimonial.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(testimonial.model_dump_json(indent=2))
    return path


def testimonial_for(workspace: str, use: str, root: Path = WORKSPACES_DIR) -> str | None:
    """The testimonial, credited as the owner asked, if they allowed this use. Otherwise None."""
    path = pilot_dir(workspace, root) / "testimonial.json"
    if not path.exists():
        return None
    item = Testimonial.model_validate_json(path.read_text())
    if use not in item.allowed_uses:
        return None
    credit = {
        "full_name": ", ".join(part for part in (item.name, item.role, item.business) if part),
        "first_name_only": item.name.split()[0] if item.name else "A customer",
        "anonymous": f"Owner of a {item.business}" if item.business else "A customer",
    }[item.attribution]
    return f"“{item.quote.strip()}” ({credit})"
