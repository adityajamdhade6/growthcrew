import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew.agents.content import ContentAgent, draft_model
from growthcrew.agents.content_models import BatchPlan, CritiqueDraft, PlannedItem, Score
from growthcrew.agents.strategist import StrategistAgent, StrategyInput
from growthcrew.brain.models import Proof, ProofItem
from growthcrew.content.checks import allowed_facts, cliche_hits, unverified_facts
from growthcrew.content.templates import TEMPLATES
from growthcrew.content.types import (
    Ad,
    BlogArticle,
    ContentRequest,
    LinkedInPost,
    Outline,
    PieceMetadata,
    SEOBrief,
    XThread,
)
from growthcrew.db.models import ContentRevision
from growthcrew.llm import LLM
from growthcrew.reports.content import piece_history, save_batch
from test_strategy import BRAND, CRITIQUE, DRAFT, FINAL, RESEARCH, REVISION, ScriptedClient

META = PieceMetadata(
    messaging_pillar="Pillar 0", target_persona="Busy parent", cta="Start a trial",
    hypothesis="If we lead with staleness, then signups will rise because it is the top pain",
)  # fmt: skip
REQUEST = ContentRequest(
    content_type="linkedin_post", pillar="Pillar 0", audience="Busy households",
    funnel_stage="awareness", goal="Trial signups",
)  # fmt: skip


def llm_with(outputs):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    client = ScriptedClient(outputs)
    return LLM(client=client, engine=engine, wait=wait_none()), client, engine


@pytest.fixture(scope="module")
def strategy():
    llm, _, _ = llm_with([*DRAFT, CRITIQUE, REVISION, *FINAL])
    return StrategistAgent(llm).run(StrategyInput(brand=BRAND, research=RESEARCH))


def linkedin(hook, body="We bake at 4am and deliver by 7."):
    post = LinkedInPost(hook=hook, body=body, hashtags=["#bread"])
    return draft_model(LinkedInPost)(metadata=META, body=post)


def critique(score, edits=()):
    one = Score(score=score, reason="r")
    return CritiqueDraft(voice=one, clarity=one, persuasion=one, accuracy=one,
                         channel_fit=one, ai_cliche=one, edits=list(edits))  # fmt: skip


# --- deterministic checks ---


def test_cliche_check_uses_default_and_brand_lists():
    brand = BRAND.model_copy(deep=True)
    brand.voice.guide.banned_phrases = ["artisan"]
    hits = cliche_hits(["Unlock artisan bread", "We unlocked nothing", "Plain line"], brand)
    assert hits == [(1, "unlock"), (1, "artisan")]


def test_accuracy_check_allows_only_facts_from_the_brain(strategy):
    brand = BRAND.model_copy(deep=True)
    brand.proof = Proof(stats=[ProofItem(summary="Retention", quote="92% of subscribers renew",
                                         source_url="https://acme.test/")])  # fmt: skip
    facts = allowed_facts(brand, strategy)
    lines = ['92% renew, and 47% buy more. "This bread changed our family breakfasts forever"']
    assert unverified_facts(lines, facts) == [
        (1, "47%"),
        (1, '"This bread changed our family breakfasts forever"'),
    ]


def test_channel_limits():
    assert TEMPLATES["x_thread"].limits(XThread(tweets=["a" * 281, "b", "c"])) == [
        "Tweet 1 is 281 characters; the limit is 280"
    ]
    google = Ad(platform="google", hook="h", primary_text="p", headline="x" * 31)
    meta = google.model_copy(update={"platform": "meta"})
    assert TEMPLATES["ad"].limits(google) and not TEMPLATES["ad"].limits(meta)


# --- critic loop ---


