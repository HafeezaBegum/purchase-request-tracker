"""Data shapes shared by the extractor, database, and API."""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

REQUIRED_FIELDS = ("item", "quantity", "unit", "deadline", "department")


class Status(str, Enum):
    NEEDS_INFO = "needs_info"
    NEW = "new"
    APPROVED = "approved"
    ORDERED = "ordered"
    DONE = "done"
    REJECTED = "rejected"


class Extracted(BaseModel):
    """Structured fields pulled out of a free-text request.

    Every field is optional on purpose: a missing value is recorded as None
    and flagged, never guessed.
    """

    item: str | None = Field(default=None, max_length=300)
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=20)
    deadline: date | None = None
    department: str | None = Field(default=None, max_length=100)

    def missing_fields(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if getattr(self, f) in (None, "")]


class RequestIn(BaseModel):
    text: str = Field(min_length=3, max_length=5000)
    department: str | None = Field(default=None, max_length=100)


class StatusUpdate(BaseModel):
    status: Status


class RequestOut(BaseModel):
    id: int
    text: str
    extracted: Extracted
    missing: list[str]
    clarification: str | None
    method: str
    status: Status
    created_at: str
    updated_at: str
