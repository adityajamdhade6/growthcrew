"""Read-only Streamlit demo of GrowthCrew, for Streamlit Community Cloud.

    uv run streamlit run streamlit_app.py

It loads the sample brand from evals/demo_seed.py into a temporary database and shows what the
full app shows. Everything on screen is invented sample data; nothing here calls a model, and
there are no approve or publish actions. The full app is the Next.js + FastAPI one in web/.
"""

import json
import os
import tempfile

import streamlit as st

st.set_page_config(page_title="GrowthCrew demo", page_icon="📈", layout="wide")


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


engine, WORKSPACE = load_demo()

from sqlmodel import Session, select  # noqa: E402

from growthcrew.agents.orchestrator import timeline  # noqa: E402
from growthcrew.agents.strategist import load_latest_strategy  # noqa: E402
from growthcrew.analytics.analysis import analyze  # noqa: E402
from growthcrew.api.ui import board, review  # noqa: E402
from growthcrew.brain.store import load_brain  # noqa: E402
from growthcrew.db.models import Cycle  # noqa: E402
from growthcrew.reports.learning_log import learning_log  # noqa: E402


def nice(value: str) -> str:
    names = {"linkedin_post": "LinkedIn post", "landing_hero": "Landing page hero",
             "pending_approval": "Needs approval", "ai_cliche": "No clichés"}  # fmt: skip
    return names.get(value, value.replace("_", " ").capitalize())


st.title("GrowthCrew")
st.caption("An AI marketing team for small businesses, where a human approves every output.")
st.info(
    "**Sample data, read-only.** The brand (Loomhouse), its drafts, scores, costs and results "
    "are invented to show the product. The system has not yet run for a real business. "
    "The full app with approve, edit and reject is in the "
    "[repo](https://github.com/adityajamdhade6/growthcrew)."
)

mission, calendar, strategy_tab, results, brain_tab = st.tabs(
    ["Mission control", "Calendar and review", "Strategy", "Results and learnings", "Brand brain"]
)

with mission:
    with Session(engine) as session:
        cycle = session.exec(
            select(Cycle).where(Cycle.workspace == WORKSPACE).order_by(Cycle.id.desc())
        ).first()
    data = timeline(engine, cycle.id)
    waiting = len(data["awaiting_approval"])
    a, b, c = st.columns(3)
    a.metric("Cost so far this cycle", f"${data['live_cost_usd']:.2f}")
    b.metric("Weekly budget", f"${data['weekly_limit_usd']:.0f}",
             f"${data['weekly_spend_usd']:.2f} spent", delta_color="off")  # fmt: skip
    c.metric("Drafts waiting for approval", waiting)
    st.warning(
        f"{waiting} drafts are waiting for a human. Nothing is scheduled or published until "
        f"they are approved. {len(data['blocked_by_guardrails'])} more blocked by a guardrail."
    )
    st.subheader("What each agent did")
    for step in data["steps"]:
        runs = [run for run in step["agents"] if run["llm_calls"]]
        cost = sum(run["cost_usd"] for run in runs)
        with st.container(border=True):
            st.markdown(f"**{nice(step['stage'])}** · {step['status']} · ${cost:.2f}")
            st.write(step["detail"])
            for run in runs:
                tokens = run["input_tokens"] + run["output_tokens"]
                st.caption(f"{nice(run['agent'])}: {run['llm_calls']} model calls, "
                           f"{tokens:,} tokens, ${run['cost_usd']:.2f}")  # fmt: skip
    st.caption("Next stages need a person: awaiting approval → scheduled → published → measured.")

with calendar:
    drafts = [d for d in board(WORKSPACE, engine)["drafts"] if d["status"] != "published"]
    left, right = st.columns([1, 2])
    with left:
        st.subheader("This week")
        labels = {
            d["id"]: f"{d['date'][5:]} · {nice(d['content_type'])}"
            + (f" · {nice(d['angle'])}" if d["angle"] else "")
            + f" · {nice(d['status'])}"
            for d in drafts
        }
        chosen = st.radio(
            "Draft", list(labels), format_func=labels.get, label_visibility="collapsed"
        )
    with right:
        item = review(chosen, engine)
        st.subheader(
            nice(item["content_type"])
            + (f" · {nice(item['angle'])} angle" if item["angle"] else "")
        )
        if item["violations"]:
            st.error("Blocked by a guardrail; it cannot be approved as written. "
                     + "; ".join(f"line {v['line']}: {v['reason']} (“{v['excerpt']}”)"
                                 for v in item["violations"]))  # fmt: skip
        st.code(item["text"], language=None, wrap_lines=True)
        if item["first_draft"]:
            with st.expander("First draft, before the editor"):
                st.code(item["first_draft"], language=None, wrap_lines=True)
        if item["scores"]:
            st.markdown("**Editor's scores** (8 or more passes)")
            columns = st.columns(3)
            for index, (name, score) in enumerate(item["scores"].items()):
                columns[index % 3].progress(score / 10, text=f"{nice(name)}: {score}/10")
        meta = item["metadata"]
        st.markdown(
            f"- **Serves pillar:** {meta.get('messaging_pillar', '')}\n"
            f"- **Written for:** {meta.get('target_persona', '')}\n"
            f"- **Call to action:** {meta.get('cta', '')}\n"
            f"- **Tests the hypothesis:** {meta.get('hypothesis', '')}"
        )
        st.caption(
            "In the full app this panel has Approve, Edit and Reject. This demo is read-only."
        )

