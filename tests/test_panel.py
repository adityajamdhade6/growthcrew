"""Phase 6: personas, the pre-test, aggregation, calibration against real results."""

import json
import random

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew.api.main import app, engine_dep
from growthcrew.db.models import Draft, ExperimentRegistration, PanelRun, Persona
from growthcrew.llm import LLM
from growthcrew.panel import calibration, personas, pretest
from growthcrew.panel.cycle import pretest_cycle
from growthcrew.panel.personas import PersonaBatch, PersonaDraft
from growthcrew.panel.pretest import Reaction, Reactions, Variant
from test_research import ScriptedClient, reply
from test_strategy import BRAND, RESEARCH


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def draft_persona(name, support=("brain:icp.pains",), phrases=()):
    return PersonaDraft(name=name, summary=f"{name} runs a cafe", segment="cafe owners",
                        demographics=["35-50"], pains=["early starts"], objections=["price"],
                        media_habits=["Instagram"], phrases=list(phrases),
                        support=list(support))  # fmt: skip


def llm_with(engine, *parsed):
    client = ScriptedClient(*(reply(parsed=item) for item in parsed))
    return LLM(client=client, engine=engine, wait=wait_none()), client


def make_personas(engine, count, generation=1):
    rows = [
        Persona(workspace="acme", generation=generation, name=f"P{n}",
                profile=draft_persona(f"P{n}").model_dump_json(exclude={"name", "support"}),
                support='["brain:icp.pains"]')
        for n in range(count)
    ]  # fmt: skip
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(rows)
        session.commit()
    return rows


# --- personas ---


def test_personas_keep_only_known_evidence_and_verbatim_phrases():
    known = {"brain:icp.pains", "voc:1"}
    quotes = ["The starter died twice and support never answered"]
    kept = personas.check(
        draft_persona("Ana", ["brain:icp.pains", "claim:99"],
                      ["support never answered", "I hate waiting"]), known, quotes,
    )  # fmt: skip
    assert kept.support == ["brain:icp.pains"] and kept.phrases == ["support never answered"]
    assert personas.check(draft_persona("Bo", ["made:up"]), known, quotes) is None


def test_generation_writes_a_new_panel_and_stops_when_nothing_new(engine):
    first = PersonaBatch(personas=[draft_persona("Ana"), draft_persona("Bo", ["nope"])])
    repeat = PersonaBatch(personas=[draft_persona("ana")])
    llm, client = llm_with(engine, first, repeat)
    made = personas.generate(llm, engine, BRAND, RESEARCH, size=4)
    assert [p.name for p in made] == ["Ana"] and made[0].generation == 1
    assert len(client.requests) == 2
    assert "Ana (cafe owners)" in client.requests[1]["messages"][0]["content"]
    llm, _ = llm_with(engine, PersonaBatch(personas=[draft_persona("Cy")]))
    assert personas.generate(llm, engine, BRAND, RESEARCH, size=1)[0].generation == 2
    assert [p.name for p in personas.panel(engine, "acme")] == ["Cy"]


# --- pre-test ---


def reactions(**by_option):
    return Reactions(reactions=[
        Reaction(option=option, stop=stop, click=click, objection="too pricey", confusing="")
        for option, (stop, click) in by_option.items()
    ])  # fmt: skip


def test_each_persona_sees_a_shuffled_order_mapped_back_by_letter(engine):
    people = make_personas(engine, 1)
    variants = [Variant(label="pain", text="Tired of stale bread?"),
                Variant(label="outcome", text="Sunday bread, every day")]  # fmt: skip
    llm, client = llm_with(engine, reactions(A=(6, True), B=(2, False)))
    rng = random.Random(1)
    answer = pretest.ask(llm, people[0], variants, rng, "acme")
    order = random.Random(1)
    shuffled = variants[:]
    order.shuffle(shuffled)
    first_shown = shuffled[0].label
    assert next(a for a in answer if a["label"] == first_shown)["stop"] == 6
    request = client.requests[0]
    assert "tools" not in request and "order they are shown in means nothing" in request["system"]
    # An answer that skips an option or invents one is not counted.
    llm, _ = llm_with(engine, reactions(A=(6, True), C=(2, False)))
    assert pretest.ask(llm, people[0], variants, rng, "acme") is None


