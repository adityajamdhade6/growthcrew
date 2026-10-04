"""Simulated performance data for testing the analyst: a week in which one angle wins.

Seeds a cycle of approved, published pieces and returns CSV exports shaped like the real ones.
Rates are set here, with a little seeded noise, so the expected findings are known:
- LinkedIn posts: the outcome angle gets about twice the click-through of pain or social proof.
- A Meta ad A/B test with large samples: outcome wins.
- A landing page hero A/B test with about 40 sessions per variant: far too small to call,
  even though one variant looks twice as good.
- Search Console: one day with a traffic spike.
"""

import json
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from growthcrew.analytics import registry
from growthcrew.db.models import CalendarItem, Cycle, Draft
from growthcrew.experiments import GuardrailSpec, preregister

WEEK_END = datetime(2026, 9, 27, tzinfo=UTC)
LINKEDIN_CTR = {"pain": 0.020, "outcome": 0.045, "social_proof": 0.022}
AD_CTR = {"pain": 0.011, "outcome": 0.019, "social_proof": 0.012}
# Looks like a 2x win for pain, on a sample too small to mean anything.
HERO = {"pain": (41, 4), "outcome": (38, 2), "social_proof": (40, 2)}


def seed(engine: Engine, workspace: str = "acme", rng_seed: int = 7) -> dict[str, str]:
    """Create the published pieces and return {source: csv text} to upload."""
    rng = random.Random(rng_seed)
    week_start = WEEK_END - timedelta(days=6)

    def noisy(trials: int, rate: float) -> int:
        return round(trials * rate * rng.uniform(0.93, 1.07))

    pieces = []  # (piece_id, content_type, angle, pillar)
    for number, angle in enumerate(["pain", "outcome", "social_proof"] * 2, 1):
        pillar = "Fresh every morning" if number <= 3 else "No trip to the shop"
        pieces.append(
            (f"{number:02d}-day{number:02d}-linkedin_post", "linkedin_post", angle, pillar)
        )
    for angle in AD_CTR:
        pieces.append((f"07-day03-ad-{angle}", "ad", angle, "Fresh every morning"))
    for angle in HERO:
        pieces.append(
            (f"08-day04-landing_hero-{angle}", "landing_hero", angle, "No trip to the shop")
        )
    pieces.append(("09-day05-newsletter", "newsletter", None, "Fresh every morning"))

    drafts: list[Draft] = []
    with Session(engine, expire_on_commit=False) as session:
        cycle = Cycle(workspace=workspace, week_start=week_start, stage="published")
        session.add(cycle)
        session.flush()
        for piece_id, content_type, angle, pillar in pieces:
            text = f"Simulated {content_type} for {piece_id}, written to test the analyst."
            draft = Draft(
                cycle_id=cycle.id, workspace=workspace, piece_id=piece_id,
                content_type=content_type, angle=angle, day=int(piece_id[6:8]),
                original_text=text, text=text, body_json="{}",
                metadata_json=json.dumps({
                    "messaging_pillar": pillar, "target_persona": "Busy households",
                    "cta": "Start a trial",
                    "hypothesis": f"If we lead with {angle or 'news'}, then clicks will rise",
                }),
                min_score=8, passed_critic=True, status="approved",
            )  # fmt: skip
            session.add(draft)
            session.flush()
            session.add(
                CalendarItem(
                    draft_id=draft.id,
                    cycle_id=cycle.id,
                    workspace=workspace,
                    scheduled_for=week_start + timedelta(days=draft.day - 1),
                    channel=content_type,
                    status="published",
                    published_via="manual",
                    published_by="simulation",
                    published_url=f"https://www.linkedin.com/posts/acme-{draft.id}"
                    if content_type == "linkedin_post"
                    else None,
                )  # fmt: skip
            )
            drafts.append(draft)
        session.commit()
        cycle_id = cycle.id

    # Both A/B tests were registered before they ran: what is being tested, on which metric,
    # and how much data is needed.
    registry.register(engine, workspace, cycle_id, preregister(
        experiment="07-day03-ad", primary_metric="click-through rate",
        hypothesis="If the ad leads with the outcome, then click-through will beat the pain and "
        "social-proof hooks because it is the most quoted benefit",
        variants=list(AD_CTR), baseline_rate=0.012, minimum_detectable_effect=0.4,
        guardrails=[GuardrailSpec(metric="cost per click")],
    ))  # fmt: skip
    registry.register(engine, workspace, cycle_id, preregister(
        experiment="08-day04-landing_hero", primary_metric="conversion rate",
        hypothesis="If the hero leads with the pain, then sign-ups will rise because visitors "
        "arrive with that problem in mind",
        variants=list(HERO), baseline_rate=0.05, minimum_detectable_effect=0.3,
    ))  # fmt: skip

    linkedin = ["Post title,Post link,Created date,Impressions,Clicks,Reactions"]
    ads = ["Reporting starts,Ad name,Impressions,Link clicks,Amount spent (USD)"]
    ga4 = ["Session manual ad content,Sessions,Key events"]
    email = ["Campaign,Sent,Opens,Clicks"]
    for draft in drafts:
        if draft.content_type == "linkedin_post":
            impressions = rng.randint(2600, 3400)
            created = (week_start + timedelta(days=draft.day - 1)).strftime("%m/%d/%Y")
            linkedin.append(
                f"Post {draft.id},https://www.linkedin.com/posts/acme-{draft.id},{created},"
                f"{impressions},{noisy(impressions, LINKEDIN_CTR[draft.angle])},"
                f"{rng.randint(10, 60)}"
            )
        elif draft.content_type == "ad":
            for day in range(7):
                impressions = rng.randint(1900, 2300)
                date = (week_start + timedelta(days=day)).strftime("%Y-%m-%d")
                ads.append(
                    f"{date},{draft.tracking_key},{impressions},"
                    f"{noisy(impressions, AD_CTR[draft.angle])},{rng.uniform(8, 12):.2f}"
                )
        elif draft.content_type == "landing_hero":
            sessions, conversions = HERO[draft.angle]
            ga4.append(f"{draft.tracking_key},{sessions},{conversions}")
        elif draft.content_type == "newsletter":
            email.append(f"Weekly letter {draft.tracking_key},1200,540,66")
    # A campaign nobody tagged: it cannot be matched to a piece.
    email.append("Black Friday teaser,800,300,20")

    gsc = ["Date,Top pages,Clicks,Impressions"]
    for day in range(14):
        date = WEEK_END - timedelta(days=13 - day)
        impressions = rng.randint(380, 440) * (4 if day == 10 else 1)
        gsc.append(
            f"{date:%Y-%m-%d},https://acme.test/blog/bread,{noisy(impressions, 0.03)},{impressions}"
        )
    return {
        "linkedin": "\n".join(linkedin),
        "ads": "\n".join(ads),
        "ga4": "\n".join(ga4),
        "email": "\n".join(email),
        "gsc": "\n".join(gsc),
    }
