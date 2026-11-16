"""Shared envelopes (spec §6.2 / §7.1)."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    requestId: str = "-"
    detail: dict[str, object] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


class HealthResponse(BaseModel):
    status: str
    version: str
    db: str
    uptimeSec: float
