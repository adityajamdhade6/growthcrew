"""Versions for prompts and strategies, so performance can be compared across changes."""

import hashlib
from pathlib import Path


def prompt_version(prompt: str) -> str:
    """A short, stable id for a prompt's text. Any edit to the prompt changes it."""
    return "p-" + hashlib.sha256(prompt.encode()).hexdigest()[:8]


def strategy_version(workspace: str, root: Path) -> int:
    """How many strategies this workspace has had; the latest one's number."""
    return len(list((root / workspace / "strategy").glob("*.json")))