def test_aggregate_ranks_with_bootstrap_uncertainty():
    responses = [
        [{"label": "a", "stop": 6, "click": True, "objection": "", "confusing": ""},
         {"label": "b", "stop": 3, "click": False, "objection": "boring", "confusing": "what?"}]
        for _ in range(9)
    ]  # fmt: skip
    responses += [
        [{"label": "a", "stop": 2, "click": False, "objection": "", "confusing": ""},
         {"label": "b", "stop": 5, "click": True, "objection": "", "confusing": ""}],
        None,
    ]  # fmt: skip
    prediction = pretest.aggregate(responses, ["a", "b"])
    assert prediction.ranking == ["a", "b"] and prediction.respondents == 10
    a, b = prediction.variants
    assert a.click_share == 0.9 and b.click_share == 0.1
    assert a.prob_first > 0.99 and b.prob_last > 0.99
    assert prediction.disagreement == 0.1
    assert b.top_objections == ["boring"] * 3 and b.confusing == ["what?"] * 3


def stats(label, prob_last):
    return pretest.VariantStats(label=label, respondents=20, mean_stop=4, stop_sd=1,
                                click_share=0.3, prob_first=0.1, prob_last=prob_last,
                                top_objections=[], confusing=[])  # fmt: skip


def test_panel_only_suggests_dropping_a_clearly_weak_variant_from_a_bigger_test():
    weak_c = [stats("a", 0.0), stats("b", 0.03), stats("c", 0.96)]
    three = pretest.Prediction(ranking=["a", "b", "c"], respondents=20, disagreement=0.2,
                               variants=weak_c)  # fmt: skip
    assert pretest.recommend(three, "untested").drop == ["c"]
    assert pretest.recommend(three, "useful").drop == ["c"]
    assert pretest.recommend(three, "low").drop == []
    close = three.model_copy(
        update={"variants": [stats("a", 0.3), stats("b", 0.3), stats("c", 0.4)]}
    )
    assert pretest.recommend(close, "useful").drop == []
    two = three.model_copy(update={"variants": [stats("a", 0.0), stats("b", 1.0)]})
    assert pretest.recommend(two, "useful").drop == []


# --- calibration ---


def test_spearman_handles_ties():
    assert calibration.spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert calibration.spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert calibration.spearman([1, 1, 1], [1, 2, 3]) == 0.0


def record(engine, cycle, predicted, actual_rates):
    variants = [stats(label, 0.0).model_copy(update={"click_share": share})
                for label, share in predicted.items()]  # fmt: skip
    ranking = sorted(predicted, key=lambda label: -predicted[label])
    readout = {"arms": [{"label": label, "trials": 1000, "successes": round(rate * 1000)}
                        for label, rate in actual_rates.items()]}  # fmt: skip
    with Session(engine) as session:
        session.add(
            PanelRun(
                workspace="acme",
                cycle_id=cycle,
                experiment="07-day03-ad",
                prediction=pretest.Prediction(
                    ranking=ranking, variants=variants, respondents=20, disagreement=0
                ).model_dump_json(),
            )
        )
        session.add(ExperimentRegistration(workspace="acme", cycle_id=cycle,
                                           experiment="07-day03-ad", data="{}",
                                           verdict=json.dumps(readout)))  # fmt: skip
        session.commit()


def simulate(engine, accuracy, tests=12, seed=0):
    """Tests with known true rates; the panel sees the truth with this much signal."""
    rng = np.random.default_rng(seed)
    for cycle in range(tests):
        truth = dict(zip("abc", rng.uniform(0.01, 0.03, 3), strict=True))
        noise = rng.normal(0, 1, 3)
        signal = np.array(list(truth.values()))
        signal = (signal - signal.mean()) / signal.std()
        guess = accuracy * signal + (1 - accuracy) * noise
        record(engine, cycle, dict(zip("abc", guess, strict=True)), truth)


def test_a_panel_that_tracks_reality_is_trusted_and_one_that_does_not_is_downweighted(engine):
    assert calibration.accuracy(engine, "acme")["trust"] == "untested"
    simulate(engine, accuracy=0.9)
    good = calibration.accuracy(engine, "acme")
    assert good["trust"] == "useful" and good["tests_compared"] == 12
    assert good["mean_correlation"] > 0.5 and good["top_pick_hit_rate"] > good["chance_hit_rate"]

    other = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(other)
    simulate(other, accuracy=-0.5, seed=1)
    bad = calibration.accuracy(other, "acme")
    assert bad["trust"] == "low" and "down-weighted" in bad["message"]


def test_runs_without_a_final_verdict_are_not_scored(engine):
    with Session(engine) as session:
        session.add(PanelRun(workspace="acme", cycle_id=1, experiment="x", prediction="{}"))
        session.add(ExperimentRegistration(workspace="acme", cycle_id=1, experiment="x", data="{}"))
        session.commit()
    assert calibration.comparisons(engine, "acme") == []


# --- in the cycle ---


