"""Phase 5: brand kit, template-rendered ads, the vision critic loop, landing pages."""

import base64
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool
from tenacity import wait_none

from growthcrew.api import ui
from growthcrew.api.main import app, engine_dep
from growthcrew.brain import store
from growthcrew.brain.models import BrandColors, BrandImage, BrandKit
from growthcrew.creative.access import alt_text_problem, check_render, contrast_ratio
from growthcrew.creative.agent import (
    CreativeAgent,
    Fix,
    SizeReview,
    SizeScale,
    Slots,
    VisionReview,
    latest,
    render_html,
)
from growthcrew.creative.images import OpenAIImages, background_prompt
from growthcrew.creative.kit import best_text, image_uri, load_kit
from growthcrew.creative.landing import export_variants, landing_html, parse_hero
from growthcrew.creative.render import chromium_path
from growthcrew.db.models import Creative, Draft, GuardrailBlock
from growthcrew.llm import LLM
from growthcrew.workflow import ApprovalRequired
from test_research import ScriptedClient, reply
from test_strategy import BRAND

# A 1x1 PNG, for brand images and the fake renderer.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
AD_TEXT = (
    "Hook: Bread that tastes like Sunday\nPrimary text: Fresh sourdough, delivered weekly.\n"
    "Headline: Try a loaf"
)
HERO_TEXT = (
    "Headline: Sourdough at your door\nSubheadline: Baked this morning, <b>delivered</b> today.\n"
    "CTA button: Start a subscription\nPoint: Cancel any time\nPoint: Local flour"
)
GOOD = {"readability": 9, "hierarchy": 9, "brand_consistency": 8, "thumb_stopping": 8,
        "platform_rules": 9}  # fmt: skip


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def kitted_brand(tmp_path, alt="A sourdough loaf on a wooden board, cut open"):
    folder = tmp_path / "acme" / "brand"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "loaf.png").write_bytes(PNG)
    kit = BrandKit(
        product_images=[BrandImage(file="loaf.png", alt=alt)],
        colors=BrandColors(primary="#1f3a5f", background="#fbf7f0", text="#1a1a1a",
                           accent="#d9822b"),
        heading_font="Fraunces", image_style_rules=["warm morning light"],
    )  # fmt: skip
    return BRAND.model_copy(update={"brand_kit": kit})


def add_draft(engine, text=AD_TEXT, kind="ad", status="pending_approval", angle=None, piece=None):
    with Session(engine, expire_on_commit=False) as session:
        draft = Draft(cycle_id=1, workspace="acme", piece_id=piece or f"01-day01-{kind}",
                      content_type=kind, angle=angle, original_text=text, text=text,
                      body_json="{}", metadata_json="{}", min_score=9, passed_critic=True,
                      status=status)  # fmt: skip
        session.add(draft)
        session.commit()
        return draft


class FakeRenderer:
    def __init__(self, measures=None):
        self.measures = measures or {}
        self.html = []

    def render(self, html, width, height):
        self.html.append(html)
        size = {1080: "square", 1350: "portrait", 1920: "story"}[height]
        return PNG, self.measures.get(size, {"slots": {"headline": {"font_px": 84}},
                                             "text_share": 0.2, "words": 12})  # fmt: skip


def slots(headline="Bread that tastes like Sunday", scale=1.0):
    return Slots(headline=headline, subcopy="Fresh sourdough, delivered weekly.",
                 cta="Try a loaf", alt_text="A cut sourdough loaf on a board",
                 scales=[SizeScale(size="story", text_scale=scale)])  # fmt: skip


def review(**overrides):
    return VisionReview(reviews=[
        SizeReview(size=size, **(GOOD | overrides.get(size, {})),
                   fixes=[Fix(target="headline", problem="long", instruction="shorten")]
                   if overrides.get(size) else [])
        for size in ("square", "portrait", "story")
    ])  # fmt: skip


def llm_with(engine, *parsed):
    client = ScriptedClient(*(reply(parsed=item) for item in parsed))
    return LLM(client=client, engine=engine, wait=wait_none()), client


# --- accessibility and kit ---


def test_contrast_ratio_matches_wcag():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert best_text("#1f3a5f") == "#ffffff" and best_text("#fbf7f0") == "#111111"


