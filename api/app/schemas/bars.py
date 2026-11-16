"""Bar payloads, enums and range helpers (spec §7.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from .instrument import InstrumentOut


class Timeframe(StrEnum):
    d1 = "1d"
    w1 = "1w"
    m1 = "1m"


class Range(StrEnum):
    m1 = "1m"
    m3 = "3m"
    m6 = "6m"
    y1 = "1y"
    y3 = "3y"
    y5 = "5y"


class Adjust(StrEnum):
    none = "none"
    qfq = "qfq"
    hfq = "hfq"


_RANGE_DAYS: dict[str, int] = {
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 366,
    "3y": 1096,
    "5y": 1827,
}


def range_to_days(value: str) -> int:
    return _RANGE_DAYS.get(value, _RANGE_DAYS["1y"])


def range_to_epoch(value: str, *, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    return int((moment - timedelta(days=range_to_days(value))).timestamp())


class BarOut(BaseModel):
    ts: int
    o: float
    h: float
    l: float
    c: float
    v: float
    amount: float | None = None


class IndicatorsOut(BaseModel):
    ma5: list[float | None] = []
    ma10: list[float | None] = []
    ma20: list[float | None] = []
    ma60: list[float | None] = []
    bollUpper: list[float | None] = []
    bollLower: list[float | None] = []
    macd: list[float | None] = []
    macdSignal: list[float | None] = []
    rsi14: list[float | None] = []


class BarsMeta(BaseModel):
    source: str
    barsCount: int
    fromTs: int | None = None
    toTs: int | None = None
    adjust: str
    timeframe: str
    cached: bool = False
    stale: bool = False
    warnings: list[str] = []


class BarsResponse(BaseModel):
    instrument: InstrumentOut
    bars: list[BarOut]
    indicators: IndicatorsOut
    meta: BarsMeta
