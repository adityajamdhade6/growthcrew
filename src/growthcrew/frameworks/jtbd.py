from pydantic import BaseModel

from growthcrew.frameworks.base import Framework


class Job(BaseModel):
    # "When <situation>, I want to <motivation>, so I can <outcome>."
    statement: str
    support: list[str]


class JobsToBeDone(BaseModel):
    functional_jobs: list[Job]
    emotional_jobs: list[Job]
    social_jobs: list[Job]


JTBD = Framework(
    key="jtbd",
    name="Jobs-to-be-done",
    purpose="Describe what customers are trying to get done, so messaging speaks to their "
    "progress rather than to product features.",
    inputs=(
        "Voice-of-customer evidence: pains, desired outcomes, objections, quote themes",
        "Brain: ICP pains, goals and buying triggers",
    ),
    output_model=JobsToBeDone,
    quality_criteria=(
        "Each job is written as 'When <situation>, I want to <motivation>, so I can <outcome>'.",
        "Jobs describe the customer's situation and never mention the brand's product.",
        "Functional, emotional and social jobs are distinct, not the same job reworded.",
        "Each job cites customer evidence; a job resting only on an inferred brain field says so.",
    ),
    instructions="Write 2-4 functional jobs (the task to get done), 1-3 emotional jobs (how "
    "they want to feel or stop feeling) and 1-2 social jobs (how they want to be seen by "
    "others). Prefer the customers' own words from the quote themes. If the evidence does not "
    "support a social job, return an empty list rather than inventing one.",
)
