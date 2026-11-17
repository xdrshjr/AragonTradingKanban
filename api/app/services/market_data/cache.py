"""TTL cache for bars/quotes, persisted in SQLite (spec §9).

Cache policy: a window is served from cache only when it *covers* the window that
was asked for **and** it is *fresh* — it already holds the session that is due, and
(while a session is running) it was refilled within ``BAR_CACHE_TTL_SEC``. Hits are
reported back as ``cached=True`` so the UI can show where the numbers came from.

★BUG-1 / BUG-7 / R-2: ``/bars`` used to treat "any row exists" as a hit, so the
first range anyone looked at capped every wider range forever (a `1m` cache
answered `3y` with 22 bars, ``cached=true``, ``stale=false``, no warning), and the
freshness predicate compared "how old is the last bar" against a TTL, which made
every weekend read as stale.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Bar, Quote
from ..market_data.base import BarRow, QuoteRow

HISTORICAL_FLOOR_DAYS = 7

#: A cached window may start this much later than the requested one before it
#: stops counting as coverage: weekends, holidays and the 07:00 UTC bar stamp.
COVERAGE_TOLERANCE_DAYS = 10

#: Clock skew we accept on a provider's "fetched at" stamp. Anything further into
#: the future is a bar timestamp masquerading as one (see ``quote_is_fresh``).
_FUTURE_SKEW = timedelta(minutes=5)

#: Generation tag for the synthetic walk (§5.3 R-4(3)). The pre-fix walk had no
#: date→price mapping, so its rows can never agree with the new quote; bumping
#: this tag retires them without any DDL, because ``Bar.source`` already exists
#: and is already reported to the UI. ``_public_source`` maps the tag back so the
#: reader keeps seeing "synthetic / 合成数据".
SYNTHETIC_TAG = "synthetic-v2"
_LEGACY_SYNTHETIC = "synthetic"

#: Session close in UTC hours — coarse, like ``is_market_open`` (no holiday table).
_CLOSE_HOUR_UTC = {"CN": 7, "HK": 8, "US": 21}


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


def latest_closed_session(market: str, now: datetime | None = None) -> date:
    """The newest calendar date whose session has already closed (coarse)."""
    moment = now or datetime.now(UTC)
    day = moment.date()
    if moment.hour < _CLOSE_HOUR_UTC.get(market, 7):
        day -= timedelta(days=1)
    while day.weekday() >= 5:  # Saturday/Sunday are never sessions
        day -= timedelta(days=1)
    return day


def latest_session_due(market: str, now: datetime | None = None) -> date:
    """The newest session a cache is expected to hold (spec §9).

    After the close that is the last closed session, and it stays put all weekend —
    nobody can produce a newer bar, so a Friday cache is as fresh as it gets. While
    a session is running it is *today*: a window that stops at yesterday is a whole
    session behind `/quote`, which is the two-price screen this fix removes.
    """
    moment = now or datetime.now(UTC)
    if is_market_open(market, moment):
        return moment.date()
    return latest_closed_session(market, moment)


#: When each (instrument, timeframe, adjust) window was last refilled from a
#: provider. §9 asks for "15 min while the market is open, 6 h after the close", and
#: that needs a "fetched at" which the zero-DDL `bars` table does not carry. The API
#: is single-worker by contract (README), so the stamp lives in memory: a restart
#: costs exactly one extra fetch and nothing else reads it.
_FETCHED_AT: dict[tuple[str, str, str], float] = {}

#: Windows are instruments a user *opened*, not instruments that exist; an
#: instance that runs for weeks must not grow this map without bound.
_FETCHED_AT_MAX = 1024


def record_bars_fetch(instrument_id: str, timeframe: str, adjust: str, *, at: float | None = None) -> None:
    """Stamp a refill so ``is_bars_cache_fresh`` can honour the TTL (tests pass ``at``)."""
    if len(_FETCHED_AT) >= _FETCHED_AT_MAX:
        _FETCHED_AT.clear()
    _FETCHED_AT[(instrument_id, timeframe, adjust)] = time.time() if at is None else at


def _refilled_within(key: tuple[str, str, str], market: str, moment: datetime) -> bool:
    stamp = _FETCHED_AT.get(key)
    return stamp is not None and (moment.timestamp() - stamp) < bars_ttl_seconds(market, moment)


def _storage_tag(source: str) -> str:
    return SYNTHETIC_TAG if source == _LEGACY_SYNTHETIC else source


def _public_source(stored: str | None) -> str | None:
    return _LEGACY_SYNTHETIC if stored == SYNTHETIC_TAG else stored


def read_bars(
    session: Session,
    instrument_id: str,
    *,
    timeframe: str,
    adjust: str,
    start_ts: int,
) -> tuple[list[BarRow], str | None]:
    rows = list(
        session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == timeframe,
                Bar.adjust == adjust,
                Bar.ts >= start_ts,
            )
            .order_by(Bar.ts.asc())
        ).scalars()
    )
    if any(row.source == _LEGACY_SYNTHETIC for row in rows):
        # A previous generator's rows: same date, different price. Serving them
        # next to the new quote would keep two numbers alive forever (§5.3 R-4(3)),
        # so retire that generation once — synthetic data is disposable by
        # definition — and let the caller refill from the provider.
        session.execute(
            delete(Bar).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == timeframe,
                Bar.adjust == adjust,
                Bar.source == _LEGACY_SYNTHETIC,
            )
        )
        return [], None
    if len({row.source for row in rows}) > 1:
        # Two producers spliced into one series (R-2(c)/R-2(d)). Their adjustment
        # bases differ, and a synthetic row sits on a different price level than a
        # real one, so the seam is a fabricated candle: measured -15.33% on
        # 600519.SH where a 22-row akshare block met 761 synthetic rows — served
        # with `cached=true, source=akshare`, i.e. both a fake move and a false
        # attribution. Miss instead, so one producer refills the whole window.
        return [], None
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
    return bars, _public_source(source)


def bars_cover(bars: list[BarRow], start_ts: int) -> bool:
    """Whether the cached rows reach back to the window that was asked for (R-2(a)).

    ``read_bars`` already filters by ``ts >= start_ts``, so without this a cache
    filled by a narrow range answers every wider one with its own 22 bars.
    """
    if not bars:
        return False
    return bars[0].ts - start_ts <= COVERAGE_TOLERANCE_DAYS * 86400


def is_bars_cache_fresh(
    bars: list[BarRow],
    market: str,
    now: datetime | None = None,
    *,
    key: tuple[str, str, str],
) -> bool:
    """Whether a cached window may answer this request instead of the provider.

    The old predicate compared "how old is the last bar" against a TTL, which made
    every weekend and holiday permanently stale — the one time nobody can produce a
    newer bar. The question is not age, it is "which session should this window
    already contain" (R-2(b), I-2):

    * nothing newer can exist — the session closed and the window already holds it,
      so reuse it however old it is;
    * the session due is missing, or a session is still running and today's bar is
      still forming — ask again, but no more often than the TTL. Without a holiday
      table, "closed for Golden Week" and "provider is slow" are indistinguishable,
      and neither may become one provider call per page view.

    A source that stopped producing bars more than ``HISTORICAL_FLOOR_DAYS`` ago
    (delisted symbol, purely historical window) is left alone; the caller's own lag
    warning carries that caveat (``snapshot._bars_warnings``).
    """
    if not bars:
        return False
    moment = now or datetime.now(UTC)
    last_session = datetime.fromtimestamp(bars[-1].ts, tz=UTC)
    if last_session < moment - timedelta(days=HISTORICAL_FLOOR_DAYS):
        return True
    if last_session.date() < latest_session_due(market, moment):
        return _refilled_within(key, market, moment)
    if is_market_open(market, moment):
        return _refilled_within(key, market, moment)
    return True


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
    stored_source = _storage_tag(source)
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
                source=stored_source,
            )
            session.add(row)
        else:
            row.open = bar.open
            row.high = bar.high
            row.low = bar.low
            row.close = bar.close
            row.volume = bar.volume
            row.amount = bar.amount
            row.source = stored_source
    session.flush()
    # The window is as good as the provider can make it right now, which is what the
    # §9 TTL counts from (see `_FETCHED_AT`).
    record_bars_fetch(instrument_id, timeframe, adjust)


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
    if stamped > moment + _FUTURE_SKEW:
        # A stamp from the future is not evidence of freshness — it is evidence that
        # the stamp is not a fetch time. The providers used to write the bar's own
        # close moment here (07:00 UTC while the session is still running), which made
        # the row immortal: `now - stamped` stayed negative forever and the rail kept
        # a price the chart had already moved past (analysis §I-5 / BUG-8). Both real
        # providers now stamp the fetch moment, so refuse the rest instead of trusting
        # them, and let the caller refresh.
        return False
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
