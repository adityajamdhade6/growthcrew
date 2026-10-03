"""The brand brain: what GrowthCrew knows about one client."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from growthcrew.schemas import Claim

Status = Literal["inferred", "confirmed"]
Confidence = Literal["low", "medium", "high"]
Stage = Literal["unknown", "pre-launch", "early", "growth", "established"]


class BusinessProfile(BaseModel):
    name: str = ""
    one_liner: str = ""
    what_they_sell: str = ""
    pricing: str = ""
    geography: str = ""
    stage: Stage = "unknown"


class ICP(BaseModel):
    audience_type: Literal["unknown", "b2b", "b2c", "both"] = "unknown"
    # B2B buyers: industry, company size, buyer role. Leave empty for pure B2C.
    firmographics: list[str] = []
    # Consumers: age, income, lifestyle. Leave empty for pure B2B.
    demographics: list[str] = []
    pains: list[str] = []
    goals: list[str] = []
    buying_triggers: list[str] = []
    objections: list[str] = []


class ToneSliders(BaseModel):
    """Each slider runs from 1 to 5."""

    formality: int = Field(3, description="1 = casual, 5 = formal")
    playfulness: int = Field(3, description="1 = serious, 5 = playful")
    enthusiasm: int = Field(3, description="1 = matter-of-fact, 5 = enthusiastic")
    technicality: int = Field(3, description="1 = plain language, 5 = expert jargon")

    @field_validator("*")
    @classmethod
    def _clamp(cls, value: int) -> int:
        return min(5, max(1, value))


class VoiceGuide(BaseModel):
    sentence_length: str = ""
    jargon_level: Literal["unknown", "none", "light", "moderate", "heavy"] = "unknown"
    banned_phrases: list[str] = []
    rules: list[str] = []


class BrandVoice(BaseModel):
    tone: ToneSliders = ToneSliders()
    do_words: list[str] = []
    dont_words: list[str] = []
    example_passages: list[str] = []
    guide: VoiceGuide = VoiceGuide()
    # Measured from the samples in code, not estimated by the model.
    avg_sentence_words: float | None = None


class Product(BaseModel):
    name: str
    description: str = ""
    price: str = ""
    url: str = ""


class ProofItem(BaseModel):
    """A case study, testimonial or stat. `quote` must appear verbatim at `source_url`."""

    summary: str
    quote: str
    attribution: str = ""
    source_url: str


class Proof(BaseModel):
    case_studies: list[ProofItem] = []
    testimonials: list[ProofItem] = []
    stats: list[ProofItem] = []


class Competitor(BaseModel):
    name: str
    url: str = ""


class FieldMeta(BaseModel):
    status: Status = "inferred"
    confidence: Confidence = "low"
    source_urls: list[str] = []
    note: str = ""


class Brain(BaseModel):
    workspace: str
    version: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_url: str = ""
    pages_crawled: list[str] = []
    business: BusinessProfile = BusinessProfile()
    icp: ICP = ICP()
    voice: BrandVoice = BrandVoice()
    products: list[Product] = []
    proof: Proof = Proof()
    competitors: list[Competitor] = []
    # Keyed by the paths in FIELD_PATHS.
    fields: dict[str, FieldMeta] = {}

    def get(self, path: str):
        value = self
        for part in path.split("."):
            value = getattr(value, part)
        return value

    def is_empty(self, path: str) -> bool:
        value = self.get(path)
        if path == "voice.tone":
            return not self.voice.example_passages and not self.voice.guide.rules
        if path == "voice.guide":
            return not value.rules
        return value in ("", [], "unknown")

    def weakest(self) -> list[tuple[str, str]]:
        """Fields a human should look at first: empty ones, then low-confidence inferences."""
        empty, low = [], []
        for path in FIELD_PATHS:
            meta = self.fields.get(path, FieldMeta())
            if meta.status == "confirmed":
                continue
            if self.is_empty(path):
                empty.append((path, meta.note or "nothing found"))
            elif meta.confidence == "low":
                low.append((path, meta.note or "low confidence"))
        return empty + low

    def cited_proof(self) -> list[Claim]:
        proof = self.proof
        return [
            Claim(statement=item.summary, source_url=item.source_url)
            for item in proof.case_studies + proof.testimonials + proof.stats
        ]


FIELD_PATHS: tuple[str, ...] = (
    *(f"business.{name}" for name in BusinessProfile.model_fields),
    *(f"icp.{name}" for name in ICP.model_fields),
    "voice.tone",
    "voice.do_words",
    "voice.dont_words",
    "voice.example_passages",
    "voice.guide",
    "products",
    "proof.case_studies",
    "proof.testimonials",
    "proof.stats",
    "competitors",
)
