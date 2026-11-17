"""Deterministic synthetic provider — the offline floor (spec §9).

Seeded by ``blake2b(symbol)`` so the same symbol always produces the same
prices: tests can assert on it, and a demo without network still looks alive.

★BUG-1 / R-1 / R-4: "the same symbol always produces the same prices" is only
true if a *date* — not a *window* — decides the price. The walk therefore starts
at the fixed calendar anchor ``_WALK_ANCHOR`` and is sliced to the caller's
window at the end. Before this, the first bar of the walk sat at ``start_ts``, so
the same 2026-11-13 close read 1754.19 today and 1747.20 tomorrow, and
``range=1m`` / ``range=3y`` disagreed about today's close by up to 17%.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

from .base import (
    BarRow,
    FundamentalPeriod,
    ProviderError,
    ProviderErrorKind,
    QuoteRow,
    SymbolHit,
)

#: A handful of recognisable names so the search box feels real offline.
KNOWN_SYMBOLS: tuple[tuple[str, str, str, str, str], ...] = (
    ("600519.SH", "贵州茅台", "CN", "CNY", "SSE"),
    ("000858.SZ", "五粮液", "CN", "CNY", "SZSE"),
    ("000568.SZ", "泸州老窖", "CN", "CNY", "SZSE"),
    ("600809.SH", "山西汾酒", "CN", "CNY", "SSE"),
    ("300750.SZ", "宁德时代", "CN", "CNY", "SZSE"),
    ("601318.SH", "中国平安", "CN", "CNY", "SSE"),
    ("600036.SH", "招商银行", "CN", "CNY", "SSE"),
    ("002594.SZ", "比亚迪", "CN", "CNY", "SZSE"),
    ("0700.HK", "腾讯控股", "HK", "HKD", "HKEX"),
    ("9988.HK", "阿里巴巴-SW", "HK", "HKD", "HKEX"),
    ("AAPL", "Apple Inc.", "US", "USD", "NASDAQ"),
    ("MSFT", "Microsoft Corp.", "US", "USD", "NASDAQ"),
    ("NVDA", "NVIDIA Corp.", "US", "USD", "NASDAQ"),
    ("TSLA", "Tesla Inc.", "US", "USD", "NASDAQ"),
)

_ALIASES: dict[str, str] = {
    "茅台": "600519.SH",
    "贵州茅台": "600519.SH",
    "gzmoutai": "600519.SH",
    "maotai": "600519.SH",
    "宁德": "300750.SZ",
    "宁德时代": "300750.SZ",
    "腾讯": "0700.HK",
    "阿里": "9988.HK",
    "苹果": "AAPL",
    "apple": "AAPL",
    "特斯拉": "TSLA",
    "英伟达": "NVDA",
    "五粮液": "000858.SZ",
    "泸州老窖": "000568.SZ",
    "汾酒": "600809.SH",
    "平安": "601318.SH",
    "招行": "600036.SH",
    "比亚迪": "002594.SZ",
    "微软": "MSFT",
}

TZ_BY_MARKET = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}
SECTOR_BY_SYMBOL = {
    "600519.SH": "食品饮料",
    "000858.SZ": "食品饮料",
    "000568.SZ": "食品饮料",
    "600809.SH": "食品饮料",
    "300750.SZ": "电力设备",
    "601318.SH": "非银金融",
    "600036.SH": "银行",
    "002594.SZ": "汽车",
    "0700.HK": "互联网",
    "9988.HK": "互联网",
    "AAPL": "消费电子",
    "MSFT": "软件服务",
    "NVDA": "半导体",
    "TSLA": "汽车",
}

#: Anchor prices for the demo symbols so offline screenshots look plausible
#: rather than randomly priced. Everything else falls back to a seeded draw.
#:
#: ★R-4(1): the semantics of these numbers changed with the fixed walk anchor.
#: They used to mean "today's price" (the walk was re-anchored here on every
#: request, which *is* the bug). They now mean "the price on ``_WALK_ANCHOR`` plus
#: the centre that ``_KAPPA`` reverts towards". A symbol can therefore sit at
#: 0.6× or 1.6× its number today: window-independence, a stable history and
#: "today equals this constant exactly" cannot all hold at once, and the first two
#: are the ones a reader can check. Do not "fix" the drift back by re-anchoring —
#: that resurrects BUG-1.
BASE_PRICE_BY_SYMBOL = {
    "600519.SH": 1672.0,
    "000858.SZ": 138.4,
    "000568.SZ": 121.7,
    "600809.SH": 186.9,
    "300750.SZ": 248.6,
    "601318.SH": 52.4,
    "600036.SH": 38.9,
    "002594.SZ": 264.3,
    "0700.HK": 412.8,
    "9988.HK": 96.4,
    "AAPL": 228.5,
    "MSFT": 438.2,
    "NVDA": 142.7,
    "TSLA": 248.9,
}


def _seed(symbol: str) -> int:
    return int.from_bytes(hashlib.blake2b(symbol.encode("utf-8"), digest_size=8).digest(), "big")


def _rng(symbol: str, salt: str = "") -> random.Random:
    return random.Random(_seed(symbol + salt))


def base_price(symbol: str) -> float:
    anchored = BASE_PRICE_BY_SYMBOL.get(symbol.strip().upper())
    if anchored is not None:
        return anchored
    rng = _rng(symbol, "price")
    market = "US" if not any(symbol.endswith(suffix) for suffix in (".SH", ".SZ", ".HK")) else "CN"
    if market == "US":
        return round(rng.uniform(40.0, 480.0), 2)
    return round(rng.uniform(18.0, 1900.0), 2)


#: Where the one and only price path starts. Fixed, so "the close of D" is a
#: function of D alone. Changing this constant changes every synthetic price at
#: once — that is a deliberate, one-time event, not a tweak.
_WALK_ANCHOR = date(2000, 1, 1)

#: Weak mean reversion towards ``base_price`` (in log space, half-life ≈ 230
#: sessions). Without it a 7,000-step walk ends at e^(±1.75) of the anchor and the
#: demo quotes 600519.SH at ¥25.71 and AAPL at $14,755 (R-4(1), measured).
_KAPPA = 0.003

#: The bar timestamp of a session: 07:00 UTC = 15:00 Asia/Shanghai, i.e. the CN
#: close. Kept as-is because cache freshness and the anchors above both key on it.
_BAR_HOUR_UTC = 7


def _walk_bars(symbol: str, timeframe: str, adjust: str, today: date) -> tuple[BarRow, ...]:
    """The symbol's whole price history up to ``today``, from ``_WALK_ANCHOR``.

    Deterministic in ``(symbol, timeframe, adjust, today)`` and *monotone* in
    ``today``: appending a day never changes an earlier day. That monotonicity is
    what ``bars()`` slicing relies on, and it is what the cross-day test pins.
    """
    rng = _rng(symbol, f"bars-{timeframe}-{adjust}")
    anchor_price = base_price(symbol)
    log_anchor = math.log(anchor_price)
    drift = rng.uniform(-0.0004, 0.0009)
    vol = rng.uniform(0.011, 0.026)
    price = anchor_price
    #: R-4(2): the open gaps off the *previous* close. The old code jittered it
    #: around today's own close, which made the mean overnight gap (1.469%) four
    #: times the mean body (0.351%) — the direction of a real daily bar is the
    #: other way round, and it is why so many bars showed high < prevClose.
    prev_close = anchor_price
    rows: list[BarRow] = []
    cursor = _WALK_ANCHOR
    while cursor <= today:
        if cursor.weekday() < 5:  # trading days only
            shock = rng.gauss(0.0, 1.0)
            reversion = _KAPPA * (log_anchor - math.log(price))
            price = max(price * math.exp(drift + vol * shock + reversion), 0.5)
            close_price = price
            open_price = prev_close * (1 + rng.gauss(0, vol / 6))
            high = max(open_price, close_price) * (1 + abs(rng.gauss(0, vol / 3)))
            low = min(open_price, close_price) * (1 - abs(rng.gauss(0, vol / 3)))
            volume = round(rng.uniform(1.2e6, 4.2e7))
            rows.append(
                BarRow(
                    ts=int(datetime(cursor.year, cursor.month, cursor.day, _BAR_HOUR_UTC, 0, tzinfo=UTC).timestamp()),
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(max(low, 0.1), 2),
                    close=round(close_price, 2),
                    volume=volume,
                    amount=round(volume * close_price, 2),
                )
            )
            prev_close = close_price
        cursor += timedelta(days=1)
    return tuple(rows)


@lru_cache(maxsize=32)
def _cached_walk(symbol: str, timeframe: str, adjust: str, today: date) -> tuple[BarRow, ...]:
    """~7,000 steps ≈ 114ms measured — `bars()` and `quote()` must share one result.

    Without this the rail pays it twice per symbol per refresh (once for `/bars`,
    once for `/quote`). `today` is part of the key, so the cache retires itself at
    midnight without any invalidation hook.
    """
    return _walk_bars(symbol, timeframe, adjust, today)


#: `quote()` is "the last session of the same series `/bars` defaults to"
#: (``Timeframe.d1`` / ``Adjust.qfq`` in `routers/instruments.py`). If those
#: defaults ever change, this pair must change with them or the two prices split
#: again.
_QUOTE_TIMEFRAME = "1d"
_QUOTE_ADJUST = "qfq"


def _last_two_bars(symbol: str, today: date | None = None) -> tuple[BarRow, BarRow] | None:
    day = today or datetime.now(UTC).date()
    rows = _cached_walk(symbol, _QUOTE_TIMEFRAME, _QUOTE_ADJUST, day)
    if not rows:
        return None
    return (rows[-2] if len(rows) > 1 else rows[-1]), rows[-1]


class SyntheticProvider:
    name = "synthetic"

    def available(self) -> bool:
        return True

    # ── search ───────────────────────────────────────────────────────────
    def search(self, query: str, limit: int) -> list[SymbolHit]:
        needle = query.strip().lower()
        if not needle:
            return []
        hits: list[SymbolHit] = []
        for symbol, name, market, currency, _exchange in KNOWN_SYMBOLS:
            haystacks = {symbol.lower(), name.lower(), name}
            if any(needle in candidate for candidate in haystacks):
                hits.append(SymbolHit(symbol, name, market, currency, 0.95))
                continue
            if _ALIASES.get(needle) == symbol:
                hits.append(SymbolHit(symbol, name, market, currency, 0.9))
        return hits[:limit]

    # ── bars ─────────────────────────────────────────────────────────────
    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        if timeframe != "1d":
            return []
        today = datetime.now(UTC).date()
        # The window decides only where the slice starts — never what a date is
        # worth. Two ranges over the same calendar therefore overlap bar-for-bar.
        return [bar for bar in _cached_walk(symbol, timeframe, adjust, today) if bar.ts >= start_ts]

    # ── quote ────────────────────────────────────────────────────────────
    def quote(self, symbol: str) -> QuoteRow:
        """The tail of the same walk `bars()` serves — all five fields, no second RNG.

        A quote built from its own random draw can never agree with the chart, and
        `day_high = last * 1.012` / `day_low = last * 0.988` manufactured bars whose
        high sat below the previous close (BUG-3, 37.9% of sessions).
        """
        pair = _last_two_bars(symbol)
        updated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if pair is None:  # pragma: no cover - the walk always covers the anchor
            price = base_price(symbol)
            return QuoteRow(
                last=price,
                change_pct=0.0,
                prev_close=price,
                day_high=price,
                day_low=price,
                volume=0.0,
                updated_at=updated_at,
                source=self.name,
            )
        previous, last = pair
        change = round((last.close / previous.close - 1) * 100, 2) if previous.close else 0.0
        return QuoteRow(
            last=last.close,
            change_pct=change,
            prev_close=previous.close,
            day_high=last.high,
            day_low=last.low,
            volume=last.volume,
            updated_at=updated_at,
            source=self.name,
        )

    # ── fundamentals ─────────────────────────────────────────────────────
    def fundamentals(self, symbol: str, periods: int) -> list[FundamentalPeriod]:
        rng = _rng(symbol, "fundamentals")
        scale = base_price(symbol) * 1e7
        today = date.today()
        out: list[FundamentalPeriod] = []
        revenue = scale * rng.uniform(0.8, 1.6)
        for index in range(periods):
            quarter_offset = index
            year = today.year - (quarter_offset // 4) - 1
            quarter = ((today.month - 1) // 3 - quarter_offset) % 4 + 1
            growth = rng.uniform(-4.0, 24.0)
            revenue = revenue * (1 + growth / 400)
            net_margin = rng.uniform(8.0, 49.0)
            out.append(
                FundamentalPeriod(
                    period=f"{year}Q{quarter}",
                    revenue=round(revenue, 0),
                    revenue_yoy=round(growth, 1),
                    net_profit=round(revenue * net_margin / 100, 0),
                    net_profit_yoy=round(growth + rng.uniform(-1.5, 3.5), 1),
                    ocf=round(revenue * rng.uniform(0.7, 1.05), 0),
                    gross_margin=round(rng.uniform(22.0, 92.0), 1),
                    net_margin=round(net_margin, 1),
                )
            )
        return out


class SyntheticNotFound(ProviderError):
    def __init__(self, symbol: str) -> None:
        super().__init__(ProviderErrorKind.notfound, f"unknown symbol {symbol}", provider="synthetic")
