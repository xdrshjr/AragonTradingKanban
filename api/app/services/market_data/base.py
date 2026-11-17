"""Provider protocol and shared DTOs (spec §9).

Every provider speaks the same small dialect so the registry can fall through
the chain without special cases: ``search`` / ``bars`` / ``quote`` /
``fundamentals``, each raising a classified ``ProviderError`` on failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ProviderErrorKind(StrEnum):
    network = "network"
    ratelimit = "ratelimit"
    notfound = "notfound"
    unavailable = "unavailable"


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, message: str, *, provider: str = "") -> None:
        self.kind = kind
        self.provider = provider
        super().__init__(f"{provider}: {message}" if provider else message)

    @property
    def retryable(self) -> bool:
        return self.kind in (ProviderErrorKind.network, ProviderErrorKind.ratelimit)


class ProviderUnavailable(ProviderError):
    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(ProviderErrorKind.unavailable, message, provider=provider)


@dataclass(slots=True)
class SymbolHit:
    symbol: str
    name: str
    market: str
    currency: str
    score: float = 1.0


@dataclass(slots=True)
class InstrumentInfo:
    symbol: str
    name: str
    market: str
    currency: str
    exchange: str | None = None
    sector: str | None = None
    tz: str = "Asia/Shanghai"


@dataclass(slots=True)
class BarRow:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float | None = None


@dataclass(slots=True)
class BarsResult:
    bars: list[BarRow]
    source: str
    warnings: list[str] = field(default_factory=list)


#: Timeframes that are *derived* from the daily series instead of being asked of a
#: provider (PCD-1). Providers keep their "daily only" contract
#: (`api/tests/test_market_data.py` pins `bars(1w) == []`), so the aggregation happens
#: once, here, for every provider — including synthetic, which is why the offline demo
#: has weekly/monthly candles too. Going to akshare's native `period="weekly"` was
#: rejected: its adjustment base differs from the daily one, so it would re-create the
#: "日线图与周线图两个价" seam that BUG-1/BUG-3 closed.
RESAMPLED_TIMEFRAMES = ("1w", "1m")


#: Coarse UTC offsets used to put a daily bar back on its **session** date before
#: bucketing. Not every provider stamps a daily bar on the same clock: akshare and
#: synthetic use 07:00 UTC (the CN close), while yfinance hands back the exchange's
#: own midnight — `2026-03-01 00:00 +08:00` is `2026-02-28 16:00 UTC`, i.e. the
#: **previous** UTC day. Bucketing on the raw UTC date would file a 1st-of-month bar
#: under the previous month and a Monday bar under the previous ISO week, which is a
#: fabricated monthly/weekly open. Adding the offset is a no-op for 07:00 UTC stamps
#: and fixes the midnight ones. DST is ignored on purpose, exactly like
#: ``cache.is_market_open``: a bucketing slip near a DST edge is not worth pulling a
#: tz database into a pure function.
_SESSION_UTC_OFFSET_HOURS = {"CN": 8, "HK": 8, "US": 0}


def _bucket_key(day: date, timeframe: str) -> tuple[int, int]:
    if timeframe == "1w":
        iso = day.isocalendar()
        return (iso[0], iso[1])  # ISO year / ISO week — Monday-anchored
    return (day.year, day.month)


def _session_date(ts: int, market: str | None) -> date:
    offset = _SESSION_UTC_OFFSET_HOURS.get((market or "").upper(), 0)
    return datetime.fromtimestamp(ts + offset * 3600, tz=UTC).date()


def resample_daily(rows: list[BarRow], timeframe: str, *, market: str | None = None) -> list[BarRow]:
    """Aggregate daily bars into ISO weeks or calendar months (PCD-1).

    ``open`` from the first bar of the bucket, ``high``/``low`` its extremes,
    ``close``/``ts`` from the **last** bar — so a bucketed series still ends on the
    same session, at the same price, as the daily one. That identity is the whole
    point: it is what stops the weekly chart from naming a different number than the
    daily chart and the quote (AC1).

    ``market`` selects the session clock the buckets are cut on (see
    ``_SESSION_UTC_OFFSET_HOURS``); without it the bar's own UTC date is used.

    The first and last buckets are partial whenever the window does not start on a
    bucket boundary. That is harmless here because nothing bucketed is ever *stored*:
    the cache only ever holds the daily series (``routers/instruments.py``), so
    coverage is judged on the daily rows and no bucket-anchored tolerance is needed.
    """
    if timeframe not in RESAMPLED_TIMEFRAMES:
        return list(rows)
    buckets: dict[tuple[int, int], list[BarRow]] = {}
    order: list[tuple[int, int]] = []
    for row in sorted(rows, key=lambda item: item.ts):
        key = _bucket_key(_session_date(row.ts, market), timeframe)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)

    out: list[BarRow] = []
    for key in order:
        group = buckets[key]
        first, last = group[0], group[-1]
        amounts = [bar.amount for bar in group]
        total_amount = None if any(value is None for value in amounts) else sum(amounts, 0.0)
        out.append(
            BarRow(
                ts=last.ts,
                open=first.open,
                high=max(bar.high for bar in group),
                low=min(bar.low for bar in group),
                close=last.close,
                volume=sum(bar.volume for bar in group),
                amount=total_amount,
            )
        )
    return out


@dataclass(slots=True)
class QuoteRow:
    last: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    updated_at: str = ""
    source: str = ""


@dataclass(slots=True)
class FundamentalPeriod:
    period: str
    revenue: float | None = None
    revenue_yoy: float | None = None
    net_profit: float | None = None
    net_profit_yoy: float | None = None
    ocf: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None


@dataclass(slots=True)
class SourceRef:
    id: str
    title: str
    publisher: str
    published_at: date | None = None
    url: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "title": self.title,
            "publisher": self.publisher,
            "publishedAt": self.published_at.isoformat() if self.published_at else None,
            "url": self.url,
        }


@runtime_checkable
class MarketDataProvider(Protocol):
    """What the registry needs from a provider. Not every provider does all four."""

    name: str

    def available(self) -> bool:
        """Whether the optional dependency for this provider can be imported."""

    def search(self, query: str, limit: int) -> list[SymbolHit]: ...

    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]: ...

    def quote(self, symbol: str) -> QuoteRow: ...

    def fundamentals(self, symbol: str, periods: int) -> list[FundamentalPeriod]: ...
