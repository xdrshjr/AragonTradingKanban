"""Schedule (定时刷新) payloads — mirrors §18.5.5."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScheduleOut(BaseModel):
    id: str
    instrumentId: str
    symbol: str | None = None
    cron: str
    enabled: bool = True
    minAgeHours: int = 12
    nextRunAt: str
    lastRunId: str | None = None
    failureCount: int = 0
    createdAt: str
    updatedAt: str


class ScheduleCreate(BaseModel):
    instrumentId: str = Field(min_length=1, max_length=40)
    cron: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    minAgeHours: int = Field(default=12, ge=1, le=720)


class SchedulePatch(BaseModel):
    cron: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    minAgeHours: int | None = Field(default=None, ge=1, le=720)


class ScheduleListResponse(BaseModel):
    items: list[ScheduleOut]
    total: int
