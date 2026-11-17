"""Instrument / symbol resolution payloads (spec §7.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Market = Literal["CN", "HK", "US"]


class AliasOut(BaseModel):
    alias: str


class InstrumentOut(BaseModel):
    id: str
    symbol: str
    name: str
    market: Market
    exchange: str | None = None
    currency: str
    sector: str | None = None
    tz: str = "Asia/Shanghai"
    createdAt: str | None = None
    updatedAt: str | None = None


class InstrumentPatch(BaseModel):
    name: str | None = None
    sector: str | None = None


class SymbolSearchHit(BaseModel):
    symbol: str
    name: str
    market: Market
    currency: str
    score: float
    existing: bool


class SymbolSearchResponse(BaseModel):
    hits: list[SymbolSearchHit]


class ResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=64)


class ResolveResponse(BaseModel):
    instrument: InstrumentOut
    created: bool
    needsConfirm: bool = False
    candidates: list[SymbolSearchHit] = Field(default_factory=list)


class QuoteOut(BaseModel):
    instrumentId: str
    last: float | None = None
    changePct: float | None = None
    prevClose: float | None = None
    dayHigh: float | None = None
    dayLow: float | None = None
    volume: float | None = None
    updatedAt: str
    source: str | None = None
    #: ★§6.1 #17: whether this answer came from the cache instead of a live provider.
    #: It replaces the old `source: "akshare (cached)"` string, which put a state into
    #: the source field and then flowed into `dataQuality` and the report text.
    cached: bool = False


class InstrumentsResponse(BaseModel):
    items: list[InstrumentOut]
