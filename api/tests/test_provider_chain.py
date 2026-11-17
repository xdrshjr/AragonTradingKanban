"""The acceptance criteria of `docs/diagnoses/provider-chain-divergence/analysis.md`.

Each test names the AC it covers. The eight criteria in the analysis' 评审结论 were
written to be machine-judgable; AC5 and AC7 (the ones that need a browser) live in
`web/tests/rail-quotes.test.tsx`.

The defect being closed, in one line: `/quote`, `/bars` and the report each walked the
provider chain on their own, so one screen could show two prices from two sources, the
周/月 buttons were a guaranteed 503, a monthly window could never be a cache hit, and a
single ghost row made every wider range refetch forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.market_data.base import BarRow, ProviderError, ProviderErrorKind, QuoteRow

DAY = 86400


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _days_ago(days: int) -> int:
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp())


QUESTIONS = [
    ("600519", "600519.SH"),
    ("0700", "0700.HK"),
    ("AAPL", "AAPL"),
]


def _instrument_id(client, query: str) -> str:
    resolved = client.post("/api/v1/symbols/resolve", json={"query": query})
    assert resolved.status_code == 200, resolved.text
    return resolved.json()["instrument"]["id"]


# ── AC1: all three timeframes answer, and a bucketed series ends where the daily one
#         ends (same ts, same close) ────────────────────────────────────────────
@pytest.mark.parametrize(("query", "symbol"), QUESTIONS)
def test_every_timeframe_answers_and_shares_the_daily_tail(client, query: str, symbol: str) -> None:
    instrument_id = _instrument_id(client, query)
    tails: dict[str, tuple[int, float]] = {}
    for timeframe in ("1d", "1w", "1m"):
        response = client.get(
            f"/api/v1/instruments/{instrument_id}/bars?timeframe={timeframe}&range=3y&adjust=qfq"
        )
        assert response.status_code != 503, f"{symbol} {timeframe}: {response.text}"
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["meta"]["barsCount"] > 0, f"{symbol} {timeframe} came back empty"
        tail = payload["bars"][-1]
        tails[timeframe] = (tail["ts"], tail["c"])
        # The chain must never be able to say "所有数据源都失败了" for a timeframe that
        # the UI offers as a button (PCD-1's user-visible form).
        assert payload["meta"]["reasonCode"] != "unsupported_timeframe"

    assert tails["1w"] == tails["1d"], "周线的末根必须就是日线的末根（同一场、同一价）"
    assert tails["1m"] == tails["1d"], "月线的末根必须就是日线的末根"

    # ...and the bucket count is a week/month count, not a bar count.
    weekly = client.get(f"/api/v1/instruments/{instrument_id}/bars?timeframe=1w&range=3y").json()
    monthly = client.get(f"/api/v1/instruments/{instrument_id}/bars?timeframe=1m&range=3y").json()
    assert 140 <= weekly["meta"]["barsCount"] <= 165, weekly["meta"]["barsCount"]
    assert 30 <= monthly["meta"]["barsCount"] <= 40, monthly["meta"]["barsCount"]


# ── AC2: the monthly window is a cache hit (R-1: the bucket-anchored tolerance) ──
def test_monthly_windows_are_cache_hits(client, monkeypatch) -> None:
    from app.services.market_data.registry import get_registry

    instrument_id = _instrument_id(client, "600519")
    registry = get_registry()
    provider = registry._instances["synthetic"]
    calls = {"n": 0}
    original = provider.bars

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "bars", counted)

    first = client.get(f"/api/v1/instruments/{instrument_id}/bars?timeframe=1m&range=3y&adjust=qfq").json()
    assert first["meta"]["cached"] is False, "the first request has to fill the cache"
    after_first = calls["n"]

    second = client.get(f"/api/v1/instruments/{instrument_id}/bars?timeframe=1m&range=3y&adjust=qfq").json()
    assert second["meta"]["cached"] is True, "R-1: a monthly window must be a cache hit, not a permanent miss"
    assert calls["n"] == after_first, "a hit may not call the provider again"
    assert second["bars"][-1]["c"] == first["bars"][-1]["c"]


# ── AC3: an injected split chain says so, in words and in a code ────────────────
class _QuoteOnlyProvider:
    """Answers `/quote` only — a realtime feed with no history."""

    name = "alpha"

    def available(self) -> bool:
        return True

    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        raise ProviderError(ProviderErrorKind.notfound, "alpha has no bars", provider=self.name)

    def quote(self, symbol: str) -> QuoteRow:
        return QuoteRow(
            last=1000.0,
            change_pct=1.0,
            prev_close=990.0,
            day_high=1005.0,
            day_low=985.0,
            volume=1000.0,
            updated_at=_now_iso(),
            source=self.name,
        )

    def search(self, query: str, limit: int):  # pragma: no cover
        return []

    def fundamentals(self, symbol: str, periods: int):  # pragma: no cover
        return []


class _BarsOnlyProvider:
    """Answers `/bars` only — a daily archive with no realtime quote."""

    name = "beta"

    def available(self) -> bool:
        return True

    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        rows = [
            BarRow(
                ts=_days_ago(offset),
                open=2000.0,
                high=2020.0,
                low=1980.0,
                close=2000.0,
                volume=2000.0,
            )
            for offset in range(120, 0, -1)
        ]
        return [row for row in rows if row.ts >= start_ts]

    def quote(self, symbol: str) -> QuoteRow:
        raise ProviderError(ProviderErrorKind.notfound, "beta has no quote", provider=self.name)

    def search(self, query: str, limit: int):  # pragma: no cover
        return []

    def fundamentals(self, symbol: str, periods: int):  # pragma: no cover
        return []


@pytest.fixture()
def split_chain(monkeypatch):
    """The topology the analysis' repro injects: one provider per endpoint."""
    from app.services.market_data import registry as registry_module

    registry = registry_module.get_registry()
    original = dict(registry._instances)
    registry._instances = {"alpha": _QuoteOnlyProvider(), "beta": _BarsOnlyProvider()}
    yield registry
    registry._instances = original


