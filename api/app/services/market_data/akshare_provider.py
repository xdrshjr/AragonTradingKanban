"""akshare provider — A-share / HK daily bars and financial abstracts (spec §9).

Imported lazily: akshare pulls in pandas and a pile of scrapers, so the API must
boot fine without it. Any missing dependency or network hiccup surfaces as a
classified ``ProviderError`` and the registry moves on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .base import (
    BarRow,
    FundamentalPeriod,
    ProviderError,
    ProviderErrorKind,
    ProviderUnavailable,
    QuoteRow,
    SymbolHit,
)


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


class AkshareProvider:
    name = "akshare"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def available(self) -> bool:
        try:
            import akshare  # noqa: F401
        except Exception:  # pragma: no cover - optional dependency
            return False
        return True

    def _ak(self) -> Any:
        try:
            import akshare
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ProviderUnavailable(
                "akshare is not installed (pip install -e '.[data]')", provider=self.name
            ) from exc
        return akshare

    # ── search ───────────────────────────────────────────────────────────
    def search(self, query: str, limit: int) -> list[SymbolHit]:
        ak = self._ak()
        try:
            frame = ak.stock_info_a_code_name()
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.network, str(exc)[:200], provider=self.name) from exc
        needle = query.strip().lower()
        hits: list[SymbolHit] = []
        for _, row in frame.iterrows():
            code = str(row.get("code", ""))
            name = str(row.get("name", ""))
            if needle in code.lower() or needle in name.lower():
                suffix = ".SH" if code.startswith(("6", "9")) else ".SZ"
                hits.append(SymbolHit(f"{code}{suffix}", name, "CN", "CNY", 0.9))
                if len(hits) >= limit:
                    break
        return hits

    # ── bars ─────────────────────────────────────────────────────────────
    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        if timeframe != "1d":
            return []
        ak = self._ak()
        clean = _normalize_symbol(symbol)
        code, _, suffix = clean.partition(".")
        if suffix not in ("SH", "SZ"):
            raise ProviderError(ProviderErrorKind.notfound, f"{symbol} is not an A-share", provider=self.name)
        start = datetime.fromtimestamp(start_ts, tz=UTC).strftime("%Y%m%d")
        try:
            frame = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=datetime.now(UTC).strftime("%Y%m%d"),
                adjust="" if adjust == "none" else adjust,
            )
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.network, str(exc)[:200], provider=self.name) from exc
        if frame is None or len(frame) == 0:
            raise ProviderError(ProviderErrorKind.notfound, f"no bars for {symbol}", provider=self.name)

        rows: list[BarRow] = []
        for _, item in frame.iterrows():
            trade_date = item.get("日期") or item.get("date")
            moment = datetime.fromisoformat(str(trade_date)[:10]).replace(hour=7, tzinfo=UTC)
            rows.append(
                BarRow(
                    ts=int(moment.timestamp()),
                    open=float(item.get("开盘", 0) or 0),
                    high=float(item.get("最高", 0) or 0),
                    low=float(item.get("最低", 0) or 0),
                    close=float(item.get("收盘", 0) or 0),
                    volume=float(item.get("成交量", 0) or 0),
                    amount=float(item["成交额"]) if item.get("成交额") is not None else None,
                )
            )
        return rows

    # ── quote ────────────────────────────────────────────────────────────
    def quote(self, symbol: str) -> QuoteRow:
        bars = self.bars(
            symbol,
            timeframe="1d",
            start_ts=int(datetime.now(UTC).timestamp()) - 14 * 86400,
            adjust="qfq",
        )
        if not bars:
            raise ProviderError(ProviderErrorKind.notfound, f"no quote for {symbol}", provider=self.name)
        last_row, prev_row = bars[-1], bars[-2] if len(bars) > 1 else bars[-1]
        change = (last_row.close / prev_row.close - 1) * 100 if prev_row.close else 0.0
        return QuoteRow(
            last=last_row.close,
            change_pct=round(change, 2),
            prev_close=prev_row.close,
            day_high=last_row.high,
            day_low=last_row.low,
            volume=last_row.volume,
            updated_at=datetime.fromtimestamp(last_row.ts, tz=UTC).isoformat().replace("+00:00", "Z"),
            source=self.name,
        )

    # ── fundamentals ─────────────────────────────────────────────────────
    def fundamentals(self, symbol: str, periods: int) -> list[FundamentalPeriod]:
        ak = self._ak()
        code, _, suffix = _normalize_symbol(symbol).partition(".")
        if suffix not in ("SH", "SZ"):
            return []
        prefix = "SH" if suffix == "SH" else "SZ"
        try:
            frame = ak.stock_financial_abstract(symbol=f"{prefix}{code}")
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.network, str(exc)[:200], provider=self.name) from exc
        if frame is None or len(frame) == 0:
            return []
        out: list[FundamentalPeriod] = []
        columns = [column for column in frame.columns if str(column).startswith("20")][:periods]
        for column in columns:
            try:
                revenue_row = frame[frame["指标"].astype(str).str.contains("营业总收入")]
                profit_row = frame[frame["指标"].astype(str).str.contains("归母净利润")]
                revenue = float(revenue_row[column].iloc[0]) if len(revenue_row) else None
                profit = float(profit_row[column].iloc[0]) if len(profit_row) else None
            except Exception:  # pragma: no cover - shape drift in the upstream lib
                continue
            out.append(
                FundamentalPeriod(
                    period=str(column),
                    revenue=revenue,
                    net_profit=profit,
                    net_margin=round(profit / revenue * 100, 1) if revenue and profit else None,
                )
            )
        return out