with strategy_tab:
    doc = load_latest_strategy(WORKSPACE)
    evidence = {item.id: item for item in doc.evidence}

    def support(ids):
        return " ".join(f"`{ref}`" for ref in ids) or "`no supporting evidence`"

    st.subheader("Positioning")
    st.markdown(f"### {doc.positioning.positioning_statement}")
    st.subheader("Messaging house")
    st.success(doc.messaging_house.core_message.text)
    for column, pillar in zip(st.columns(3), doc.messaging_house.pillars, strict=False):
        with column, st.container(border=True):
            st.markdown(f"**{pillar.message}**")
            for point in pillar.proof_points:
                st.write(f"{point.text} {support(point.support)}")
    st.subheader("90-day channel plan")
    st.dataframe(
        [{"Stage": nice(stage.stage), "Channel": play.channel, "Tactic": play.tactic,
          "Timing": play.timing, "Budget %": play.budget_pct}
         for stage in doc.channel_plan.stages for play in stage.channels],
        hide_index=True, width="stretch",
    )  # fmt: skip
    st.subheader("Experiments, ranked by ICE")
    for number, experiment in enumerate(doc.experiments, 1):
        with st.expander(f"{number}. {experiment.name} (ICE {experiment.ice})"):
            st.write(experiment.hypothesis)
            st.caption(f"Metric: {experiment.metric} · Minimum sample: {experiment.minimum_sample} · "
                       f"{experiment.decision_rule}")  # fmt: skip
            st.markdown(f"Evidence: {support(experiment.support)}")
    st.subheader("Devil's advocate review")
    st.write(doc.critique.summary)
    for change in doc.revision.changes:
        st.markdown(
            f"- **{nice(change.decision)}:** {change.critique_issue}. _{change.change_made}_"
        )
    with st.expander(f"Evidence the strategy cites ({len(evidence)} items)"):
        for item in evidence.values():
            st.markdown(
                f"`{item.id}` {item.text[:200]}" + (f" _({item.quality})_" if item.quality else "")
            )

with results:
    analysis = analyze(engine, WORKSPACE)
    st.caption(f"Week of {analysis.window_start} to {analysis.window_end} · "
               f"{analysis.rows_used} rows matched to a piece")  # fmt: skip
    st.subheader("Click-through rate by angle")
    by_angle = {nice(row.value): row.rate_pct for row in analysis.performance
                if row.dimension == "angle" and row.metric == "click-through rate"}  # fmt: skip
    st.bar_chart(by_angle, horizontal=True, y_label="", x_label="Click-through rate (%)")
    st.caption("Descriptive only. The experiment results below say whether a gap is real.")
    st.subheader("Experiment results")
    for readout in analysis.readouts:
        name = readout.name.split("-")[-1] if readout.kind == "ab_test" else readout.name
        with st.container(border=True):
            arms = ", ".join(f"{nice(arm['label'])} {arm['rate_pct']}% ({arm['successes']}/{arm['trials']})"
                             for arm in readout.arms)  # fmt: skip
            if readout.status == "significant":
                st.markdown(f"**{nice(name)}** · :green[{nice(readout.winner)} wins], "
                            f"+{readout.lift_pct}% over the runner-up")  # fmt: skip
            elif readout.status == "not_enough_data":
                st.markdown(f"**{nice(name)}** · :orange[Not enough data yet]. Too early to call, "
                            "whatever the rates suggest.")  # fmt: skip
            else:
                st.markdown(f"**{nice(name)}** · No real difference")
            st.caption(f"{nice(readout.metric)}: {arms}")
    st.subheader("Learning log")
    for entry in learning_log(engine, WORKSPACE):
        if entry["kind"] != "learnings":
            continue
        st.markdown(f"**Week of {entry['window']}**")
        for change in entry["changes"]:
            colour = "green" if change["decision"] == "accepted" else "red"
            st.markdown(f"- :{colour}[{nice(change['decision'])}] {change['change']}  \n"
                        f"  _{change['reason']}_")  # fmt: skip

with brain_tab:
    brain = load_brain(WORKSPACE)
    confirmed = sum(meta.status == "confirmed" for meta in brain.fields.values())
    st.progress(
        confirmed / len(brain.fields),
        text=f"{confirmed} of {len(brain.fields)} fields confirmed by the owner",
    )
    st.caption("Fields drafted from the website stay 'inferred' until a person confirms them, and "
               "the agents treat them as assumptions.")  # fmt: skip
    rows = []
    for path, meta in brain.fields.items():
        value = brain.get(path)
        shown = value if isinstance(value, str) else json.dumps(
            [v if isinstance(v, str) else v.model_dump() for v in value] if isinstance(value, list)
            else value.model_dump(), ensure_ascii=False)  # fmt: skip
        rows.append(
            {
                "Field": path,
                "Status": meta.status,
                "Confidence": meta.confidence,
                "Value": shown[:300],
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
