import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from growthcrew.brain.context import render_brain
from growthcrew.brain.models import (
    FIELD_PATHS,
    ICP,
    BusinessProfile,
    Competitor,
    Proof,
    ProofItem,
    ToneSliders,
    VoiceGuide,
)
from growthcrew.brain.onboarding import BrainDraft, FieldNote, Questionnaire, onboard
from growthcrew.brain.store import confirm_fields, list_versions, load_brain
from growthcrew.brain.voice import VoiceDraft, avg_sentence_words, extract_voice
from growthcrew.db.models import BrainVersion
from growthcrew.tools.crawl import crawl
from growthcrew.tools.scrape import Page

SITE = "https://acme.test"
REAL_QUOTE = "Acme cut our scrap rate by 40%"
PAGES = [
    Page(url=f"{SITE}/", text=f"Acme makes precision brackets. {REAL_QUOTE}, says Jo. " * 20),
    *[Page(url=f"{SITE}/p{i}", text=f"We machine part {i} to tight tolerances. " * 20)
      for i in range(5)],
]  # fmt: skip


def proof_item(quote):
    return ProofItem(summary="Scrap reduced", quote=quote, attribution="Jo", source_url=f"{SITE}/")


DRAFT = BrainDraft(
    business=BusinessProfile(name="Acme", what_they_sell="Brackets", pricing="Request a quote"),
    icp=ICP(audience_type="b2b", pains=["scrap"]),
    products=[],
    proof=Proof(testimonials=[proof_item(REAL_QUOTE), proof_item("Best supplier ever, 10/10")]),
    competitors=[Competitor(name="Guessed Rival")],
    field_notes=[
        FieldNote(
            path="business.what_they_sell",
            confidence="high",
            source_urls=[f"{SITE}/", "https://elsewhere.test/x"],
            reason="stated on home page",
        ),
        FieldNote(path="icp.pains", confidence="low", source_urls=[], reason="implied only"),
        FieldNote(path="not.a.field", confidence="high", source_urls=[], reason=""),
    ],
)
VOICE = VoiceDraft(
    tone=ToneSliders(formality=9, playfulness=1, enthusiasm=2, technicality=4),
    do_words=["precision"],
    dont_words=["cheap"],
    example_passages=["We machine part 1 to tight tolerances.", "An invented passage."],
    guide=VoiceGuide(sentence_length="Under 12 words", jargon_level="moderate", rules=["Use we"]),
    confidence="medium",
    confidence_reason="samples are repetitive",
)


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def call(self, role, *, system, user, output_model, workspace=None):
        self.prompts.append(user)
        return {BrainDraft: DRAFT, VoiceDraft: VOICE}[output_model]


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def brain(tmp_path, engine):
    answers = Questionnaire(pricing="From $2 per part", competitors=[Competitor(name="Rival Co")])
    return onboard(
        SITE, answers, FakeLLM(), crawl_fn=lambda url: PAGES, root=tmp_path, engine=engine
    )


def test_onboard_marks_fields_inferred_or_confirmed(brain):
    assert set(brain.fields) == set(FIELD_PATHS)
    assert brain.fields["business.what_they_sell"].status == "inferred"
    assert brain.fields["business.what_they_sell"].confidence == "high"
    # Source URLs that were not crawled are discarded.
    assert brain.fields["business.what_they_sell"].source_urls == [f"{SITE}/"]
    # Questionnaire answers win over the draft and are confirmed.
    assert brain.business.pricing == "From $2 per part"
    assert brain.fields["business.pricing"].status == "confirmed"
    assert [c.name for c in brain.competitors] == ["Rival Co"]
    assert brain.fields["competitors"].status == "confirmed"


def test_proof_that_is_not_on_the_site_is_dropped(brain):
    assert [item.quote for item in brain.proof.testimonials] == [REAL_QUOTE]


def test_voice_is_clamped_measured_and_verbatim(brain):
    assert brain.voice.tone.formality == 5
    assert brain.voice.example_passages == ["We machine part 1 to tight tolerances."]
    assert brain.voice.avg_sentence_words > 0
    assert brain.fields["voice.guide"].confidence == "medium"


def test_weakest_lists_empty_then_low_confidence_and_skips_confirmed(brain):
    weakest = [path for path, _ in brain.weakest()]
    assert "business.geography" in weakest  # empty
    assert "icp.pains" in weakest  # low confidence
    assert "business.pricing" not in weakest  # confirmed
    assert weakest.index("business.geography") < weakest.index("icp.pains")


def test_versions_are_append_only_and_tracked_in_db(brain, tmp_path, engine):
    assert brain.version == 1
    updated = confirm_fields("acme", ["icp.pains"], root=tmp_path, engine=engine)
    assert updated.version == 2
    assert list_versions("acme", tmp_path) == [1, 2]
    assert load_brain("acme", 1, tmp_path).fields["icp.pains"].status == "inferred"
    assert load_brain("acme", root=tmp_path).fields["icp.pains"].status == "confirmed"
    with Session(engine) as session:
        rows = list(session.exec(select(BrainVersion).order_by(BrainVersion.version)))
    assert rows[1].changed_fields == "icp.pains"


def test_confirm_rejects_unknown_field(brain, tmp_path, engine):
    with pytest.raises(ValueError):
        confirm_fields("acme", ["business.nope"], root=tmp_path, engine=engine)


def test_render_brain_tags_every_field(brain):
    text = render_brain(brain)
    assert "business.pricing [confirmed]: From $2 per part" in text
    assert "icp.pains [inferred, low confidence]" in text
    assert "business.geography [inferred, low confidence]: (unknown)" in text


def test_extract_voice_requires_five_to_ten_samples():
    with pytest.raises(ValueError):
        extract_voice(["one", "two"], FakeLLM())


def test_avg_sentence_words():
    assert avg_sentence_words(["One two three. Four five."]) == 2.5


def site_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    links = "".join(f'<a href="/page{i}">p{i}</a>' for i in range(40))
    body = f"<html><body><p>{'Content for ' + path + '. ' * 30}</p>{links}"
    body += '<a href="/private/x">x</a><a href="https://other.test/">o</a>'
    body += '<a href="/about">a</a><a href="/file.pdf">f</a></body></html>'
    return httpx.Response(200, html=body)


def test_crawl_respects_robots_page_limit_and_host():
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return site_handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pages = crawl(f"{SITE}/", max_pages=5, client=client, delay=0)

    assert len(pages) == 5
    assert pages[0].url == f"{SITE}/"
    assert pages[1].url == f"{SITE}/about"  # priority pages first
    assert not any("/private" in url or "other.test" in url or ".pdf" in url for url in requested)
