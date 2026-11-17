"""One price per date — the acceptance criteria for BUG-1 / BUG-2 / BUG-3.

`docs/diagnoses/chart-price-integrity/analysis.md` §6.4 R-4/R-5 replaced the
original five criteria with these, because two of them were unsound:

* "每根 K 线由 seed(symbol, date) 独立决定" makes a white-noise chart (trend, MA and
  BOLL stop meaning anything) — the walk is anchored, not per-date;
* "`dayHigh >= prev_close` 逐日成立" is a *false* invariant: 297 of 783 sessions in
  the real cache were legitimate gap-downs, so asserting it would force the
  implementation to fabricate highs.

What is left is checkable, and these tests are the check:

1. any two windows over the same calendar agree bar-for-bar;
2. the same range asked on two different days returns the same history;
3. `quote()` is the tail of the `bars()` walk, field by field (not an inequality);
4. a daily bar still looks like a daily bar (gap < body, high/low bracket o/c);
5. a narrow cache may not answer a wider request;
6. freshness is "do I hold the session that is due" (plus §9's TTL while one is
   running), not "is the bar young";
7. one series never splices two producers, and a stamp from the future is not
   freshness (BUG-8 / §I-5).
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.market_data import cache
from app.services.market_data.base import BarRow
from app.services.market_data.synthetic_provider import (
    KNOWN_SYMBOLS,
    SyntheticProvider,
    _walk_bars,
    base_price,
)

DAY = 86400


@pytest.fixture()
def synthetic() -> SyntheticProvider:
    return SyntheticProvider()


def _window(synthetic: SyntheticProvider, symbol: str, days: int) -> list[BarRow]:
    return synthetic.bars(symbol, timeframe="1d", start_ts=int(time.time()) - days * DAY, adjust="qfq")


# ── 1 + 3: the one-line criterion ────────────────────────────────────────────
def test_last_close_does_not_depend_on_the_window(synthetic: SyntheticProvider) -> None:
    """`range=1m` and `range=3y` must name the same price for the same session."""
    for symbol in ("600519.SH", "AAPL", "0700.HK"):
        short = _window(synthetic, symbol, 31)
        long = _window(synthetic, symbol, 1096)
        assert short and long
        assert short[-1].ts == long[-1].ts
        assert short[-1].close == long[-1].close, f"{symbol}: {short[-1].close} != {long[-1].close}"


def test_windows_overlap_bar_for_bar(synthetic: SyntheticProvider) -> None:
    wide = {bar.ts: bar for bar in _window(synthetic, "600519.SH", 400)}
    narrow = _window(synthetic, "600519.SH", 90)
    assert narrow
    for bar in narrow:
        assert wide[bar.ts] == bar, "the same date must produce the same OHLCV in every window"


def test_history_does_not_change_when_the_day_advances(synthetic: SyntheticProvider) -> None:
    """Today's read of 2026-11-13 must still hold tomorrow (was 0/782).

    This is the criterion the original document missed and the reason a cache fix
    alone could never be enough: `snapshot.py` refetches three years of bars every
    day, so a history that shifts daily rewrites every report's evidence.
    """
    today = datetime.now(UTC).date()
    now = _walk_bars("600519.SH", "1d", "qfq", today)
    later = _walk_bars("600519.SH", "1d", "qfq", today + timedelta(days=1))
    assert len(now) > 7000, "the walk is anchored at a fixed calendar date"
    shared = {bar.ts: bar.close for bar in now}
    changed = [bar.ts for bar in later if bar.ts in shared and shared[bar.ts] != bar.close]
    assert changed == [], f"{len(changed)} historical closes moved"
    # ...and it only ever appends: tomorrow is a superset of today.
    assert {bar.ts for bar in now} <= {bar.ts for bar in later}


# ── 3: quote and bars are one series ─────────────────────────────────────────
@pytest.mark.parametrize("symbol", [row[0] for row in KNOWN_SYMBOLS])
def test_quote_mirrors_the_last_two_bars(symbol: str, synthetic: SyntheticProvider) -> None:
    quote = synthetic.quote(symbol)
    bars = _window(synthetic, symbol, 366)
    last, previous = bars[-1], bars[-2]
    assert quote.last == last.close
    assert quote.prev_close == previous.close
    assert quote.day_high == last.high
    assert quote.day_low == last.low
    assert quote.volume == last.volume
    assert quote.change_pct == pytest.approx(round((last.close / previous.close - 1) * 100, 2))


def test_quote_cannot_claim_a_high_below_the_previous_close_by_construction(
    synthetic: SyntheticProvider,
) -> None:
    """BUG-3's visible symptom, checked where it is legal to check it.

    The illegal state was `dayHigh < prevClose` *while* the day closed above it.
    Gap-down days are exempt (analysis §5.3 R-5): 297/783 real sessions open below
    the previous close and never trade back up, which is perfectly legal.
    """
    for symbol in ("600519.SH", "AAPL"):
        quote = synthetic.quote(symbol)
        bars = _window(synthetic, symbol, 366)
        last = bars[-1]
        assert quote.day_high == last.high
        assert quote.day_low == last.low
        if last.open >= quote.prev_close:  # not a gap-down day
            assert quote.day_high >= quote.prev_close


# ── 4: a daily bar still looks like a daily bar ──────────────────────────────
@pytest.mark.parametrize("symbol", ["600519.SH", "AAPL", "0700.HK"])
def test_overnight_gap_is_smaller_than_the_body(symbol: str, synthetic: SyntheticProvider) -> None:
    """The old open was jittered around *today's* close: gap 1.469% > body 0.351%.

    A real daily bar opens near yesterday's close and moves during the session, so
    the gap must be the smaller of the two (measured after the fix: 0.24% / 1.42%).
    """
    bars = _window(synthetic, symbol, 1096)
    body = [abs(bar.open / bar.close - 1) for bar in bars]
    gap = [abs(bar.open / bars[index - 1].close - 1) for index, bar in enumerate(bars) if index > 0]
    assert statistics.mean(gap) < statistics.mean(body)


def test_prices_stay_in_a_believable_band(synthetic: SyntheticProvider) -> None:
    """R-4(1)'s product-level regression: anchoring alone quotes 600519 at ¥25.71.

    No existing assertion caught that (`test_known_symbols_are_covered_by_anchored_prices`
    only checks `> 0`), so the band is pinned here instead.
    """
    for symbol, _name, _market, _currency, _exchange in KNOWN_SYMBOLS:
        ratio = (synthetic.quote(symbol).last or 0) / base_price(symbol)
        assert 0.3 <= ratio <= 3.0, f"{symbol} drifted to {ratio:.2f}× its anchor"


def test_ohlc_is_self_consistent(synthetic: SyntheticProvider) -> None:
    for bar in _window(synthetic, "600519.SH", 1096):
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)
        assert bar.low > 0
        assert bar.volume > 0


# ── 5 + 6: cache coverage and freshness ──────────────────────────────────────
def test_bars_cover_rejects_a_cache_narrower_than_the_request() -> None:
    now = datetime.now(UTC)
    start_3y = int(time.time()) - 1096 * DAY
    narrow = [BarRow(ts=int((now - timedelta(days=30)).timestamp()), open=1, high=1, low=1, close=1)]
    wide = [BarRow(ts=int((now - timedelta(days=1095)).timestamp()), open=1, high=1, low=1, close=1)]
    assert cache.bars_cover(narrow, start_3y) is False
    assert cache.bars_cover(wide, start_3y) is True
    assert cache.bars_cover([], start_3y) is False


def test_freshness_asks_for_the_newest_closed_session_not_for_a_young_bar() -> None:
    """A weekend must read as fresh — nobody can produce a Saturday bar.

    The old predicate compared `now - last_ts` against a TTL, so every Saturday,
    Sunday and holiday refetched from the provider (and reported `cached=false`).
    """
    moment = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)  # Saturday 12:00 in Shanghai
    made = [
        BarRow(ts=int(datetime(2026, 11, day, 7, 0, tzinfo=UTC).timestamp()), open=1, high=1, low=1, close=1)
        for day in (20, 19)
    ]
    friday_bar, thursday_bar = [made[0]], [made[1]]
    key = ("CN:600519.SH", "1d", "qfq")
    assert cache.latest_closed_session("CN", moment) == date(2026, 11, 20)
    # No refill stamp at all: a closed session needs none, the window is complete.
    cache._FETCHED_AT.pop(key, None)
    assert cache.is_bars_cache_fresh(friday_bar, "CN", moment, key=key) is True
    assert cache.is_bars_cache_fresh(thursday_bar, "CN", moment, key=key) is False


def test_a_running_session_must_not_sit_on_yesterdays_bar() -> None:
    """The rail refreshes every 60 s; the chart may not stay a session behind it.

    Mid-session the newest session due is *today*, so a window that stops at
    yesterday has to be revalidated — otherwise 左栏 shows the live price while 头部
    and the axis label show yesterday's close, which is the two-price screen this
    fix removes. The TTL is what keeps that from becoming one provider call per
    page view, since a holiday and a slow provider look identical here.
    """
    key = ("CN:600519.SH", "1d", "qfq")
    moment = datetime(2026, 11, 18, 2, 0, tzinfo=UTC)  # 10:00 in Shanghai, mid-session
    yesterday = [BarRow(ts=int(datetime(2026, 11, 17, 7, 0, tzinfo=UTC).timestamp()), open=1, high=1, low=1, close=1)]

    cache._FETCHED_AT.pop(key, None)
    assert cache.latest_session_due("CN", moment) == date(2026, 11, 18)
    assert cache.is_bars_cache_fresh(yesterday, "CN", moment, key=key) is False, "never refilled"

    cache.record_bars_fetch(*key, at=moment.timestamp() - 60)  # refilled a minute ago
    assert cache.is_bars_cache_fresh(yesterday, "CN", moment, key=key) is True, "bounded by the TTL"

    cache.record_bars_fetch(*key, at=moment.timestamp() - cache.bars_ttl_seconds("CN", moment) - 1)
    assert cache.is_bars_cache_fresh(yesterday, "CN", moment, key=key) is False, "the TTL expired"


def test_after_the_close_a_complete_window_needs_no_refill_stamp() -> None:
    """The other half of the rule: nothing newer can exist, so age is irrelevant."""
    key = ("CN:600519.SH", "1d", "qfq")
    moment = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)  # 18:00 in Shanghai, closed
    today_bar = [BarRow(ts=int(datetime(2026, 11, 18, 7, 0, tzinfo=UTC).timestamp()), open=1, high=1, low=1, close=1)]
    cache._FETCHED_AT.pop(key, None)
    assert cache.is_bars_cache_fresh(today_bar, "CN", moment, key=key) is True


# ── 5 (storage level): the previous generator's rows must not survive ────────
def test_legacy_generation_rows_are_retired_once(session, instrument) -> None:
    """R-4(3): zero-DDL generation check through the existing `source` column."""
    legacy = [
        BarRow(ts=int(time.time()) - 2 * DAY, open=1.0, high=1.0, low=1.0, close=1.0),
        BarRow(ts=int(time.time()) - DAY, open=2.0, high=2.0, low=2.0, close=2.0),
    ]
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=legacy, source="synthetic-v2")
    session.commit()
    read, source = cache.read_bars(session, instrument.id, timeframe="1d", adjust="qfq", start_ts=0)
    assert len(read) == 2
    assert source == "synthetic", "the public label must stay stable for the UI"

    # Simulate a row left behind by the pre-fix walk.
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=legacy, source="akshare")
    from app.models import Bar

    row = session.query(Bar).first()
    row.source = "synthetic"
    session.commit()

    read, source = cache.read_bars(session, instrument.id, timeframe="1d", adjust="qfq", start_ts=0)
    assert read == [] and source is None, "an older generator's rows must not be served"
    assert session.query(Bar).filter(Bar.source == "synthetic").count() == 0, "and must be retired"


def test_foreign_sources_are_never_purged(session, instrument) -> None:
    bars = [BarRow(ts=int(time.time()) - DAY, open=1.0, high=1.0, low=1.0, close=1.0)]
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=bars, source="akshare")
    session.commit()
    read, source = cache.read_bars(session, instrument.id, timeframe="1d", adjust="qfq", start_ts=0)
    assert len(read) == 1
    assert source == "akshare"


def test_a_series_spliced_from_two_producers_is_not_served(session, instrument) -> None:
    """R-2(c)/R-2(d): a seam between two producers is a fabricated candle.

    Measured on the live database: `/bars?range=3y` answered 783 bars with
    `cached=true, source=akshare` and one **-15.33%** day exactly where a 22-row
    akshare block met 761 synthetic rows. Two producers also mean one attribution
    label for two price levels, so the honest answer is a miss — the caller refills
    the whole window from a single producer.

    ★R-5: `write_bars` can no longer create this state (it takes the series over, see
    the test below), so the splice is planted directly in the table. The gate itself
    must stay: it is what stops a row written before this fix, or by a second
    process, from being served behind a single source label.
    """
    from app.models import Bar

    base = int(time.time()) - 5 * DAY
    for index in range(3):
        session.add(
            Bar(
                instrument_id=instrument.id,
                timeframe="1d",
                ts=base + index * DAY,
                adjust="qfq",
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=0.0,
                source="akshare",
            )
        )
    for index in range(3, 5):
        session.add(
            Bar(
                instrument_id=instrument.id,
                timeframe="1d",
                ts=base + index * DAY,
                adjust="qfq",
                open=2.0,
                high=2.0,
                low=2.0,
                close=2.0,
                volume=0.0,
                source="synthetic-v2",
            )
        )
    session.commit()

    read, source, reason = cache.read_bars_detail(
        session, instrument.id, timeframe="1d", adjust="qfq", start_ts=0
    )
    assert read == [] and source is None, "one series may not splice two producers"
    assert reason == cache.REASON_MIXED_SOURCE, "and the reason must be reportable, not silent"
    assert session.query(Bar).filter(Bar.source == "akshare").count() == 3, "the gate does not delete"


def test_a_write_takes_over_the_whole_series(session, instrument) -> None:
    """★PCD-4 / R-5: "one series, one producer" is a *write-time* invariant.

    Deleting only inside the incoming window does not converge: the live database
    held 726 akshare rows plus 57 `synthetic-v2` ghost rows (a synthetic walk traded
    on Chinese holidays, the real source did not), and with a narrower rewrite the
    mix survived as 281 + 5. Both cases end here as a single producer.
    """
    from app.models import Bar

    base = int(time.time()) - 30 * DAY
    wide = [BarRow(ts=base + index * DAY, open=3.0, high=3.0, low=3.0, close=3.0) for index in range(30)]
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=wide, source="synthetic")
    session.commit()
    assert session.query(Bar).count() == 30

    # The real source comes back with a *narrower* window than it left with.
    narrow = [BarRow(ts=base + (25 + index) * DAY, open=1.0, high=1.0, low=1.0, close=1.0) for index in range(5)]
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=narrow, source="akshare")
    session.commit()

    rows = session.query(Bar).all()
    assert {row.source for row in rows} == {"akshare"}, "the series may hold only one producer"
    assert len(rows) == 5, "the previous producer's rows are not kept next to the new price level"

    read, source, reason = cache.read_bars_detail(
        session, instrument.id, timeframe="1d", adjust="qfq", start_ts=0
    )
    assert len(read) == 5 and source == "akshare" and reason == cache.REASON_OK


def test_a_quote_stamped_in_the_future_is_not_fresh() -> None:
    """§I-5 / BUG-8: a bar's close moment is not a fetch time.

    Both real providers wrote the bar stamp (07:00 UTC) into `updated_at`, so during
    a session `now - stamped` was negative and the row never expired — the rail could
    keep a price the chart had already left behind.
    """
    from app.services.market_data.base import QuoteRow

    moment = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    assert cache.quote_is_fresh(QuoteRow(updated_at="2026-11-17T07:00:00Z"), moment, ttl=60) is False
    assert cache.quote_is_fresh(QuoteRow(updated_at="2026-11-17T03:02:00Z"), moment, ttl=60) is True, "clock skew"


# ── the same criteria through the HTTP surface ───────────────────────────────
def test_bars_endpoint_does_not_let_a_narrow_cache_cap_a_wider_range(client) -> None:
    """BUG-7 / R-2(a): 22 cached bars used to answer `range=3y` with `cached=true`."""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    month = client.get("/api/v1/instruments/CN:600519.SH/bars?timeframe=1d&range=1m&adjust=qfq").json()
    assert 15 <= month["meta"]["barsCount"] <= 25

    quarter_year = client.get("/api/v1/instruments/CN:600519.SH/bars?timeframe=1d&range=3y&adjust=qfq").json()
    assert quarter_year["meta"]["barsCount"] > 700, "a wider range must not be served by the 1m cache"
    assert quarter_year["bars"][-1]["c"] == month["bars"][-1]["c"], "same session, same close"

    again = client.get("/api/v1/instruments/CN:600519.SH/bars?timeframe=1d&range=3y&adjust=qfq").json()
    assert again["meta"]["cached"] is True, "a covered, fresh window is a hit"
    assert again["bars"][-1]["c"] == quarter_year["bars"][-1]["c"]


def test_quote_endpoint_agrees_with_the_bars_endpoint(client) -> None:
    """The screen-level promise: the rail, the header and the axis name one price."""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    quote = client.get("/api/v1/instruments/CN:600519.SH/quote").json()
    bars = client.get("/api/v1/instruments/CN:600519.SH/bars?timeframe=1d&range=1m&adjust=qfq").json()
    assert quote["last"] == bars["bars"][-1]["c"]
    assert quote["dayHigh"] == bars["bars"][-1]["h"]
    assert quote["dayLow"] == bars["bars"][-1]["l"]