def test_a_split_chain_is_named_not_hidden(client, instrument, split_chain) -> None:
    quote = client.get(f"/api/v1/instruments/{instrument.id}/quote")
    assert quote.status_code == 200, quote.text
    assert quote.json()["source"] == "alpha"

    bars = client.get(f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1m&adjust=qfq")
    assert bars.status_code == 200, "no endpoint may be knocked over to make the sources agree (R-6)"
    meta = bars.json()["meta"]
    assert meta["source"] == "beta"
    assert meta["reasonCode"] == "divergent_source", meta
    readable = [line for line in meta["warnings"] if "来源" in line]
    assert readable, f"a divergence must be said out loud: {meta['warnings']}"
    assert not readable[0].startswith(("alpha", "beta")), "not a provider-internal message"
    assert quote.json()["last"] != bars.json()["bars"][-1]["c"], "the two prices really do differ"


# ── AC4: on a single-source chain the two endpoints must agree (regression net) ──
def test_one_source_chain_names_one_price(client, instrument) -> None:
    client.get(f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1m&adjust=qfq")
    quote = client.get(f"/api/v1/instruments/{instrument.id}/quote").json()
    bars = client.get(f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1m&adjust=qfq").json()
    assert quote["source"] == bars["meta"]["source"]
    assert quote["last"] == bars["bars"][-1]["c"]
    assert bars["meta"]["reasonCode"] == "ok", bars["meta"]
    assert bars["meta"]["warnings"] == []


# ── AC8: `stale` may only mean "the newest bar lags the session that is due" ────
class _DeadProvider:
    name = "dead"

    def available(self) -> bool:
        return False

    def bars(self, *args, **kwargs):  # pragma: no cover - never reached
        raise AssertionError

    def quote(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError

    def search(self, *args, **kwargs):  # pragma: no cover
        return []

    def fundamentals(self, *args, **kwargs):  # pragma: no cover
        return []


class _StaleProvider:
    """A provider that only has history: its last bar is two months old."""

    name = "archive"

    def available(self) -> bool:
        return True

    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        rows = [
            BarRow(ts=_days_ago(offset), open=10.0, high=11.0, low=9.0, close=10.0, volume=1.0)
            for offset in range(200, 59, -1)
        ]
        return [row for row in rows if row.ts >= start_ts]

    def quote(self, symbol: str) -> QuoteRow:  # pragma: no cover - not asked
        raise ProviderError(ProviderErrorKind.notfound, "no quote", provider=self.name)

    def search(self, query: str, limit: int):  # pragma: no cover
        return []

    def fundamentals(self, symbol: str, periods: int):  # pragma: no cover
        return []


@pytest.fixture()
def chain_swap(monkeypatch):
    from app.services.market_data import registry as registry_module

    registry = registry_module.get_registry()
    original = dict(registry._instances)

    def install(*providers):
        registry._instances = {provider.name: provider for provider in providers}

    yield install
    registry._instances = original


def test_provider_warnings_do_not_make_current_data_look_stale(client, instrument, chain_swap) -> None:
    """R-7: `stale=bool(warnings)` called a perfectly current window 陈旧."""
    from app.services.market_data.synthetic_provider import SyntheticProvider

    chain_swap(_DeadProvider(), SyntheticProvider())
    payload = client.get(f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1m").json()
    assert payload["meta"]["warnings"], "the dead provider must still be reported"
    assert payload["meta"]["reasonCode"] == "provider_fallback"
    assert payload["meta"]["stale"] is False, "nothing about this window is out of date"

    # ...and the flag still fires for the one thing it is allowed to mean.
    chain_swap(_StaleProvider())
    stale_payload = client.get(
        f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1y&force=1"
    ).json()
    assert stale_payload["meta"]["barsCount"] > 0
    assert stale_payload["meta"]["stale"] is True, "the newest bar is two months behind"


# ── AC6 (read side): a dirty series is repaired and the reason is reportable ────
def test_a_dirty_series_is_repaired_by_the_refill(client, instrument, session) -> None:
    from app.models import Bar

    base = _days_ago(20)
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

    payload = client.get(f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1m").json()
    assert payload["meta"]["reasonCode"] == "mixed_source", payload["meta"]

    producers = {
        row.source
        for row in session.query(Bar).filter(Bar.instrument_id == instrument.id).all()
    }
    assert producers == {"synthetic-v2"}, f"one series, one producer — got {producers}"


# ── PCD-5 / §6.1 #17: the quote exposes `cached`, not "source (cached)" ─────────
def test_quote_carries_a_cached_flag_instead_of_a_mangled_source(client, instrument) -> None:
    first = client.get(f"/api/v1/instruments/{instrument.id}/quote").json()
    assert first["cached"] is False
    assert first["source"] == "synthetic"

    second = client.get(f"/api/v1/instruments/{instrument.id}/quote").json()
    assert second["cached"] is True, "the second read inside the 60 s TTL is a cache hit"
    assert second["source"] == "synthetic", "the source field must stay a source"


# ── the resampler itself: buckets are the week/month's extremes, tail is the day ─
def test_resample_daily_keeps_the_tail_and_the_extremes() -> None:
    from app.services.market_data.base import resample_daily

    monday = int(datetime(2026, 3, 2, 7, 0, tzinfo=UTC).timestamp())
    rows = [
        BarRow(ts=monday, open=10.0, high=11.0, low=9.0, close=10.5, volume=100.0, amount=1000.0),
        BarRow(ts=monday + DAY, open=10.5, high=12.5, low=10.0, close=12.0, volume=200.0, amount=2000.0),
        BarRow(ts=monday + 2 * DAY, open=12.0, high=12.2, low=8.0, close=9.0, volume=300.0, amount=3000.0),
        BarRow(ts=monday + 7 * DAY, open=9.0, high=9.5, low=8.5, close=9.2, volume=50.0, amount=500.0),
    ]
    weekly = resample_daily(rows, "1w")
    assert len(weekly) == 2
    first = weekly[0]
    assert first.ts == monday + 2 * DAY, "the bucket is stamped at its last bar"
    assert (first.open, first.high, first.low, first.close) == (10.0, 12.5, 8.0, 9.0)
    assert first.volume == 600.0
    assert first.amount == 6000.0
    assert weekly[-1].ts == rows[-1].ts and weekly[-1].close == rows[-1].close

    monthly = resample_daily(rows, "1m")
    assert len(monthly) == 1 and monthly[0].ts == rows[-1].ts
    assert monthly[0].high == 12.5 and monthly[0].low == 8.0

    # 1d and unknown timeframes are untouched.
    assert resample_daily(rows, "1d") == rows


def test_buckets_are_cut_on_the_session_date_not_the_utc_date() -> None:
    """The bucket edges must land on *sessions*, not on UTC midnights.

    yfinance stamps a daily bar at the exchange's own midnight, so
    `2026-06-01 00:00 +08:00` arrives as `2026-05-31 16:00 UTC` — the **previous**
    UTC day. Cutting buckets on the raw UTC date filed that Monday close under May
    and under ISO week 22, i.e. it invented a monthly open for a month the bar is
    not in. Both real providers must survive the same aggregation, so the resampler
    takes the market's clock (``market=``) and 07:00 UTC stamps are unaffected.
    """
    from app.services.market_data.base import resample_daily

    monday = int(datetime(2026, 5, 31, 16, 0, tzinfo=UTC).timestamp())  # = 2026-06-01 00:00 +08:00
    rows = [
        BarRow(ts=monday - DAY, open=10.0, high=11.0, low=9.0, close=10.5, volume=1.0),
        BarRow(ts=monday, open=20.0, high=21.0, low=19.0, close=20.5, volume=1.0),
    ]

    # Without the session clock both bars fall into the same UTC month and ISO week.
    assert len(resample_daily(rows, "1m")) == 1
    assert len(resample_daily(rows, "1w")) == 1

    # With it, the Monday close opens a June bucket and a new ISO week.
    monthly = resample_daily(rows, "1m", market="CN")
    assert len(monthly) == 2
    assert (monthly[-1].ts, monthly[-1].open, monthly[-1].close) == (monday, 20.0, 20.5)
    assert len(resample_daily(rows, "1w", market="CN")) == 2

    # A 07:00 UTC stamp (akshare / synthetic) is already the session close — adding
    # +8 must not move it: two conventions for one session have to end up in ONE bucket.
    akshare_like = BarRow(
        ts=int(datetime(2026, 6, 1, 7, 0, tzinfo=UTC).timestamp()),  # 15:00 Asia/Shanghai
        open=20.0,
        high=21.0,
        low=19.0,
        close=20.5,
        volume=1.0,
    )
    blended = resample_daily([akshare_like, rows[-1]], "1m", market="CN")
    assert len(blended) == 1, "同一场、两种戳法，必须落进同一根月线"
    assert blended[0].ts == akshare_like.ts and blended[0].close == akshare_like.close


def test_a_retired_generation_is_its_own_reason_not_a_dirty_series(client, instrument, session) -> None:
    """A generation retirement is a one-time migration, not corruption.

    Both cases miss the cache and both refill, but they say different things: keeping
    one code for the two would tell every reader of an upgraded install that its
    cache is dirty — forever, since `reasonCode` is what `/bars.meta` reports.
    """
    from app.models import Bar

    base = _days_ago(20)
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
                source="synthetic",  # the pre-fix walk's tag, i.e. an older generation
            )
        )
    session.commit()

    payload = client.get(f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1m").json()
    assert payload["meta"]["reasonCode"] == "legacy_generation", payload["meta"]
    assert payload["meta"]["barsCount"] > 0, "the refill still answers the request"
    assert session.query(Bar).filter(Bar.source == "synthetic").count() == 0


# ── I-1: a provider flip may not put two prices on two timeframes ───────
class _FlipProvider:
    """Answers the daily request, at a price the previous provider never used."""

    name = "flip"

    def available(self) -> bool:
        return True

    def bars(self, symbol: str, *, timeframe: str, start_ts: int, adjust: str) -> list[BarRow]:
        assert timeframe == "1d", f"周/月不该被传给 provider，而是由日线派生（拿到 {timeframe}）"
        rows = [
            BarRow(ts=_days_ago(offset), open=7.0, high=7.5, low=6.5, close=7.0, volume=1.0)
            for offset in range(200, 0, -1)
        ]
        return [row for row in rows if row.ts >= start_ts]

    def quote(self, symbol: str) -> QuoteRow:  # pragma: no cover - not asked
        raise ProviderError(ProviderErrorKind.notfound, "no quote", provider=self.name)

    def search(self, query: str, limit: int):  # pragma: no cover
        return []

    def fundamentals(self, symbol: str, periods: int):  # pragma: no cover
        return []


def test_a_provider_flip_cannot_separate_the_weekly_price_from_the_daily_one(
    client, instrument, chain_swap
) -> None:
    """I-1: deriving 周/月 *below* the cache gave each timeframe its own chain.

    Measured before this fix, on this machine, for one window: `?timeframe=1d`
    answered 262 bars from `synthetic` (1,753.06) while `?timeframe=1w` answered 52
    buckets from `akshare` (1,439.78) — the same screen's 日 and 周 telling two
    prices, which is the defect this round exists to remove.
    """
    from app.services.market_data.synthetic_provider import SyntheticProvider

    daily_url = f"/api/v1/instruments/{instrument.id}/bars?timeframe=1d&range=1y&adjust=qfq"
    weekly_url = f"/api/v1/instruments/{instrument.id}/bars?timeframe=1w&range=1y&adjust=qfq"

    chain_swap(SyntheticProvider())
    daily = client.get(daily_url).json()
    assert daily["meta"]["source"] == "synthetic"

    # The provider that wins the next refill is a different one, at a different price.
    chain_swap(_FlipProvider())
    weekly = client.get(weekly_url).json()
    assert weekly["bars"][-1]["c"] == daily["bars"][-1]["c"], (
        "周线必须就是同一条日线抽出来的——换源也不能让两个周期说两个价"
    )
    assert weekly["meta"]["source"] == daily["meta"]["source"], "同一条序列，同一个来源"
