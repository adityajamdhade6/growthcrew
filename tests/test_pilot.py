from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from growthcrew import pilot
from growthcrew.db.models import Approval, ChangeDecision, Cycle, Draft, Learnings, LLMCall

START = date(2026, 10, 5)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def kit(tmp_path):
    return pilot.init_pilot("acme", "Acme Bakery", START, "Sam", root=tmp_path), tmp_path


def fill(folder, baseline, weeks):
    lines = (folder / "baseline.csv").read_text().splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        key = line.split(",")[0]
        value, last_year = baseline.get(key, ("", ""))
        cells = line.rsplit(",", 4)
        out.append(f"{cells[0]},{value},{last_year},,")
    (folder / "baseline.csv").write_text("\n".join(out) + "\n")
    header = (folder / "weekly.csv").read_text().splitlines()[0]
    keys = header.split(",")
    rows = [header]
    for number in range(1, pilot.WEEKS + 1):
        row = {"week": number, "week_start": "", **weeks.get(number, {})}
        rows.append(",".join(str(row.get(key, "")) for key in keys))
    (folder / "weekly.csv").write_text("\n".join(rows) + "\n")


def test_init_writes_the_kit_and_never_overwrites_what_a_human_filled_in(kit):
    folder, root = kit
    assert {p.name for p in folder.iterdir()} == {
        "pilot.json", "baseline.csv", "weekly.csv", "plan.md", "weekly-checklist.md",
        "testimonial-request.md",
    }  # fmt: skip
    baseline = (folder / "baseline.csv").read_text()
    assert "05 Sep 2026 to 04 Oct 2026" in baseline  # the 30 days before the pilot
    for key in ("followers", "impressions", "engagement_rate_pct", "sessions_from_social",
                "leads", "email_reply_rate_pct", "hours_on_marketing"):  # fmt: skip
        assert f"\n{key}," in baseline
    assert (folder / "weekly.csv").read_text().count("\n") == pilot.WEEKS + 1
    checklist = (folder / "weekly-checklist.md").read_text()
    order = [checklist.index(step) for step in ("Upload data", "Review WeeklyLearnings",
                                                "Approve content", "Log the week")]  # fmt: skip
    assert order == sorted(order)
    assert "No strategy exists" in (folder / "plan.md").read_text()

    (folder / "baseline.csv").write_text("mine")
    pilot.init_pilot("acme", "Acme Bakery", START, root=root)
    assert (folder / "baseline.csv").read_text() == "mine"


def test_report_compares_with_baseline_on_the_same_footing_and_flags_what_it_cannot_say(
    kit, engine
):
    folder, root = kit
    fill(
        folder,
        {"followers": (1000, ""), "impressions": (30000, ""), "leads": (4, ""),
         "hours_on_marketing": (6, "")},
        {week: {"followers": 1000 + 25 * week, "impressions": 10500, "leads": 2 if week == 1 else 1,
                "hours_on_marketing": 3, "other_activity": "Trade show" if week == 2 else ""}
         for week in range(1, 5)},
    )  # fmt: skip
    report = pilot.build_report(engine, "acme", 30, root=root)

    # Four weeks is 28 days, so flows are scaled to 30: 42,000 / 28 * 30 = 45,000 (+50%).
    assert "| Impressions | 30000 | 45000.0 | +50% |" in report
    assert "| Followers | 1000 | 1100.0 | +10% |" in report
    # Five leads against four is noise, and the report says so instead of celebrating +34%.
    assert "| Leads or inquiries | 4 | 5.4 | +34% | too few to read anything into |" in report
    assert "Email reply rate (%): no baseline" in report
    assert "6 before, 3.0 during this period" in report

    assert "not an experiment" in report and "No control group" in report
    assert "The owner logged: Week 2: Trade show" in report
    assert "No same-period figures from last year were recorded" in report
    assert (folder / "report-day30.md").exists()


