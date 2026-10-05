"""Optional AI imagery for ad backgrounds, behind a config flag.

Off by default. When `GROWTHCREW_AI_IMAGES=1` and `OPENAI_API_KEY` are set, an ad without a
product photo gets a generated background. The prompt is built in code from the brand kit's
image style rules, never from fetched text, and asks for no text, logos or people's faces.
Every generated image is recorded as AI-generated with its model, prompt and date, and the
review panel says so.
"""

import base64
from datetime import UTC, datetime
from typing import Protocol

import httpx
from pydantic import BaseModel

from growthcrew import config

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"


class GeneratedImage(BaseModel):
    png: bytes
    model: str
    prompt: str
    created_at: str
    ai_generated: bool = True


class ImageGenerator(Protocol):
    def generate(self, prompt: str) -> GeneratedImage: ...


class ImagesNotConfigured(RuntimeError):
    pass


class OpenAIImages:
    """OpenAI's Images API. Swap providers by writing another class with `generate`."""

    def __init__(self, client: httpx.Client | None = None, api_key: str | None = None) -> None:
        self.client = client or httpx.Client(timeout=120.0)
        self.api_key = api_key if api_key is not None else config.IMAGE_API_KEY
        if not self.api_key:
            raise ImagesNotConfigured("OPENAI_API_KEY is not set, so AI images are unavailable")

    def generate(self, prompt: str) -> GeneratedImage:
        response = self.client.post(
            OPENAI_IMAGES_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": config.IMAGE_MODEL, "prompt": prompt, "size": "1024x1024", "n": 1},
        )
        response.raise_for_status()
        data = response.json()["data"][0]["b64_json"]
        return GeneratedImage(
            png=base64.b64decode(data),
            model=config.IMAGE_MODEL,
            prompt=prompt,
            created_at=datetime.now(UTC).isoformat(),
        )


def background_prompt(what_they_sell: str, style_rules: tuple[str, ...]) -> str:
    rules = "; ".join(style_rules) or "natural light, uncluttered, calm colours"
    return (
        f"A background photograph for an ad for {what_they_sell or 'a small business'}. "
        f"Style: {rules}. No text, no lettering, no logos, no recognisable faces. Leave the "
        "lower third plain so text can sit on it."
    )


def generator() -> ImageGenerator | None:
    """The configured image generator, or None when AI imagery is switched off."""
    return OpenAIImages() if config.AI_IMAGES else None
