"""Content types: the body schema for each kind of piece, and how it reads as plain lines."""

from typing import Literal

from pydantic import BaseModel

ContentType = Literal[
    "linkedin_post",
    "x_thread",
    "blog_article",
    "cold_email_sequence",
    "ad",
    "landing_hero",
    "newsletter",
]
FunnelStage = Literal["awareness", "consideration", "conversion", "retention"]
Angle = Literal["pain", "outcome", "social_proof"]
ANGLES: tuple[Angle, ...] = ("pain", "outcome", "social_proof")


def _lines(*parts: str) -> list[str]:
    return [line for part in parts for line in part.splitlines() if line.strip()]


class LinkedInPost(BaseModel):
    hook: str
    body: str
    hashtags: list[str]

    def lines(self) -> list[str]:
        return _lines(self.hook, self.body, " ".join(self.hashtags))


class XThread(BaseModel):
    tweets: list[str]

    def lines(self) -> list[str]:
        return [f"{n}/ {tweet}" for n, tweet in enumerate(self.tweets, 1)]


class SEOBrief(BaseModel):
    primary_keyword: str
    secondary_keywords: list[str]
    search_intent: str
    title_tag: str
    meta_description: str


class OutlineSection(BaseModel):
    heading: str
    points: list[str]


class Outline(BaseModel):
    sections: list[OutlineSection]


class BlogArticle(BaseModel):
    title: str
    article_markdown: str

    def lines(self) -> list[str]:
        return _lines(f"# {self.title}", self.article_markdown)


class Email(BaseModel):
    send_day: int
    subject: str
    body: str


class ColdEmailSequence(BaseModel):
    emails: list[Email]

    def lines(self) -> list[str]:
        out: list[str] = []
        for number, email in enumerate(self.emails, 1):
            out += _lines(
                f"Email {number} (day {email.send_day}) subject: {email.subject}", email.body
            )
        return out


class Ad(BaseModel):
    platform: Literal["meta", "google"]
    hook: str
    primary_text: str
    headline: str

    def lines(self) -> list[str]:
        return _lines(
            f"Hook: {self.hook}", f"Primary text: {self.primary_text}", f"Headline: {self.headline}"
        )


class LandingHero(BaseModel):
    headline: str
    subheadline: str
    primary_cta: str
    supporting_points: list[str]
    social_proof_line: str

    def lines(self) -> list[str]:
        return _lines(
            f"Headline: {self.headline}",
            f"Subheadline: {self.subheadline}",
            f"CTA button: {self.primary_cta}",
            *(f"Point: {point}" for point in self.supporting_points),
            f"Social proof: {self.social_proof_line}" if self.social_proof_line else "",
        )


class Newsletter(BaseModel):
    subject: str
    preview_text: str
    body_markdown: str

    def lines(self) -> list[str]:
        return _lines(
            f"Subject: {self.subject}", f"Preview: {self.preview_text}", self.body_markdown
        )


class ContentRequest(BaseModel):
    content_type: ContentType
    # The messaging-house pillar this piece should serve.
    pillar: str
    audience: str
    funnel_stage: FunnelStage
    goal: str
    topic: str = ""
    # For ads only.
    platform: Literal["meta", "google"] = "meta"
    # None means "use the default for this content type".
    ab_test: bool | None = None
    # The angle for a single (not A/B-tested) piece. Set when learnings favour one angle.
    angle: Angle | None = None


class PieceMetadata(BaseModel):
    messaging_pillar: str
    target_persona: str
    cta: str
    hypothesis: str
