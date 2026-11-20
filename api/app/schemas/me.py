"""Request / response shapes for `/api/v1/me` (spec §24.5.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PendingRequestRef(BaseModel):
    id: str
    createdAt: str


class QuotaBucket(BaseModel):
    #: `null` = unlimited.
    limit: int | None = None
    usedToday: int
    bonus: int
    remaining: int | None = None
    resetAt: str
    pendingRequest: PendingRequestRef | None = None


class QuotaStatus(BaseModel):
    report: QuotaBucket
    deepAsk: QuotaBucket
    tz: str


class QuotaLedgerItem(BaseModel):
    id: int
    at: str
    resource: Literal["report", "deep_ask"]
    delta: int
    reason: str
    source: Literal["daily", "bonus"]
    refId: str | None = None
    note: str | None = None


class QuotaLedgerPage(BaseModel):
    items: list[QuotaLedgerItem]
    nextCursor: str | None = None


class QuotaRequestCreate(BaseModel):
    resource: Literal["report", "deep_ask"]
    amount: int = Field(ge=1, le=20)
    reason: str = Field(min_length=1, max_length=200)


class QuotaRequestOut(BaseModel):
    id: str
    resource: Literal["report", "deep_ask"]
    amount: int
    reason: str
    status: Literal["pending", "approved", "rejected"]
    granted: int | None = None
    resolvedAt: str | None = None
    resolutionNote: str | None = None
    createdAt: str


class QuotaRequestList(BaseModel):
    items: list[QuotaRequestOut]
