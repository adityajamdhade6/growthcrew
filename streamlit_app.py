"""Read-only Streamlit demo of GrowthCrew, for Streamlit Community Cloud.

    uv run streamlit run streamlit_app.py

It loads the sample brand from evals/demo_seed.py into a temporary database and shows what the
full app shows. Everything on screen is invented sample data; nothing here calls a model, and
there are no approve or publish actions. The full app is the Next.js + FastAPI one in web/.
"""

import math
import os
import tempfile
from datetime import date

import altair as alt
import streamlit as st

AUTHOR = "Aditya Jamdhade"
REPO = "https://github.com/adityajamdhade6/growthcrew"
CASE_STUDY = f"{REPO}/blob/main/docs/case_study.md"

# Chart colours: one hue. The winner takes the full colour; the rest a lighter step of it.
SERIES = "#2a78d6"
SERIES_QUIET = "#9cc0ec"

st.set_page_config(page_title="GrowthCrew demo", page_icon="📈", layout="centered")


@st.cache_resource
def load_demo():
    """Seed the sample workspace once per server process, in a scratch folder."""
    folder = tempfile.mkdtemp(prefix="growthcrew-demo-")
    os.environ["DATABASE_URL"] = f"sqlite:///{folder}/demo.db"
    os.environ.setdefault("GROWTHCREW_SECRET", "streamlit-demo")
    os.chdir(folder)  # workspaces/ and other relative paths resolve here

    from evals.demo_seed import WORKSPACE, main

    from growthcrew.db.session import get_engine

    main()
    return get_engine(), WORKSPACE


@st.cache_data
def scorecard():
    """The offline evals, run once when the demo starts."""
    from evals.run import run

    results, _ = run(live=False, limit=None)
    return [result.model_dump() for result in results], date.today().isoformat()


engine, WORKSPACE = load_demo()

from sqlmodel import Session, select  # noqa: E402

from growthcrew.agents.orchestrator import timeline  # noqa: E402
from growthcrew.agents.strategist import load_latest_strategy  # noqa: E402
from growthcrew.analytics.analysis import analyze  # noqa: E402
from growthcrew.api.ui import board, review  # noqa: E402
from growthcrew.brain.store import load_brain  # noqa: E402
from growthcrew.db.models import Cycle  # noqa: E402
from growthcrew.memory import playbook  # noqa: E402
from growthcrew.naming import display, piece_name, plural  # noqa: E402
from growthcrew.reports.learning_log import learning_log  # noqa: E402

TEAM = {
    "research": "Researcher",
    "strategist": "Strategist",
    "content": "Writer",
    "critic": "Editor",
}
STAGES = {
    "research": "Research refresh",
    "strategy_check": "Strategy check",
    "content_plan": "Content plan",
    "drafting": "Drafting",
    "critic": "Editor's quality gate",
}
STATUS_BADGE = {
    "pending_approval": ("Needs approval", "orange"),
    "blocked": ("Blocked by a guardrail", "red"),
    "scheduled": ("Approved and scheduled", "green"),
    "approved": ("Approved", "green"),
    "rejected": ("Rejected", "gray"),
}
DECISIONS = {
    "accepted": ("Accepted", "green"),
    "accepted_partial": ("Accepted in part", "orange"),
    "held": ("Held for the owner", "red"),
    "rejected": ("Rejected", "red"),
    "pending": ("Awaiting a ruling", "gray"),
}


def evidence_name(ref: str) -> str:
    kind, _, rest = ref.partition(":")
    if kind == "brain":
        return f"Brand brain: {rest.split('.')[-1].replace('_', ' ')}"
    return {"learning": f"Research learning {rest}", "claim": f"Research finding {rest}",
            "voc": "Customer quotes"}.get(kind, ref)  # fmt: skip


def chance(probability: float) -> str:
    """A probability as text, without claiming certainty the data cannot give."""
    if probability > 0.999:
        return "Over 99.9%"
    return f"{probability:.0%}"


def day(value: str) -> str:
    return f"{date.fromisoformat(value):%-d %b %Y}"


