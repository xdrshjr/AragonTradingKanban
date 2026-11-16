"""Provider protocol and shared DTOs (spec §9).

Every provider speaks the same small dialect so the registry can fall through
the chain without special cases: ``search`` / ``bars`` / ``quote`` /
``fundamentals``, each raising a classified ``ProviderError`` on failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