def test_day_60_report_uses_the_second_month_and_last_years_figures(kit, engine):
    folder, root = kit
    fill(folder, {"impressions": (30000, 52000)},
         {week: {"impressions": 7000 if week <= 4 else 14000} for week in range(1, 9)})  # fmt: skip
    report = pilot.build_report(engine, "acme", 60, root=root)
    assert "weeks 5 to 8 (days 29 to 56)" in report
    assert "| Impressions | 30000 | 60000.0 | +100% | same period last year: 52000 |" in report
    assert "compare the change with them before crediting the pilot" in report


def test_tracker_combines_logged_metrics_with_what_the_system_recorded(kit, engine):
    folder, root = kit
    fill(folder, {}, {1: {"followers": 1010, "hours_on_marketing": 4}})
    week1 = datetime(2026, 10, 7, tzinfo=UTC)
    with Session(engine) as session:
        cycle = Cycle(workspace="acme", week_start=week1)
        session.add(cycle)
        session.flush()
        for number, decision in enumerate(("approved", "approved", "edited", "rejected")):
            draft = Draft(cycle_id=cycle.id, workspace="acme", piece_id=f"p{number}",
                          content_type="linkedin_post", original_text="x", text="x", body_json="{}",
                          metadata_json="{}", min_score=9, passed_critic=True)  # fmt: skip
            session.add(draft)
            session.flush()
            session.add(Approval(draft_id=draft.id, decision=decision, reviewer="sam",
                                 created_at=week1))  # fmt: skip
        session.add(LLMCall(agent="content", workspace="acme", model="m", cost_usd=1.25,
                            created_at=week1))  # fmt: skip
        session.add(Learnings(workspace="acme", data="{}", created_at=week1))
        session.add(ChangeDecision(workspace="acme", learnings_id=1, cycle_id=cycle.id,
                                   change_json='{"change": "Shift 40% of posts to outcome"}',
                                   decision="accepted", reason="r", created_at=week1))  # fmt: skip
        session.commit()

    tracker = pilot.build_tracker(engine, "acme", root=root, today=date(2026, 10, 14))
    assert "Day 10 of 60" in tracker
    assert "| 1 | 2 | 1 | 1 | 25% | $1.25 | 3 of 4 steps |" in tracker  # no data uploaded
    assert "| 2 | 0 | 0 | 0 |  | $0.00 | 0 of 4 steps |" in tracker
    assert "Week 1: accepted: Shift 40% of posts to outcome" in tracker
    assert "Hours not logged for week(s): 2" in tracker
    assert "| 3 |" not in tracker  # week 3 has not started
    assert (folder / "tracker.json").exists()


def test_testimonial_needs_confirmed_wording_and_is_only_returned_for_permitted_uses(kit):
    _, root = kit
    words = pilot.Testimonial(
        quote="I got my Sunday evenings back.", name="Sam Okafor", role="Owner",
        business="Acme Bakery", attribution="first_name_only", allowed_uses=["website"],
        wording_confirmed_by_owner=False, permission_given_on=date(2026, 12, 5),
    )  # fmt: skip
    with pytest.raises(ValueError, match="has not confirmed"):
        pilot.record_testimonial("acme", words, root=root)
    confirmed = words.model_copy(update={"wording_confirmed_by_owner": True})
    with pytest.raises(ValueError, match="has not allowed any use"):
        pilot.record_testimonial(
            "acme", confirmed.model_copy(update={"allowed_uses": []}), root=root
        )
    with pytest.raises(ValueError, match="Unknown use"):
        pilot.record_testimonial(
            "acme", confirmed.model_copy(update={"allowed_uses": ["tv"]}), root=root
        )

    assert pilot.testimonial_for("acme", "website", root=root) is None  # nothing saved yet
    pilot.record_testimonial("acme", confirmed, root=root)
    assert (
        pilot.testimonial_for("acme", "website", root=root)
        == "“I got my Sunday evenings back.” (Sam)"
    )
    assert pilot.testimonial_for("acme", "social", root=root) is None
