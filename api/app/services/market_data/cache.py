"""TTL cache for bars/quotes, persisted in SQLite (spec §9).

Cache policy: 15 min while the market is open, 6 h after the close, and
"forever" for purely historical windows (`to < today - 7d`). Hits are reported
back as ``cached=True`` so the UI can show where the numbers came from.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Bar, Quote
from ..market_data.base import BarRow, QuoteRow

HISTORICAL_FLOOR_DAYS = 7


def is_market_open(market: str, now: datetime | None = None) -> bool:
    """Coarse session check — good enough to pick a TTL, not a trading calendar."""
    moment = now or datetime.now(UTC)
    if moment.weekday() >= 5:
        return False
    if market == "CN":
        hour = (moment.hour + 8) % 24
        return 9 <= hour < 15
    if market == "HK":
        hour = (moment.hour + 8) % 24
        return 9 <= hour < 16
    hour = (moment.hour - 5) % 24  # ET ≈ UTC-5 (DST ignored on purpose)
    return 9 <= hour < 16


def bars_ttl_seconds(market: str, now: datetime | None = None) -> int:
    if is_market_open(market, now):
        return get_settings().bar_cache_ttl_sec
    return 6 * 3600


def read_bars(
    session: Session,
    instrument_id: str,
    *,
    timeframe: str,
    adjust: str,
    start_ts: int,
) -> tuple[list[BarRow], str | None]:
    rows = session.execute(
        select(Bar)
        .where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == timeframe,
            Bar.adjust == adjust,
            Bar.ts >= start_ts,
        )
        .order_by(Bar.ts.asc())
    ).scalars()
    bars: list[BarRow] = []
    source: str | None = None
    for row in rows:
        bars.append(
            BarRow(
                ts=row.ts,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                amount=row.amount,
            )
        )
        source = row.source
    return bars, source


def is_bars_cache_fresh(bars: list[BarRow], market: str, now: datetime | None = None) -> bool:
    """Historical windows never go stale; recent ones obey the TTL."""
    if not bars:
        return False
    moment = now or datetime.now(UTC)
    last_ts = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
    window_end = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
    if window_end < moment - timedelta(days=HISTORICAL_FLOOR_DAYS):
        return True
    return (moment - last_ts) < timedelta(seconds=bars_ttl_seconds(market, moment))


def write_bars(
    session: Session,
    instrument_id: str,
    *,
    timeframe: str,
    adjust: str,
    bars: list[BarRow],
    source: str,
) -> None:
    if not bars:
        return
    existing = {
        row.ts: row
        for row in session.execute(
            select(Bar).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == timeframe,
                Bar.adjust == adjust,
                Bar.ts >= bars[0].ts,
            )
        ).scalars()
    }
    for bar in bars:
        row = existing.get(bar.ts)
        if row is None:
            row = Bar(
                instrument_id=instrument_id,
                timeframe=timeframe,
                ts=bar.ts,
                adjust=adjust,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                amount=bar.amount,
                source=source,
            )
            session.add(row)
        else:
            row.open = bar.open
            row.high = bar.high
            row.low = bar.low
            row.close = bar.close
            row.volume = bar.volume
            row.amount = bar.amount
            row.source = source
    session.flush()


def read_quote(session: Session, instrument_id: str) -> QuoteRow | None:
    row = session.get(Quote, instrument_id)
    if row is None:
        return None
    return QuoteRow(
        last=row.last,
        change_pct=row.change_pct,
        prev_close=row.prev_close,
        day_high=row.day_high,
        day_low=row.day_low,
        volume=row.volume,
        updated_at=row.updated_at,
        source=row.source or "",
    )


def quote_is_fresh(quote: QuoteRow, now: datetime | None = None, ttl: int = 60) -> bool:
    if not quote.updated_at:
        return False
    moment = now or datetime.now(UTC)
    try:
        stamped = datetime.fromisoformat(quote.updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    return (moment - stamped) < timedelta(seconds=ttl)


def write_quote(session: Session, instrument_id: str, quote: QuoteRow) -> None:
    row = session.get(Quote, instrument_id)
    if row is None:
        row = Quote(instrument_id=instrument_id, updated_at=quote.updated_at)
        session.add(row)
    row.last = quote.last
    row.change_pct = quote.change_pct
    row.prev_close = quote.prev_close
    row.day_high = quote.day_high
    row.day_low = quote.day_low
    row.volume = quote.volume
    row.source = quote.source
    row.updated_at = quote.updated_at
    session.flush()


def purge_instrument_bars(session: Session, instrument_id: str) -> None:
    session.execute(delete(Bar).where(Bar.instrument_id == instrument_id))
