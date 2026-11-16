"""`/search` payloads — mirrors §18.5.3 (with the v4 block-level additions)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class InstrumentHit(BaseModel):
    symbol: str
    name: str
    market: str
    currency: str
    score: float
    #: True when a report for this instrument already exists locally.
    existing: bool = False


class ReportHit(BaseModel):
    reportId: str
    symbol: str
    title: str
    #: ★V4-P0-1: both ids come from the block-level index, so the hit can deep-link
    #: to `/{symbol}?report=<id>&section=<sid>&block=<bid>`.
    sectionId: str
    sectionTitle: str = ""
    blockId: str = ""
    #: A **substring of the block's own text** with `<mark>` around the match.
    snippet: str
    score: float


class SearchResponse(BaseModel):
    query: str
    instruments: list[InstrumentHit] = Field(default_factory=list)
    reports: list[ReportHit] = Field(default_factory=list)
    tookMs: int = 0
    #: True when the LIKE fallback answered because FTS5 is unavailable (R-18).
    degraded: bool = False