def bar_chart(rows: list[dict], x_title: str, step: float = 0.5, quiet: bool = False) -> alt.Chart:
    """Horizontal bars for one measure: value labels on the bars, ticks every `step`.

    `rows` have label, value, text and highlight. Highlighted bars take the full colour.
    """
    top = max(row["value"] for row in rows)
    limit = math.ceil(top * 1.35 / step) * step
    ticks = [round(index * step, 2) for index in range(int(limit / step) + 1)]
    base = alt.Chart(alt.Data(values=rows)).encode(
        y=alt.Y("label:N", sort=None, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("value:Q", title=x_title, scale=alt.Scale(domain=[0, limit]),
                axis=alt.Axis(values=ticks, format=".1f", grid=True)),
        tooltip=[alt.Tooltip("label:N", title="Variant"), alt.Tooltip("text:N", title="Result")],
    )  # fmt: skip
    colour = alt.value("#b9bfc9") if quiet else alt.condition(
        "datum.highlight", alt.value(SERIES), alt.value(SERIES_QUIET)
    )  # fmt: skip
    bars = base.mark_bar(cornerRadiusEnd=4, size=20).encode(color=colour)
    labels = base.mark_text(align="left", dx=6, fontSize=12).encode(text="text:N")
    return (bars + labels).properties(height=44 * len(rows) + 30)


st.title("GrowthCrew")
st.markdown(
    "An AI marketing team for small businesses, where a human approves every output.  \n"
    f"Built by **{AUTHOR}** · [GitHub repo]({REPO}) · [Case study]({CASE_STUDY})"
)
st.caption(
    "Sample data, read-only. The brand (Loomhouse) and every number here are invented to show "
    "the product; it has not yet run for a real business."
)

tabs = st.tabs(
    [
        "Mission control",
        "Calendar",
        "Strategy",
        "Results",
        "Playbook",
        "Brand brain",
        "How it works",
        "Evals",
    ]
)
mission, calendar, strategy_tab, results, playbook_tab, brain_tab, how_tab, evals_tab = tabs

# --- Mission control ---

with mission:
    with Session(engine) as session:
        cycle = session.exec(
            select(Cycle).where(Cycle.workspace == WORKSPACE).order_by(Cycle.id.desc())
        ).first()
    data = timeline(engine, cycle.id)
    waiting, blocked = len(data["awaiting_approval"]), len(data["blocked_by_guardrails"])

    with st.container(border=True):
        st.markdown(f"#### {plural(waiting, 'draft')} waiting for the owner's approval")
        st.write(
            "The team has finished its part of this week's cycle. Nothing is scheduled or "
            f"published until a person approves it. {plural(blocked, 'more draft')} "
            f"{'was' if blocked == 1 else 'were'} blocked by a guardrail."
        )
    cost, budget = st.columns(2)
    cost.metric("Model cost so far this cycle", f"${data['live_cost_usd']:.2f}")
    budget.metric("Weekly budget", f"${data['weekly_limit_usd']:.0f}")
    budget.progress(
        min(1.0, data["weekly_spend_usd"] / data["weekly_limit_usd"]),
        text=f"${data['weekly_spend_usd']:.2f} spent. The team stops if the limit is reached.",
    )

    st.subheader("The team")
    runs = [run for step in data["steps"] for run in step["agents"]]
    for column, (agent, name) in zip(st.columns(4), TEAM.items(), strict=True):
        mine = [run for run in runs if run["agent"] == agent]
        with column, st.container(border=True):
            st.markdown(f"**{name}**")
            st.badge("Done", color="green")
            st.caption(f"{plural(sum(r['llm_calls'] for r in mine), 'model call')} · "
                       f"${sum(r['cost_usd'] for r in mine):.2f}")  # fmt: skip

    st.subheader("This week, step by step")
    for step in data["steps"]:
        with st.container(border=True):
            head, state = st.columns([4, 1])
            head.markdown(f"**{STAGES.get(step['stage'], display(step['stage']))}**")
            state.badge(
                display(step["status"]), color="green" if step["status"] == "done" else "gray"
            )
            st.write(step["detail"])
            for run in step["agents"]:
                if run["llm_calls"]:
                    tokens = run["input_tokens"] + run["output_tokens"]
                    st.caption(f"{TEAM.get(run['agent'], display(run['agent']))}: "
                               f"{plural(run['llm_calls'], 'model call')}, {plural(tokens, 'token')}, "
                               f"${run['cost_usd']:.2f}")  # fmt: skip
    with st.container(border=True):
        head, state = st.columns([4, 1])
        head.markdown("**Human approval**")
        state.badge("Waiting on the owner", color="orange")
        st.write(
            "Then scheduled, published by the owner, and measured. None of these happen on their own."
        )

# --- Calendar and review ---

with calendar:
    drafts = [
        d
        for d in board(WORKSPACE, engine)["drafts"]
        if d["status"] not in ("published", "measured")
    ]
    drafts.sort(key=lambda d: (d["date"], d["id"]))
    names = {d["id"]: piece_name(d["piece"]) for d in drafts}
    captions = [
        f"{date.fromisoformat(d['date']):%a %d %b} · {STATUS_BADGE.get(d['status'], (display(d['status']),))[0]}"
        for d in drafts
    ]
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.subheader("This week's drafts")
        first = next((i for i, d in enumerate(drafts) if d["status"] == "pending_approval"), 0)
        chosen = st.radio("Draft", list(names), index=first, format_func=names.get,
                          captions=captions, label_visibility="collapsed")  # fmt: skip
    with right:
        item = review(chosen, engine)
        status = item["calendar"]["status"] if item["calendar"] else item["status"]
        text, colour = STATUS_BADGE.get(status, (display(status), "gray"))
        st.subheader(names[chosen])
        st.badge(text, color=colour)
        if item["violations"]:
            st.error(
                "This draft cannot be approved as written. "
                + " ".join(f"Line {v['line']}: {v['reason'].lower()} (“{v['excerpt']}”)."
                           for v in item["violations"])
            )  # fmt: skip
        with st.container(border=True):
            st.markdown(item["text"].replace("\n", "  \n"))
        if item["first_draft"]:
            with st.expander("See the first draft, before the editor's changes"):
                st.markdown(item["first_draft"].replace("\n", "  \n"))
        if item["scores"]:
            st.markdown("**Editor's scores** · 8 or more on every line passes")
            columns = st.columns(2)
            for index, (name, score) in enumerate(item["scores"].items()):
                columns[index % 2].progress(score / 10, text=f"{display(name)} · {score}/10")
        meta = item["metadata"]
        with st.expander("Why this piece exists", expanded=True):
            st.markdown(
                f"**Messaging pillar:** {meta.get('messaging_pillar', '')}  \n"
                f"**Written for:** {meta.get('target_persona', '')}  \n"
                f"**Call to action:** {meta.get('cta', '')}  \n"
                f"**Hypothesis it tests:** {meta.get('hypothesis', '')}"
            )
        memory = item.get("memory") or {}
        if memory.get("examples") or memory.get("rules"):
            winners, held = memory.get("examples", []), memory.get("rules", [])
            label = f"Written with {plural(len(winners), 'past winner')} and {plural(len(held), 'playbook rule')}"
            with st.expander(label):
                for rule in held:
                    st.markdown(f"- **Rule:** {rule}")
                for example in winners:
                    first_line = example["text"].splitlines()[0]
                    st.markdown(
                        f"- **Past winner** ({example['published_on']}, {example['rate_pct']}% "
                        f"{example['metric']}, {example['score']:.1f}x the brand average): “{first_line}”"
                    )
        reject, edit, approve = st.columns(3)
        reject.button("Reject", disabled=True, width="stretch")
        edit.button("Edit", disabled=True, width="stretch")
        approve.button("Approve", disabled=True, type="primary", width="stretch")
        st.caption("These work in the full app. This demo is read-only.")

# --- Strategy ---

with strategy_tab:
    doc = load_latest_strategy(WORKSPACE)
    evidence = {item.id: item for item in doc.evidence}

    def support(refs: list[str]) -> str:
        if not refs:
            return ":red-badge[No supporting evidence]"
        return " ".join(
            f":{'orange' if evidence[ref].quality.startswith('inferred') else 'gray'}-badge[{evidence_name(ref)}"
            f"{' · unconfirmed' if evidence[ref].quality.startswith('inferred') else ''}]"
            for ref in refs
        )

    st.subheader("Positioning")
    st.markdown(f"#### {doc.positioning.positioning_statement}")

    st.subheader("Messaging house")
    st.success(f"**Core message:** {doc.messaging_house.core_message.text}")
    for pillar in doc.messaging_house.pillars:
        with st.container(border=True):
            st.markdown(f"**{pillar.message}**")
            for point in pillar.proof_points:
                st.markdown(f"{point.text}  \n{support(point.support)}")

    st.subheader("90-day channel plan")
    spans = {
        "days 1-30": (1, 30),
        "days 31-60": (31, 60),
        "days 61-90": (61, 90),
        "all 90 days": (1, 90),
    }
    plays = [
        {"channel": play.channel, "start": spans[play.timing][0], "end": spans[play.timing][1],
         "label": f"{play.budget_pct}% of budget · {display(stage.stage)}",
         "tactic": play.tactic, "stage": display(stage.stage), "budget": f"{play.budget_pct}%"}
        for stage in doc.channel_plan.stages for play in stage.channels
    ]  # fmt: skip
    if not plays:
        st.warning("The strategy has no channel plan yet.")
    else:
        plan = alt.Chart(alt.Data(values=plays)).encode(
            y=alt.Y("channel:N", sort=None, title=None, axis=alt.Axis(labelLimit=160)),
            x=alt.X("start:Q", title="Day of the plan", scale=alt.Scale(domain=[1, 90]),
                    axis=alt.Axis(values=[1, 30, 60, 90], grid=True)),
            tooltip=[alt.Tooltip("channel:N", title="Channel"), alt.Tooltip("tactic:N", title="Tactic"),
                     alt.Tooltip("stage:N", title="Funnel stage"), alt.Tooltip("budget:N", title="Budget")],
        )  # fmt: skip
        bars = plan.mark_bar(cornerRadius=4, size=24, color=SERIES).encode(x2="end:Q")
        labels = plan.mark_text(align="left", dx=6, color="white", fontSize=12).encode(
            text="label:N"
        )
        st.altair_chart((bars + labels).properties(height=52 * len(plays) + 30), width="stretch")
        for play in plays:
            st.markdown(
                f"- **{play['channel']}** ({play['stage'].lower()}, {play['budget']}): {play['tactic']}"
            )

    st.subheader("Experiments, ranked by ICE")
    for number, experiment in enumerate(doc.experiments, 1):
        with st.expander(f"{number}. {experiment.name} · ICE {experiment.ice}"):
            st.write(experiment.hypothesis)
            st.markdown(
                f"**Metric:** {experiment.metric}  \n**Minimum sample:** {experiment.minimum_sample}  \n"
                f"**Decision rule:** {experiment.decision_rule}  \n{support(experiment.support)}"
            )

    st.subheader("Devil's advocate review")
    st.write(doc.critique.summary)
    for change in doc.revision.changes:
        st.markdown(
            f"- **{display(change.decision)}:** {change.critique_issue}. _{change.change_made}_"
        )

# --- Results ---

with results:
    analysis = analyze(engine, WORKSPACE)
    readouts = analysis.readouts
    st.caption(
        f"Week of {day(analysis.window_start)} to {day(analysis.window_end)} · "
        f"{plural(analysis.rows_used, 'row')} of uploaded data matched to a piece"
    )

    st.subheader("Click-through rate by angle")
    winners = {
        r.winner for r in readouts if r.status == "significant" and r.metric == "click-through rate"
    }
    angles = sorted(
        (
            row
            for row in analysis.performance
            if row.dimension == "angle" and row.metric == "click-through rate"
        ),
        key=lambda row: -row.rate_pct,
    )
    st.altair_chart(
        bar_chart(
            [{"label": display(row.value), "value": row.rate_pct, "highlight": row.value in winners,
              "text": f"{row.rate_pct:.2f}%" + (" · won its test" if row.value in winners else "")}
             for row in angles],
            "Click-through rate (%)",
        ),
        width="stretch",
    )  # fmt: skip
    st.caption(
        "All pieces pooled, so this is descriptive. The tests below say whether a gap is real."
    )

    st.subheader("Experiment results")
    st.caption(
        "Bayesian readouts. A winner is called only for a test that was registered before it ran, "
        "once its planned sample is in, when the leader is very likely the best and choosing it "
        "risks almost nothing."
    )
    for readout in readouts:
        u = readout.uncertainty
        leader = display(u.leader)
        unit = {"click-through rate": "impression", "conversion rate": "session"}.get(
            readout.metric, "send"
        )
        kind = "A/B test" if readout.kind == "ab_test" else "Comparison across different pieces"
        with st.container(border=True):
            st.markdown(f"**{readout.title}** · {kind} · {readout.metric}")
            if readout.status == "significant" and readout.guardrail_flags:
                st.badge(f"{leader} wins, but held: it hurts a guardrail", color="red")
            elif readout.status == "significant":
                st.badge(f"{leader} wins", color="green")
            elif readout.status == "not_enough_data":
                st.badge("Keep running", color="orange")
            elif readout.status == "not_preregistered":
                st.badge("Descriptive only · not pre-registered", color="blue")
            else:
                st.badge("No clear difference", color="gray")

            zero = " (includes zero)" if u.lift_low_pct < 0 < u.lift_high_pct else ""
            st.markdown(
                f"{chance(u.prob_best[u.leader])} probability that {leader.lower()} is the best variant · "
                f"lift over the runner-up {readout.lift_pct:+.0f}% "
                f"(95% interval {u.lift_low_pct:+.0f}% to {u.lift_high_pct:+.0f}%{zero}) · "
                f"sample: {plural(u.sample_size, unit)}"
            )
            if readout.status == "not_enough_data":
                st.markdown(f"About **{plural(readout.more_needed, 'more ' + unit)}** needed before a call. "
                            f"Planned: {readout.planned_per_variant:,} per variant.")  # fmt: skip
            for flag in readout.guardrail_flags:
                st.error(f"Guardrail: {flag}")

            st.altair_chart(
                bar_chart(
                    [{"label": display(arm["label"]), "value": arm["rate_pct"],
                      "highlight": arm["label"] == readout.winner,
                      "text": f"{arm['rate_pct']:.2f}%  ({arm['successes']:,} of {arm['trials']:,})"}
                     for arm in readout.arms],
                    f"{display(readout.metric)} (%)",
                    step=0.5 if max(a["rate_pct"] for a in readout.arms) < 5 else 2.5,
                    quiet=readout.status != "significant",
                ),
                width="stretch",
            )  # fmt: skip
            st.dataframe(
                [{"Variant": display(arm["label"]),
                  "Probability it is best": chance(u.prob_best[arm["label"]]) if u.prob_best[arm["label"]] >= 0.001 else "Under 0.1%",
                  "Expected loss if chosen": f"{u.expected_loss_pct[arm['label']]:.1f}% of the rate"}
                 for arm in readout.arms],
                hide_index=True, width="stretch",
            )  # fmt: skip
            if readout.next_split:
                split = " · ".join(
                    f"{display(label)} {share:.0%}" for label, share in readout.next_split.items()
                )
                st.markdown(
                    f"**Next week's budget split** (Thompson sampling, 10% floor while undecided): {split}"
                )
            if readout.preregistered:
                with st.expander("What was pre-registered"):
                    st.markdown(
                        f"**Hypothesis:** {readout.hypothesis}  \n"
                        f"**Primary metric:** {readout.metric}  \n"
                        f"**Planned sample:** {readout.planned_per_variant:,} per variant  \n"
                        f"**Guardrails:** {', '.join(readout.guardrails_checked) or 'none'}"
                    )
            else:
                st.caption(readout.note)

    st.subheader("Learning log")
    st.caption("Each week the analyst proposes changes and the strategist rules on each. A confident "
               "test winner is accepted by default; a stated risk makes it a partial shift.")  # fmt: skip
    for entry in learning_log(engine, WORKSPACE):
        if entry["kind"] != "learnings":
            continue
        start, _, end = entry["window"].partition(" to ")
        st.markdown(f"**Week of {day(start)} to {day(end)}**")
        for change in entry["changes"]:
            text, colour = DECISIONS[change["decision"]]
            with st.container(border=True):
                st.badge(text, color=colour)
                st.markdown(f"**{change['change']}**")
                st.write(change["reason"] or change["rationale"])

# --- Playbook ---

with playbook_tab:
    book = playbook.summary(engine, WORKSPACE)
    learned = book["learned"]
    st.subheader("What this brand has learned")
    st.caption(
        f"From {plural(book['pieces_remembered'], 'published piece')} and their results, as of "
        f"{day(book['as_of'])}. Patterns are found by comparing pieces that happened to differ, so "
        "they are leads, not proof: a rule must hold on two weekly runs to become active, and one "
        "miss takes it out of use."
    )
    found, active, retired = st.columns(3)
    found.metric(f"Patterns found in {learned['days']} days", learned["found"])
    active.metric("Still holding", learned["still_active"])
    retired.metric("Retired", learned["retired"])

    def show_rules(title: str, rows: list[dict], colour: str, empty: str) -> None:
        st.subheader(title)
        if not rows:
            st.caption(empty)
        for rule in rows:
            with st.container(border=True):
                st.badge(display(rule["status"]), color=colour)
                st.markdown(f"**{rule['statement']}**")
                sure = "over 99%" if rule["probability"] > 0.99 else f"{rule['probability']:.0%}"
                st.markdown(
                    f"+{abs(rule['lift_pct']):.0f}% · {rule['pieces_with']} pieces with it, "
                    f"{rule['pieces_without']} without · {sure} probability it is real · "
                    f"found {day(rule['found_on'][:10])}"
                )
                with st.expander(f"History ({plural(len(rule['history']), 'weekly run')})"):
                    st.dataframe(
                        [{"Week of": day(event["at"][:10]), "Status": display(event["status"]),
                          "Lift": f"{event['lift_pct']:+.0f}%", "Probability": f"{event['probability']:.0%}",
                          "Note": event["note"]}
                         for event in rule["history"]],
                        hide_index=True, width="stretch",
                    )  # fmt: skip

    show_rules("Active rules", book["active"], "green",
               "No rule is holding yet. The writer uses rules only while they are active.")  # fmt: skip
    show_rules(
        "Out of use, being watched", book["weakening"] + book["candidate"], "orange", "None."
    )
    show_rules("Retired", book["retired"], "gray", "None.")

    st.subheader("Performance by prompt and strategy version")
    st.caption("Every agent prompt and strategy has a version, so a change can be judged by what followed it. "
               "This is a before-and-after comparison, not a test.")  # fmt: skip
    st.dataframe(
        [{"Writer prompt": row["prompt_version"], "Strategy": f"v{row['strategy_version']}",
          "Content type": display(row["content_type"]), "Pieces": row["pieces"],
          "Rate": f"{row['rate_pct']:.2f}%"}
         for row in book["by_version"]],
        hide_index=True, width="stretch",
    )  # fmt: skip

# --- Brand brain ---

with brain_tab:
    brain = load_brain(WORKSPACE)
    confirmed = sum(meta.status == "confirmed" for meta in brain.fields.values())
    st.progress(confirmed / len(brain.fields),
                text=f"{confirmed} of {len(brain.fields)} fields confirmed by the owner")  # fmt: skip
    with st.container(border=True):
        st.markdown(
            "**What agents do differently with an inferred fact**\n"
            "- They treat it as a working assumption and never state it as fact in customer-facing copy.\n"
            "- A recommendation resting on a low-confidence one must say so, and it shows as "
            "“unconfirmed” in the strategy.\n"
            "- Empty fields stay unknown; agents do not fill them in.\n"
            "- The owner confirms or corrects a field in one tap, and the change is saved as a new version."
        )

    def show(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(
                "- "
                + (
                    v
                    if isinstance(v, str)
                    else " · ".join(str(x) for x in v.model_dump().values() if x)
                )
                for v in value
            )
        return "  \n".join(
            f"{display(key)}: {', '.join(val) if isinstance(val, list) else val}"
            for key, val in value.model_dump().items()
        )

    groups = {"Business": ("business", "products"), "ICP": ("icp",), "Voice": ("voice",),
              "Proof": ("proof",), "Competitors": ("competitors",)}  # fmt: skip
    for title, prefixes in groups.items():
        st.subheader(title)
        for path, meta in brain.fields.items():
            if path.split(".")[0] not in prefixes:
                continue
            with st.container(border=True):
                name, badge = st.columns([3, 2])
                name.markdown(f"**{display(path.split('.')[-1])}**")
                if meta.status == "confirmed":
                    badge.badge("Confirmed", color="green")
                else:
                    badge.badge(f"Inferred · {meta.confidence} confidence", color="orange")
                st.markdown(show(brain.get(path)) or "_Not found on the website_")
                if meta.status != "confirmed":
                    sources = ", ".join(
                        f"[{url.split('//')[-1]}]({url})" for url in meta.source_urls
                    )
                    st.caption(f"Source: {sources or 'none recorded'}. {meta.note}")

# --- How it works ---

with how_tab:
    st.subheader("Five agents, one approval gate, one feedback loop")
    st.graphviz_chart(
        """
        digraph {
          rankdir=TB; bgcolor="transparent";
          node [shape=box, style="rounded,filled", fillcolor="#eef3fb", color="#9cb4d8", fontname="Helvetica", fontsize=11];
          edge [color="#7d8796", fontname="Helvetica", fontsize=9, fontcolor="#5b6577"];
          site [label="Website + questionnaire", fillcolor="#f4f5f7"];
          brain [label="Brand brain\\nevery field inferred or confirmed", shape=cylinder, fillcolor="#f4f5f7"];
          research [label="Researcher\\nsearch, read, reviews"];
          strategy [label="Strategist\\n5 frameworks + devil's advocate"];
          writer [label="Writer\\n7 content types"];
          editor [label="Editor\\nscores + line edits, max 3 rounds"];
          guard [label="Guardrails", shape=diamond, fillcolor="#fdf3d8", color="#d4b45a"];
          human [label="Owner approves,\\nedits or rejects", fillcolor="#e3f4ea", color="#7cc19a", penwidth=2];
          live [label="Scheduled, then\\npublished by the owner"];
          analyst [label="Analyst\\nsignificance tests in code"];
          site -> brain -> research;
          research -> strategy [label="cited claims only"];
          strategy -> writer -> editor -> guard;
          editor -> writer [label="edits"];
          guard -> writer [label="blocked"];
          guard -> human -> live -> analyst;
          human -> brain [label="recurring edits become voice rules", constraint=false];
          analyst -> strategy [label="3 proposed changes a week", constraint=false, color="#2a78d6", penwidth=2];
        }
        """
    )
    st.markdown(
        "**The feedback loop** is the blue arrow. Results come back, the analyst proposes three "
        "changes, the strategist rules on each with a reason, and accepted changes alter next "
        "week's content plan."
    )
    st.subheader("What it refuses to do")
    st.markdown(
        "| It will not | How that is enforced |\n|---|---|\n"
        "| Publish anything | Every draft waits for a named person's approval. |\n"
        "| Use an invented statistic or testimonial | Anything not in the brand's own proof blocks the draft. |\n"
        "| Make an uncited market claim | Claims whose source the agent never read are deleted. |\n"
        "| Call a winner early or on a cherry-picked metric | Tests are pre-registered; nothing is called "
        "before the planned sample, or on any metric but the registered one. |\n"
        "| Accept a winner that hurts a guardrail | It is held for a person to decide. |\n"
        "| Turn down a confident winner on a hunch | A significant A/B winner is accepted unless a specific risk is stated. |\n"
        "| Overspend | A weekly cap checked before every model request. |"
    )
    st.markdown(f"More in the [architecture notes]({REPO}/blob/main/docs/architecture.md).")

# --- Evals ---

with evals_tab:
    rows, ran_on = scorecard()
    labels = {"pass": ("Pass", "green"), "fail": ("Fail", "red"),
              "pending": ("Waiting on a human", "orange"), "skipped": ("Needs the live model", "gray")}  # fmt: skip
    passed = sum(row["status"] == "pass" for row in rows)
    st.subheader("Eval scorecard")
    st.caption(f"The offline suite, run when this demo started ({ran_on}). {passed} of {len(rows)} evals "
               "can pass without a model or a human; the rest say what they are waiting for.")  # fmt: skip
    for row in rows:
        text, colour = labels[row["status"]]
        with st.container(border=True):
            name, state = st.columns([3, 2])
            name.markdown(f"**{display(row['name'])}** · {display(row['agent'], capital=False)}")
            state.badge(text, color=colour)
            if row["metrics"]:
                st.dataframe(
                    [{"Metric": display(key), "Value": str(value), "Threshold": row["thresholds"].get(key, "")}
                     for key, value in row["metrics"].items()],
                    hide_index=True, width="stretch",
                )  # fmt: skip
            for note in row["notes"]:
                st.caption(note)
