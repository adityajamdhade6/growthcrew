from pydantic import BaseModel

from growthcrew.frameworks.base import Framework, Point


class Positioning(BaseModel):
    competitive_alternatives: list[Point]
    unique_attributes: list[Point]
    value: list[Point]
    target_customers: list[Point]
    market_category: Point
    positioning_statement: str


POSITIONING = Framework(
    key="positioning",
    name="Positioning (April Dunford's five components)",
    purpose="Define the context in which this brand is the obvious choice for a specific customer.",
    inputs=(
        "Competitor teardowns and the research learnings",
        "Brain: what the business sells, products, proof, ICP",
        "The jobs-to-be-done you just wrote",
    ),
    output_model=Positioning,
    quality_criteria=(
        "Components follow Dunford's order: alternatives, then attributes the alternatives "
        "lack, then the value those attributes enable, then who cares most, then the category.",
        "Competitive alternatives include what customers do today (spreadsheets, an agency, "
        "nothing), not only named competitors.",
        "Each unique attribute is a capability the alternatives demonstrably lack, not an "
        "adjective like 'better' or 'easier'.",
        "Each value point traces to a named unique attribute.",
        "Target customers are narrow enough to exclude someone.",
        "The market category is one a buyer would actually search for.",
    ),
    instructions="Work through the five components in order, since each depends on the one "
    "before. Then write a one-to-two sentence positioning_statement that combines them. Cite "
    "competitor claims for the alternatives and brain facts or proof for the unique attributes.",
)
