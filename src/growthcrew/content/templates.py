"""One template per content type: schema, best practices, and platform limits.

X's 280 characters, LinkedIn's 3,000 and Google's 30/90 are hard platform limits. The others
are working conventions; change them here if a client's data says otherwise.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from growthcrew.content.types import (
    Ad,
    BlogArticle,
    ColdEmailSequence,
    LandingHero,
    LinkedInPost,
    Newsletter,
    XThread,
)


def _words(text: str) -> int:
    return len(text.split())


def _over(label: str, text: str, limit: int, unit: str = "characters") -> list[str]:
    size = len(text) if unit == "characters" else _words(text)
    return [f"{label} is {size} {unit}; the limit is {limit}"] if size > limit else []


def _linkedin(post: LinkedInPost) -> list[str]:
    return [
        *_over("Post", "\n".join(post.lines()), 3000),
        *_over("Hook", post.hook, 210),
        *([f"{len(post.hashtags)} hashtags; use at most 5"] if len(post.hashtags) > 5 else []),
    ]


def _x_thread(thread: XThread) -> list[str]:
    issues = [
        issue
        for number, tweet in enumerate(thread.tweets, 1)
        for issue in _over(f"Tweet {number}", tweet, 280)
    ]
    if not 3 <= len(thread.tweets) <= 12:
        issues.append(f"Thread has {len(thread.tweets)} tweets; use 3 to 12")
    return issues


def _blog(article: BlogArticle) -> list[str]:
    words = _words(article.article_markdown)
    return [
        *_over("Title", article.title, 60),
        *([f"Article is {words} words; write at least 600"] if words < 600 else []),
    ]


def _cold_email(sequence: ColdEmailSequence) -> list[str]:
    issues = (
        []
        if len(sequence.emails) == 3
        else [f"Sequence has {len(sequence.emails)} emails; it must have 3"]
    )
    for number, email in enumerate(sequence.emails, 1):
        issues += _over(f"Email {number} subject", email.subject, 60)
        issues += _over(f"Email {number} body", email.body, 150, "words")
    return issues


def _ad(ad: Ad) -> list[str]:
    headline, text = (30, 90) if ad.platform == "google" else (40, 125)
    return [
        *_over(f"{ad.platform} headline", ad.headline, headline),
        *_over(f"{ad.platform} primary text", ad.primary_text, text),
        *_over("Hook", ad.hook, 80),
    ]


def _hero(hero: LandingHero) -> list[str]:
    return [
        *_over("Headline", hero.headline, 12, "words"),
        *_over("Subheadline", hero.subheadline, 30, "words"),
        *_over("CTA button", hero.primary_cta, 5, "words"),
    ]


def _newsletter(letter: Newsletter) -> list[str]:
    return [
        *_over("Subject", letter.subject, 60),
        *_over("Preview text", letter.preview_text, 100),
        *_over("Body", letter.body_markdown, 700, "words"),
    ]


@dataclass(frozen=True)
class ContentTemplate:
    key: str
    name: str
    body_model: type[BaseModel]
    format: str
    best_practices: tuple[str, ...]
    limits: Callable[..., list[str]]
    # Whether this type is A/B tested by default, which means three variants by angle.
    ab_tested: bool = False

    def prompt(self) -> str:
        practices = "\n".join(f"- {item}" for item in self.best_practices)
        return f"Content type: {self.name}\n\nFormat: {self.format}\n\nBest practices:\n{practices}"


TEMPLATES: dict[str, ContentTemplate] = {
    template.key: template
    for template in (
        ContentTemplate(
            "linkedin_post",
            "LinkedIn post",
            LinkedInPost,
            "A hook of up to 210 characters (what shows before 'see more'), a body in short "
            "paragraphs, and up to 5 hashtags. 3,000 characters at most.",
            (
                "The hook states a specific problem, number or opinion; it does not announce "
                "that a post is coming.",
                "One idea per post, told through a concrete example or observation.",
                "Short paragraphs with white space; no walls of text.",
                "End with one question or one call to action, not both.",
                "Put any link in the CTA line, not in the hook.",
            ),
            _linkedin,
        ),
        ContentTemplate(
            "x_thread",
            "X thread",
            XThread,
            "3 to 12 tweets of at most 280 characters each. Do not number them; numbering "
            "is added automatically.",
            (
                "Tweet 1 must stand alone and make the reader want the rest.",
                "One point per tweet, each readable out of context.",
                "Use specifics (a number, a name, an example) in most tweets.",
                "The last tweet restates the takeaway and carries the single CTA.",
            ),
            _x_thread,
        ),
        ContentTemplate(
            "blog_article",
            "Blog article",
            BlogArticle,
            "A title of at most 60 characters and the article in Markdown with H2/H3 "
            "headings, following the SEO brief and outline you wrote. At least 600 words.",
            (
                "Answer the search intent in the first 100 words.",
                "Use the primary keyword in the title, the first paragraph and one H2, and "
                "nowhere it reads unnaturally.",
                "Each section makes one point and backs it with an example or evidence.",
                "Prefer short sentences and plain words over filler transitions.",
                "Close with one next step for the reader that matches the funnel stage.",
            ),
            _blog,
        ),
        ContentTemplate(
            "cold_email_sequence",
            "Cold email sequence (3 steps)",
            ColdEmailSequence,
            "Exactly 3 emails with send_day, subject (at most 60 characters) and a plain-text "
            "body of at most 150 words each.",
            (
                "Email 1 opens with something true about the recipient's situation, not about "
                "the sender.",
                "One ask per email, small enough to answer in a line.",
                "Email 2 adds a new reason or piece of proof; it does not 'bump' or 'circle back'.",
                "Email 3 is a short, polite close that makes it easy to say no.",
                "No images, no more than one link, no marketing formatting.",
            ),
            _cold_email,
            ab_tested=True,
        ),
        ContentTemplate(
            "ad",
            "Meta or Google ad",
            Ad,
            "A hook (the opening line or on-image text, at most 80 characters), primary text "
            "and a headline. Meta: headline up to 40 characters, primary text up to 125. "
            "Google: headline up to 30, description (primary text) up to 90.",
            (
                "The hook names the customer's situation in their own words.",
                "Lead with the benefit; mention a feature only as the reason to believe it.",
                "One offer and one action per ad.",
                "The headline says what happens when they click.",
            ),
            _ad,
            ab_tested=True,
        ),
        ContentTemplate(
            "landing_hero",
            "Landing page hero section",
            LandingHero,
            "A headline of at most 12 words, a subheadline of at most 30, a CTA button label "
            "of at most 5 words, 2 to 3 supporting points, and a social proof line (leave it "
            "empty if the brain has no proof).",
            (
                "The headline states the outcome for the visitor, matching the ad or post that "
                "sent them.",
                "The subheadline says who it is for and how it works, in one sentence.",
                "The CTA label describes what they get, not 'Submit' or 'Learn more'.",
                "Supporting points answer the top objections from the research.",
            ),
            _hero,
            ab_tested=True,
        ),
        ContentTemplate(
            "newsletter",
            "Email newsletter",
            Newsletter,
            "A subject of at most 60 characters, preview text of at most 100, and a Markdown "
            "body of at most 700 words.",
            (
                "The subject is specific about what is inside; no clickbait.",
                "The preview text adds to the subject instead of repeating it.",
                "Open with the most useful thing, written as one person to another.",
                "One main story and at most two short secondary items.",
                "One primary CTA, repeated at most once.",
            ),
            _newsletter,
        ),
    )
}
