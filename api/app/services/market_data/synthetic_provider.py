"""Deterministic synthetic provider — the offline floor (spec §9).

Seeded by ``blake2b(symbol)`` so the same symbol always produces the same
prices: tests can assert on it, and a demo without network still looks alive.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, date, datetime, timedelta

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
        rng = _rng(symbol, f"bars-{timeframe}-{adjust}")
        price = base_price(symbol)
        step_days = 1
        start = datetime.fromtimestamp(start_ts, tz=UTC).date()
        today = datetime.now(UTC).date()
        rows: list[BarRow] = []
        drift = rng.uniform(-0.0004, 0.0009)
        vol = rng.uniform(0.011, 0.026)
        cursor = start
        while cursor <= today:
            if cursor.weekday() < 5:  # trading days only
                shock = rng.gauss(0.0, 1.0)
                price *= math.exp(drift + vol * shock)
                price = max(price, 0.5)
                open_price = price * (1 + rng.gauss(0, vol / 4))
                close_price = price
                high = max(open_price, close_price) * (1 + abs(rng.gauss(0, vol / 3)))
                low = min(open_price, close_price) * (1 - abs(rng.gauss(0, vol / 3)))
                volume = round(rng.uniform(1.2e6, 4.2e7))
                rows.append(
                    BarRow(
                        ts=int(datetime(cursor.year, cursor.month, cursor.day, 7, 0, tzinfo=UTC).timestamp()),
                        open=round(open_price, 2),
                        high=round(high, 2),
                        low=round(max(low, 0.1), 2),
                        close=round(close_price, 2),
                        volume=volume,
                        amount=round(volume * close_price, 2),
                    )
                )
            cursor += timedelta(days=step_days)
        return rows

    # ── quote ────────────────────────────────────────────────────────────
    def quote(self, symbol: str) -> QuoteRow:
        rng = _rng(symbol, "quote")
        price = base_price(symbol)
        change = round(rng.uniform(-2.8, 3.1), 2)
        last = round(price * (1 + change / 100), 2)
        return QuoteRow(
            last=last,
            change_pct=change,
            prev_close=price,
            day_high=round(last * 1.012, 2),
            day_low=round(last * 0.988, 2),
            volume=round(rng.uniform(1.5e6, 5.5e7)),
            updated_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
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
