import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from growthcrew import config
from growthcrew.db import session as db_session

APP = Path(__file__).parent.parent / "streamlit_app.py"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Run the demo script once, in a scratch folder with its own database."""
    folder = tmp_path_factory.mktemp("demo")
    patch = pytest.MonkeyPatch()
    patch.chdir(folder)
    patch.setattr(config, "DATABASE_URL", f"sqlite:///{folder / 'demo.db'}")
    patch.setenv("DATABASE_URL", f"sqlite:///{folder / 'demo.db'}")
    db_session.get_engine.cache_clear()
    app = AppTest.from_file(str(APP), default_timeout=120).run()
    yield app
    patch.undo()
    db_session.get_engine.cache_clear()


def text_of(app) -> str:
    parts = [e.value for e in [*app.markdown, *app.caption, *app.subheader, *app.error]]
    return "\n".join(str(part) for part in parts)


def badges(app) -> list[str]:
    """Badge labels. Streamlit renders a badge as markdown like `:green-badge[Done]`."""
    found = []
    for element in app.markdown:
        value = str(element.value)
        if "-badge[" in value and value.endswith("]"):
            found.append(value.split("-badge[", 1)[1][:-1])
    return found


def test_demo_renders_every_tab_without_errors(demo):
    assert not demo.exception
    labels = [tab.label for tab in demo.tabs]
    assert labels[:5] == ["Mission control", "Calendar", "Strategy", "Results", "Brand brain"]
    assert labels[5:] == ["How it works", "Evals"]
    header = demo.markdown[0].value
    assert "Built by" in header and "github.com/adityajamdhade6/growthcrew" in header
    assert "docs/case_study.md" in header


def test_copy_uses_names_people_read(demo):
    text = text_of(demo)
    assert "Day 3 newsletter" in text and "03-day03-newsletter" not in text
    assert "LinkedIn post" in text
    assert "linkedin_post" not in text and "Linkedin" not in text
    assert "1 model call ·" in text and not re.search(r"(?<!\d)1 model calls", text)


def test_confident_ad_winner_is_no_longer_rejected(demo):
    text = text_of(demo)
    assert "Move ad budget to the outcome hook" in text
    assert "Accepted in part: outcome won Day 3 ad (+54% over the runner-up" in text
    assert "single week of data" in text and "Shifting 25% now" in text
    assert badges(demo).count("Accepted in part") == 1 and "Rejected" not in badges(demo)


def test_every_readout_states_its_uncertainty(demo):
    lines = [m.value for m in demo.markdown if "probability that" in m.value]
    assert len(lines) == 3  # one per experiment readout
    for line in lines:
        assert "95% interval" in line and "sample:" in line
    assert any("44,322 impressions" in line for line in lines)
    assert any("includes zero" in line for line in lines)  # the landing page test, still running
    # No false certainty, and only a registered test is badged as a win.
    assert sum(line.startswith("Over 99.9% probability") for line in lines) == 2
    assert not any("100" in line.split(" probability")[0] for line in lines)
    assert badges(demo).count("Outcome wins") == 1
    assert "Descriptive only · not pre-registered" in badges(demo)
    assert "Keep running" in badges(demo)


def test_results_show_probability_to_be_best_and_expected_loss_for_every_test(demo):
    tables = [t.value for t in demo.dataframe if "Probability it is best" in t.value.columns]
    assert len(tables) == 3
    ad = tables[0].set_index("Variant")
    assert ad.loc["Outcome", "Probability it is best"] == "Over 99.9%"
    assert ad.loc["Outcome", "Expected loss if chosen"] == "0.0% of the rate"
    assert ad.loc["Pain", "Probability it is best"] == "Under 0.1%"
    text = text_of(demo)
    assert "Next week's budget split" in text and "Outcome 100%" in text
    assert "What was pre-registered" in [e.label for e in demo.expander]
    assert "more sessions** needed before a call" in text


def test_brand_brain_is_grouped_with_badges_and_sources(demo):
    assert {"Business", "ICP", "Voice", "Proof", "Competitors"} <= {s.value for s in demo.subheader}
    text = text_of(demo)
    assert "What agents do differently with an inferred fact" in text
    assert "loomhouse.example/faq" in text and "Implied by the questions the FAQ answers" in text
    assert "Confirmed" in badges(demo) and "Inferred · low confidence" in badges(demo)


def test_calendar_opens_on_a_draft_that_needs_approval(demo):
    [radio] = demo.radio
    chosen = next(s.value for s in demo.subheader if s.value.startswith("Day "))
    assert chosen == "Day 1 ad (outcome angle)"  # not the blocked pain variant listed first
    assert radio.index == 1


def test_channel_plan_how_it_works_and_evals_are_present(demo):
    text = text_of(demo)
    assert "**Meta ads** (awareness, 35%)" in text  # the plan rendered, with all four channels
    assert len(demo.get("vega_lite_chart")) == 5  # CTR, three readouts, the plan timeline
    assert len(demo.get("graphviz_chart")) == 1
    assert "Eval scorecard" in text and "Analyst winner detection" in text
