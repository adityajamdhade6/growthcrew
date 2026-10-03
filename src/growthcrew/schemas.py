"""Types shared across agents."""

from enum import StrEnum

from pydantic import BaseModel, field_validator


class Claim(BaseModel):
    """A statement about a market or competitor. It must carry its source."""

    statement: str
    source_url: str

    @field_validator("source_url")
    @classmethod
    def _must_be_http_url(cls, value: str) -> str:
        # workspace:// points at a review export a human added to the workspace.
        if not value.startswith(("http://", "https://", "workspace://")):
            raise ValueError("source_url must be an http(s) or workspace URL")
        return value


class ApprovalStatus(StrEnum):
    PENDING = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