def test_alt_text_must_describe_the_image():
    assert alt_text_problem("") == "The image has no alt text"
    assert "names the file" in alt_text_problem("This is loaf.png here", "loaf.png")
    assert "names the file" in alt_text_problem("Photo IMG_2041 of bread", "x.png")
    assert "too short" in alt_text_problem("Bread", "x.png")
    assert alt_text_problem("A sourdough loaf on a wooden board", "x.png") == ""


def test_render_checks_cap_the_right_scores():
    measure = {
        "slots": {"headline": {"font_px": 40, "clipped": True},
                  "cta": {"font_px": 38, "outside_safe_area": True}},
        "text_share": 0.5, "words": 40,
    }  # fmt: skip
    found = check_render(measure, {"headline": ("#999999", "#ffffff")}, "9:16")
    checks = {(f.check, f.caps) for f in found}
    assert checks == {
        ("contrast", "readability"), ("text_size", "readability"), ("clipped", "readability"),
        ("safe_area", "platform_rules"), ("text_share", "platform_rules"),
        ("word_count", "platform_rules"),
    }  # fmt: skip


def test_brand_images_must_be_image_files_inside_the_brand_folder(tmp_path):
    folder = tmp_path / "brand"
    folder.mkdir()
    (folder / "logo.svg").write_text("<svg onload='alert(1)'/>")
    (tmp_path / "secret.png").write_bytes(PNG)
    with pytest.raises(ValueError):
        image_uri(folder, "logo.svg")
    with pytest.raises(ValueError):
        image_uri(folder, "../secret.png")


def test_kit_falls_back_to_readable_defaults(tmp_path):
    kit = load_kit(BRAND, tmp_path)
    assert kit.from_brain is False and kit.product_image == ""
    assert contrast_ratio(kit.text, kit.background) >= 4.5
    assert contrast_ratio(kit.cta_text, kit.primary) >= 4.5


def test_template_escapes_slot_text(tmp_path):
    kit = load_kit(kitted_brand(tmp_path), tmp_path)
    html = render_html(kit, slots("<script>alert(1)</script>"), "story", "Acme")
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "padding: 250px 80px 340px" in html  # the story's safe area
    assert "Fraunces" in html and "#fbf7f0" in html


def test_brand_kit_is_a_brain_field_that_starts_empty():
    assert BRAND.is_empty("brand_kit")
    assert not BRAND.model_copy(update={"brand_kit": BrandKit(heading_font="Inter")}).is_empty(
        "brand_kit"
    )


# --- the vision critic loop ---