def add_variants(engine, cycle_id=1):
    with Session(engine) as session:
        for angle in ("pain", "outcome", "social_proof"):
            session.add(Draft(cycle_id=cycle_id, workspace="acme",
                              piece_id=f"07-day03-ad-{angle}", content_type="ad", angle=angle,
                              original_text=f"{angle} ad", text=f"{angle} ad", body_json="{}",
                              metadata_json="{}", min_score=9, passed_critic=True))  # fmt: skip
        session.add(Draft(cycle_id=cycle_id, workspace="acme", piece_id="01-day01-linkedin_post",
                          content_type="linkedin_post", original_text="t", text="t",
                          body_json="{}", metadata_json="{}", min_score=9,
                          passed_critic=True))  # fmt: skip
        session.commit()


def test_cycle_pretests_each_new_experiment_once(engine, tmp_path, monkeypatch):
    from growthcrew import config
    from growthcrew.brain import store

    monkeypatch.setattr(config, "PANEL_SIZE", 2)
    store.save_brain(BRAND, note="t", root=tmp_path, engine=engine)
    folder = tmp_path / "acme" / "research"
    folder.mkdir(parents=True)
    (folder / "r.json").write_text(RESEARCH.model_dump_json())
    add_variants(engine)
    batch = PersonaBatch(personas=[draft_persona("Ana"), draft_persona("Bo")])
    answer = reactions(A=(6, True), B=(3, False), C=(2, False))
    llm, client = llm_with(engine, batch, answer, answer)
    notes = pretest_cycle(llm, engine, 1, tmp_path)
    assert len(notes) == 1 and notes[0].startswith("panel predicts")
    with Session(engine) as session:
        [run] = session.exec(select(PanelRun)).all()
    assert run.experiment == "07-day03-ad" and run.trust == "untested"
    assert json.loads(run.prediction)["respondents"] == 2
    # Already pre-tested: nothing to do, and no model calls.
    calls = len(client.requests)
    assert pretest_cycle(llm, engine, 1, tmp_path) == [] and len(client.requests) == calls


def test_panel_routes(engine):
    add_variants(engine)
    app.dependency_overrides[engine_dep] = lambda: engine
    try:
        client = TestClient(app)
        assert client.get("/drafts/1/panel").json() == {"experiment": "07-day03-ad", "ran": False}
        record(engine, 1, {"pain": 0.5, "outcome": 0.2, "social_proof": 0.1},
               {"pain": 0.02, "outcome": 0.01, "social_proof": 0.015})  # fmt: skip
        data = client.get("/drafts/1/panel").json()
        assert data["ran"] and data["prediction"]["ranking"][0] == "pain"
        summary = client.get("/workspaces/acme/panel").json()
        assert summary["accuracy"]["tests_compared"] == 1 and summary["personas"] == []
    finally:
        app.dependency_overrides.pop(engine_dep, None)


def test_a_pretest_cut_short_by_the_budget_keeps_enough_answers(engine, monkeypatch):
    from growthcrew import budget

    people = make_personas(engine, 10)
    variants = [Variant(label="a", text="A"), Variant(label="b", text="B")]
    answer = [
        {"label": "a", "stop": 5, "click": True, "objection": "", "confusing": ""},
        {"label": "b", "stop": 2, "click": False, "objection": "", "confusing": ""},
    ]

    def budget_after(answers):
        calls = []

        def fake_ask(llm, persona, variants, rng, workspace):
            calls.append(persona.name)
            if len(calls) > answers:
                raise budget.BudgetExceeded("spent")
            return answer

        return fake_ask

    monkeypatch.setattr(pretest, "ask", budget_after(9))
    run = pretest.run(None, engine, "acme", 1, "x", variants, people, "untested")
    assert json.loads(run.prediction)["respondents"] == 9
    monkeypatch.setattr(pretest, "ask", budget_after(3))
    with pytest.raises(budget.BudgetExceeded):
        pretest.run(None, engine, "acme", 1, "y", variants, people, "untested")


def test_images_are_shown_only_when_every_variant_has_one(engine):
    people = make_personas(engine, 1)
    png = b"\x89PNG\r\n\x1a\n"
    with_images = [Variant(label="a", text="A", image=png), Variant(label="b", text="B", image=png)]
    llm, client = llm_with(engine, reactions(A=(4, True), B=(3, False)),
                           reactions(A=(4, True), B=(3, False)))  # fmt: skip
    pretest.ask(llm, people[0], with_images, random.Random(0), "acme")
    content = client.requests[0]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["image", "image", "text"]
    one_image = [with_images[0], Variant(label="b", text="B")]
    pretest.ask(llm, people[0], one_image, random.Random(0), "acme")
    assert isinstance(client.requests[1]["messages"][0]["content"], str)
