from pydantic import BaseModel

from growthcrew.frameworks.base import Framework, Point


class Pillar(BaseModel):
    message: str
    support: list[str]
    proof_points: list[Point]


class MessagingHouse(BaseModel):
    core_message: Point
    pillars: list[Pillar]


MESSAGING_HOUSE = Framework(
    key="messaging_house",
    name="Messaging house",
    purpose="Give every writer the same one core message, three supporting pillars and the "
    "proof behind each.",
    inputs=(
        "The positioning and jobs-to-be-done you just wrote",
        "Brain: proof (case studies, testimonials, stats) and brand voice",
        "Customer quote themes",
    ),
    output_model=MessagingHouse,
    quality_criteria=(
        "There is exactly one core message and exactly three pillars.",
        "The core message is a single sentence a customer could repeat.",
        "Each pillar maps to a value point in the positioning.",
        "Every proof point cites real proof from the brain or a research claim. If a pillar "
        "has no proof, its proof_points are empty and that gap is visible.",
        "The wording follows the brand voice guide.",
    ),
    instructions="Write the core message first, then three pillars that together justify it. "
    "Under each pillar list its proof points. Use only proof that exists in the evidence; do "
    "not write a proof point the business would have to go and create.",
)