def test_code_checks_cap_the_critic_and_fixes_go_back_to_the_slots(tmp_path, engine):
    brand = kitted_brand(tmp_path)
    draft = add_draft(engine)
    small = {"story": {"slots": {"subcopy": {"font_px": 24}}, "text_share": 0.2, "words": 12}}
    renderer = FakeRenderer(small)
    llm, client = llm_with(engine, slots(), review(), slots(scale=1.25), review())
    # Round 1: the critic scores everything 8+, but the story's sub-copy measured too small.
    result = CreativeAgent(llm, engine, renderer, tmp_path).run(draft, brand, max_rounds=1)
    assert not result.passed
    assert result.rounds[0].scores["story"]["readability"] == 5
    assert result.rounds[0].scores["square"]["readability"] == 9

    # With the fix applied (a bigger text scale), the next run passes.
    renderer.measures = {}
    result = CreativeAgent(llm, engine, renderer, tmp_path).run(draft, brand, max_rounds=1)
    assert result.passed and len(result.rounds) == 1

    vision = [r for r in client.requests if "creative director" in r["system"]]
    images = [b for b in vision[0]["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 3 and images[0]["source"]["media_type"] == "image/png"
    assert "subcopy: 24px text" in vision[0]["messages"][0]["content"][-1]["text"]


def test_loop_stops_at_three_rounds_and_keeps_failures_for_the_reviewer(tmp_path, engine):
    brand = kitted_brand(tmp_path)
    draft = add_draft(engine)
    weak = {"square": {"thumb_stopping": 6}}
    llm, client = llm_with(engine, slots(), review(**weak), slots(), review(**weak), slots(),
                           review(**weak))  # fmt: skip
    result = CreativeAgent(llm, engine, FakeRenderer(), tmp_path).run(draft, brand)
    assert [r.round for r in result.rounds] == [1, 2, 3] and not result.passed
    fills = [r for r in client.requests if "fill its slots" in r["system"]]
    assert "Fixes to apply" in fills[1]["messages"][0]["content"]
    assert "[headline] long: shorten" in fills[1]["messages"][0]["content"]
    with Session(engine) as session:
        rows = session.exec(select(Creative)).all()
    assert len(rows) == 9 and not any(row.passed for row in rows)
    assert (tmp_path / rows[-1].path).read_bytes() == PNG
    view = latest(engine, draft.id)
    assert view["rounds"] == 3 and len(view["sizes"]) == 3 and view["stale"] is False
    assert [h["lowest"] for h in view["history"]] == [6, 6, 6]


def test_owner_alt_text_wins_and_bad_alt_text_blocks_a_pass(tmp_path, engine):
    draft = add_draft(engine)
    llm, _ = llm_with(engine, slots(), review())
    result = CreativeAgent(llm, engine, FakeRenderer(), tmp_path).run(
        draft, kitted_brand(tmp_path, alt="loaf"), max_rounds=1
    )
    assert not result.passed and result.rounds[0].blocked
    assert result.rounds[0].slots.alt_text == "loaf"


def test_words_on_the_image_go_through_the_guardrails(tmp_path, engine):
    draft = add_draft(engine)
    invented = slots("Loved by 98% of our customers")
    llm, client = llm_with(engine, invented, slots(), review())
    renderer = FakeRenderer()
    result = CreativeAgent(llm, engine, renderer, tmp_path).run(draft, kitted_brand(tmp_path),
                                                                 max_rounds=2)  # fmt: skip
    first, second = result.rounds
    assert first.blocked and first.scores == {} and second.passed
    assert len(renderer.html) == 3  # nothing was rendered for the blocked round
    with Session(engine) as session:
        assert session.exec(select(GuardrailBlock)).first().stage == "creative"


def test_only_ad_drafts_get_images(tmp_path, engine):
    draft = add_draft(engine, HERO_TEXT, kind="landing_hero")
    with pytest.raises(ValueError):
        CreativeAgent(llm_with(engine)[0], engine, FakeRenderer(), tmp_path).run(draft, BRAND)


# --- AI imagery ---


class FakeImages:
    def generate(self, prompt):
        from growthcrew.creative.images import GeneratedImage

        return GeneratedImage(png=PNG, model="fake-image-1", prompt=prompt, created_at="2026-10-05")


def test_generated_backgrounds_are_labelled(tmp_path, engine):
    draft = add_draft(engine)
    llm, _ = llm_with(engine, slots(), review())
    result = CreativeAgent(llm, engine, FakeRenderer(), tmp_path, images=FakeImages()).run(
        draft, BRAND, max_rounds=1
    )
    assert result.ai_generated
    with Session(engine) as session:
        row = session.exec(select(Creative)).first()
    assert row.ai_generated and json.loads(row.image_meta)["model"] == "fake-image-1"
    [meta] = (tmp_path / "acme" / "creative" / str(draft.id)).glob("ai-*.json")
    assert json.loads(meta.read_text())["ai_generated"] is True


def test_openai_images_adapter_and_prompt():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body["n"] == 1 and "No text" in body["prompt"]
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    image = OpenAIImages(client=client, api_key="k").generate(
        background_prompt("sourdough", ("warm light",))
    )
    assert image.png == PNG and image.ai_generated


# --- landing pages ---


def test_landing_hero_text_becomes_escaped_html(tmp_path, engine):
    draft = add_draft(engine, HERO_TEXT, kind="landing_hero", angle="outcome")
    hero = parse_hero(draft.text)
    assert hero.supporting_points == ["Cancel any time", "Local flour"]
    html, problems = landing_html(draft, kitted_brand(tmp_path), tmp_path)
    assert "&lt;b&gt;delivered&lt;/b&gt;" in html and 'data-variant="outcome"' in html
    assert 'alt="A sourdough loaf on a wooden board, cut open"' in html and problems == []
    with pytest.raises(ValueError):
        parse_hero("Point: only a point")


def test_landing_export_writes_only_approved_variants(tmp_path, engine):
    brand = kitted_brand(tmp_path)
    first = add_draft(engine, HERO_TEXT, "landing_hero", "pending_approval", "outcome",
                      "04-day04-landing_hero-outcome")  # fmt: skip
    add_draft(engine, HERO_TEXT, "landing_hero", "approved", "pain", "04-day04-landing_hero-pain")
    add_draft(engine, HERO_TEXT, "landing_hero", "rejected", "social_proof",
              "04-day04-landing_hero-social_proof")  # fmt: skip
    paths = export_variants(engine, first.id, brand, tmp_path)
    assert [p.name for p in paths] == ["pain.html"]
    only = add_draft(engine, HERO_TEXT, "landing_hero", "pending_approval", piece="09-x")
    with pytest.raises(ApprovalRequired):
        export_variants(engine, only.id, brand, tmp_path)


# --- the real browser ---

needs_browser = pytest.mark.skipif(
    not chromium_path() and not os.getenv("GROWTHCREW_REQUIRE_BROWSER"),
    reason="no Chromium for Playwright on this machine",
)


@needs_browser
def test_real_render_measures_text_and_blocks_the_network(tmp_path):
    from growthcrew.creative.render import SIZES, renderer

    kit = load_kit(kitted_brand(tmp_path), tmp_path)
    tracker = '<img src="http://127.0.0.1:9/pixel.png">'
    with renderer() as browser:
        html = render_html(kit, slots(), "square", "Acme").replace("</body>", tracker + "</body>")
        png, measure = browser.render(html, *SIZES["square"][:2])
        long = slots(" ".join(["Sourdough"] * 30))
        _, clipped = browser.render(render_html(kit, long, "story", "Acme"), *SIZES["story"][:2])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (
        measure["slots"]["headline"]["font_px"] == 84
        and not measure["slots"]["headline"]["clipped"]
    )
    assert 0 < measure["text_share"] < 0.5 and measure["words"] == 12
    assert clipped["slots"]["headline"]["clipped"]


# --- API ---


@pytest.fixture
def client(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(store.load_brain, "__defaults__", (None, tmp_path))
    store.save_brain(kitted_brand(tmp_path), note="t", root=tmp_path, engine=engine)
    app.dependency_overrides[engine_dep] = lambda: engine
    yield TestClient(app)
    app.dependency_overrides.pop(engine_dep, None)


def test_creative_routes(client, engine, tmp_path):
    draft = add_draft(engine)
    assert client.get(f"/drafts/{draft.id}/creatives").json()["rounds"] == 0
    llm, _ = llm_with(engine, slots(), review())
    CreativeAgent(llm, engine, FakeRenderer(), tmp_path).run(draft, kitted_brand(tmp_path), 1)
    data = client.get(f"/drafts/{draft.id}/creatives").json()
    assert data["passed"] and [s["ratio"] for s in data["sizes"]] == ["1:1", "4:5", "9:16"]
    image = client.get(data["sizes"][0]["url"])
    assert image.status_code == 200 and image.content == PNG
    # Downloading for use needs an approved ad.
    assert client.get(f"/drafts/{draft.id}/creatives/square.png?download=1").status_code == 409
    assert client.get(f"/drafts/{draft.id}/creatives/huge.png").status_code == 404

    hero = add_draft(engine, HERO_TEXT, kind="landing_hero", angle="pain")
    page = client.get(f"/drafts/{hero.id}/landing.html")
    assert page.status_code == 200 and "sandbox" in page.headers["content-security-policy"]
    assert client.post(f"/drafts/{hero.id}/landing/export").status_code == 409
    assert client.post(f"/drafts/{draft.id}/creatives").status_code in (202, 503)


def test_brand_images_are_uploaded_by_signature_and_saved_as_a_new_version(client, tmp_path):
    from growthcrew.brain.store import list_versions, load_brain

    before = list_versions("acme", tmp_path)
    url = "/workspaces/acme/brand/images?name=../../etc/My Loaf!.png&alt=A loaf on a board"
    added = client.post(url, content=PNG)
    assert added.status_code == 200 and added.json()["file"] == "MyLoaf.png"
    assert (tmp_path / "acme" / "brand" / "MyLoaf.png").read_bytes() == PNG
    brain = load_brain("acme", root=tmp_path)
    assert brain.brand_kit.product_images[-1].alt == "A loaf on a board"
    assert brain.fields["brand_kit"].status == "confirmed"
    assert len(list_versions("acme", tmp_path)) == len(before) + 1
    fake = client.post("/workspaces/acme/brand/images?name=x.png", content=b"<svg onload=x>")
    assert fake.status_code == 400


def test_the_critic_sees_phone_sized_images():
    import io

    from PIL import Image

    from growthcrew.creative.agent import shrink

    out = io.BytesIO()
    Image.new("RGB", (1080, 1920), "white").save(out, format="PNG")
    small = Image.open(io.BytesIO(shrink(out.getvalue())))
    assert small.size == (540, 960)
    assert shrink(PNG) == PNG  # already small
