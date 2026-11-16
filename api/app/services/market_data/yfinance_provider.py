"""yfinance provider — US / HK daily bars and headline fundamentals (spec §9)."""

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


def to_yahoo_symbol(symbol: str) -> str:
    """`600519.SH` never reaches here, but `0700.HK` → `0700.HK`, `AAPL` → `AAPL`."""
    clean = symbol.strip().upper()
    if clean.endswith(".SH"):
        return f"{clean[:-3]}.SS"
    return clean


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def available(self) -> bool:
        try:
            import yfinance  # noqa: F401
        except Exception:  # pragma: no cover - optional dependency
            return False
        return True

    def _yf(self) -> Any:
        try:
            import yfinance
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ProviderUnavailable(
                "yfinance is not installed (pip install -e '.[data]')", provider=self.name
            ) from exc
        return yfinance

    def search(self, query: str, limit: int) -> list[SymbolHit]:
        yf = self._yf()
        try:
            results = yf.Search(query, max_results=limit).quotes  # type: ignore[attr-defined]
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.network, str(exc)[:200], provider=self.name) from exc
        hits: list[SymbolHit] = []
        for item in results or []:
            symbol = str(item.get("symbol", "")).upper()
            if not symbol:
                continue
            market = "HK" if symbol.endswith(".HK") else "US"
            hits.append(
                SymbolHit(
                    symbol=symbol,
                    name=str(item.get("shortname") or item.get("longname") or symbol),
                    market=market,
                    currency="HKD" if market == "HK" else "USD",
                    score=0.85,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        if timeframe != "1d":
            return []
        yf = self._yf()
        start = datetime.fromtimestamp(start_ts, tz=UTC).date().isoformat()
        try:
            frame = yf.Ticker(to_yahoo_symbol(symbol)).history(start=start, interval="1d", auto_adjust=adjust != "none")
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.network, str(exc)[:200], provider=self.name) from exc
        if frame is None or len(frame) == 0:
            raise ProviderError(ProviderErrorKind.notfound, f"no bars for {symbol}", provider=self.name)
        rows: list[BarRow] = []
        for index, item in frame.iterrows():
            moment = index.to_pydatetime()
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            rows.append(
                BarRow(
                    ts=int(moment.timestamp()),
                    open=float(item["Open"]),
                    high=float(item["High"]),
                    low=float(item["Low"]),
                    close=float(item["Close"]),
                    volume=float(item.get("Volume", 0) or 0),
                )
            )
        return rows

    def quote(self, symbol: str) -> QuoteRow:
        bars = self.bars(
            symbol,
            timeframe="1d",
            start_ts=int(datetime.now(UTC).timestamp()) - 14 * 86400,
            adjust="none",
        )
        if not bars:
            raise ProviderError(ProviderErrorKind.notfound, f"no quote for {symbol}", provider=self.name)
        last_row = bars[-1]
        prev_row = bars[-2] if len(bars) > 1 else bars[-1]
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

    def fundamentals(self, symbol: str, periods: int) -> list[FundamentalPeriod]:
        yf = self._yf()
        try:
            ticker = yf.Ticker(to_yahoo_symbol(symbol))
            frame = ticker.quarterly_income_stmt
        except Exception as exc:
            raise ProviderError(ProviderErrorKind.network, str(exc)[:200], provider=self.name) from exc
        if frame is None or len(frame.columns) == 0:
            return []
        out: list[FundamentalPeriod] = []
        for column in list(frame.columns)[:periods]:
            # Bind `column` into the closures explicitly: capturing the loop
            # variable by reference is a latent bug even when it happens to work.
            def pick(row_name: str, period_column: object = column) -> float | None:
                try:
                    value = frame.loc[row_name, period_column]
                except Exception:
                    return None
                return None if value is None or value != value else float(value)  # NaN check

            def period_label(period_column: object = column) -> str:
                date_method = getattr(period_column, "date", None)
                return str(date_method() if callable(date_method) else period_column)[:10]

            revenue = pick("Total Revenue")
            profit = pick("Net Income")
            out.append(
                FundamentalPeriod(
                    period=period_label(),
                    revenue=revenue,
                    net_profit=profit,
                    gross_margin=round((pick("Gross Profit") or 0) / revenue * 100, 1) if revenue else None,
                    net_margin=round(profit / revenue * 100, 1) if revenue and profit else None,
                )
            )
        return out
