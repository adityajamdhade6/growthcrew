"""A marketing framework as a structured prompt template."""

from dataclasses import dataclass

from pydantic import BaseModel

from growthcrew.frameworks.loader import render


class Point(BaseModel):
    """One recommendation or finding, with the evidence IDs that support it."""

    text: str
    support: list[str]


@dataclass(frozen=True)
class Framework:
    key: str
    name: str
    purpose: str
    inputs: tuple[str, ...]
    output_model: type[BaseModel]
    quality_criteria: tuple[str, ...]
    instructions: str

    def prompt(self) -> str:
        return render("framework", fw=self)