def test_code_checks_cap_scores_and_the_revision_passes(strategy):
    llm, client, engine = llm_with([
        linkedin("Unlock fresher bread: 47% of loaves go stale"),
        critique(9),  # the model liked it; the checks did not
        linkedin("Supermarket bread is stale by Tuesday"),
        critique(9),
    ])  # fmt: skip
    [record], notes = ContentAgent(llm).produce(REQUEST, BRAND, strategy)

    first, second = record.versions
    assert first.critique.scores() == {
        "voice": 9, "clarity": 9, "persuasion": 9, "accuracy": 3, "channel_fit": 9, "ai_cliche": 4,
    }  # fmt: skip
    assert not first.critique.passed
    assert {(e.line, e.criterion) for e in first.critique.edits} == {
        (1, "ai_cliche"),
        (1, "accuracy"),
    }
    assert first.critique.edits[0].original == "Unlock fresher bread: 47% of loaves go stale"
    assert second.critique.passed and record.passed and notes == []
    assert record.status == "pending_approval"

    # The revision request carried the edits and findings back to the writer.
    revise = client.requests[2]["messages"][-1]["content"][-1]["text"]
    assert "banned phrase 'unlock'" in revise and "47% is not in the brand's proof" in revise

    with Session(engine) as session:
        rows = list(session.exec(select(ContentRevision).order_by(ContentRevision.round)))
    assert [(row.round, row.min_score, row.passed) for row in rows] == [(1, 3, False), (2, 9, True)]

    history = piece_history(record)
    assert "## Round 1: first draft" in history and "## Round 2: revision 1" in history
    assert "Hypothesis: If we lead with staleness" in history


def test_loop_stops_after_three_rounds(strategy):
    llm, client, _ = llm_with([linkedin("Hook"), critique(6)] * 3)
    [record], _ = ContentAgent(llm).produce(REQUEST, BRAND, strategy)
    assert len(record.versions) == 3 and not record.passed
    assert client.outputs == []


def test_critic_edits_must_point_at_real_lines(strategy):
    from growthcrew.agents.content_models import LineEdit

    edits = [
        LineEdit(line=1, criterion="clarity", suggestion="Sharper hook", reason="vague"),
        LineEdit(line=99, criterion="clarity", suggestion="x", reason="x"),
    ]
    llm, _, _ = llm_with([linkedin("Hook"), critique(9, edits)])
    [record], _ = ContentAgent(llm).produce(REQUEST, BRAND, strategy)
    [edit] = record.final.critique.edits
    assert (edit.line, edit.original) == (1, "Hook")


# --- variants, blog, batch ---


def test_ab_tested_types_get_a_variant_per_angle(strategy):
    request = REQUEST.model_copy(update={"content_type": "ad"})
    agent = ContentAgent(llm_with([])[0])

    angles, notes = agent.angles(request, BRAND)
    assert angles == ["pain", "outcome"] and "no proof" in notes[0]

    with_proof = BRAND.model_copy(deep=True)
    with_proof.proof.testimonials = [ProofItem(summary="s", quote="q", source_url="https://a.test")]
    assert agent.angles(request, with_proof)[0] == ["pain", "outcome", "social_proof"]
    assert agent.angles(REQUEST, with_proof)[0] == [None]
    assert agent.angles(REQUEST.model_copy(update={"ab_test": True}), with_proof)[0][0] == "pain"


def test_blog_runs_seo_brief_then_outline_then_draft(strategy):
    seo = SEOBrief(primary_keyword="bread subscription", secondary_keywords=[],
                   search_intent="commercial", title_tag="Bread subscription",
                   meta_description="Fresh bread delivered.")  # fmt: skip
    article = BlogArticle(title="Is a bread subscription worth it?", article_markdown="word " * 650)
    llm, client, _ = llm_with([
        seo, Outline(sections=[]), draft_model(BlogArticle)(metadata=META, body=article),
        critique(9),
    ])  # fmt: skip
    request = REQUEST.model_copy(update={"content_type": "blog_article"})
    [record], _ = ContentAgent(llm).produce(request, BRAND, strategy)
    assert record.seo_brief == seo and record.outline is not None and record.passed
    assert [r["output_format"].__name__ for r in client.requests[:3]] == [
        "SEOBrief", "Outline", "BlogArticleDraft",
    ]  # fmt: skip


def test_batch_is_planned_written_and_saved(strategy, tmp_path):
    plan = BatchPlan(items=[
        PlannedItem(day=99, request=REQUEST, rationale="out of range"),
        PlannedItem(day=3, request=REQUEST, rationale="Awareness gets 40% of budget"),
    ])  # fmt: skip
    llm, _, _ = llm_with([plan, linkedin("Hook"), critique(9)])
    batch = ContentAgent(llm).run_batch(BRAND, strategy, weeks=2)

    assert [item.day for item in batch.plan] == [3]
    assert [piece.id for piece in batch.pieces] == ["01-day03-linkedin_post"]

    folder = save_batch(batch, tmp_path)
    assert {path.name for path in folder.iterdir()} == {
        "batch.json", "batch.md", "01-day03-linkedin_post.md",
    }  # fmt: skip
    assert "| 3 | 01-day03-linkedin_post |" in (folder / "batch.md").read_text()
