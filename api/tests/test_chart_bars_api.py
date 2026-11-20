"""图表 profile 的预热、周期、缓存原子性和公开读取契约。"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.errors import Unavailable
from app.models import Bar
from app.services.market_data import cache, registry
from app.services.market_data.base import (
    BarRow,
    BarsResult,
    ProviderUnavailable,
    QuoteRow,
)
from app.services.market_data.registry import ChainResult

NOW = datetime(2026, 11, 20, 22, tzinfo=UTC)
DAY = 86400
CALCULATION_START = int(
    (NOW.replace(hour=0, minute=0, second=0) - timedelta(days=3653))
    .timestamp()
)


def candle(day: str, close: float = 100.0, **changes) -> BarRow:
    ts = int(datetime.fromisoformat(day).replace(hour=7, tzinfo=UTC).timestamp())
    return replace(BarRow(ts, close, close, close, close, 1000.0), **changes)


class StubRegistry:
    def __init__(self, rows=None, source="akshare", error=None):
        self.rows = rows if rows is not None else [candle("2026-11-20")]
        self.source = source
        self.error = error
        self.warnings = []
        self.calls = []

    async def bars(self, symbol, **request):
        self.calls.append((symbol, request))
        if self.error is not None:
            raise self.error
        result = BarsResult(self.rows, self.source, list(self.warnings))
        return ChainResult(result, self.source, list(self.warnings))


@pytest.fixture()
def stub(monkeypatch):
    provider = StubRegistry()
    monkeypatch.setattr(registry, "_registry", provider)
    return provider


def history(monkeypatch):
    module = importlib.import_module("app.services.chart_history")
    monkeypatch.setattr(module, "_utc_now", lambda: NOW)
    return module


def load(session, instrument, monkeypatch, **request):
    module = history(monkeypatch)
    return asyncio.run(module.load_chart_bars(
        session, instrument, module.ChartBarsRequest(**request),
    ))


def stored(session, instrument):
    session.expire_all()
    return list(session.scalars(
        select(Bar).where(Bar.instrument_id == instrument.id).order_by(Bar.ts)
    ))


def seed(session, instrument, rows, source="akshare"):
    cache.write_bars(
        session, instrument.id, timeframe="1d", adjust="qfq",
        bars=rows, source=source,
    )
    session.commit()


def test_chart_profile_returns_complete_contract(client, stub, instrument) -> None:
    response = client.get(
        "/api/v1/instruments/CN:600519.SH/bars?profile=chart"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("chart") is not None
    assert payload["chart"]["version"] == "chart/v1"
    assert len(payload["indicators"]) == 19
    assert all(len(v) == len(payload["bars"])
               for v in payload["indicators"].values())


def test_profile_enum_rejects_unknown_values(client, instrument) -> None:
    assert client.get(
        "/api/v1/instruments/CN:600519.SH/bars?profile=typo"
    ).status_code == 422


def test_standard_keeps_legacy_rsi_and_null_chart(client, stub, instrument) -> None:
    stub.rows = [candle(f"2026-11-{day:02d}") for day in range(1, 21)]
    payload = client.get(
        "/api/v1/instruments/CN:600519.SH/bars?profile=standard"
    ).json()
    assert payload["chart"] is None
    assert payload["indicators"]["rsi14"][-1] == 100
    assert payload["indicators"]["ema12"] == []


def test_chart_is_public_for_guest(auth_client, stub, instrument) -> None:
    response = auth_client.get(
        "/api/v1/instruments/CN:600519.SH/bars?profile=chart"
    )
    assert response.status_code == 200
    assert response.json()["chart"]["version"] == "chart/v1"


def test_prewarm_and_previous_close_are_independent_of_range(
    session, instrument, monkeypatch, stub,
) -> None:
    start = NOW - timedelta(days=400)
    stub.rows = [
        BarRow(int((start + timedelta(days=i)).timestamp()),
               i + 1, i + 1, i + 1, i + 1, 1)
        for i in range(400)
    ]
    month = load(session, instrument, monkeypatch, range="1m")
    year = load(session, instrument, monkeypatch, range="1y")
    by_ts = {bar.ts: i for i, bar in enumerate(year.bars)}
    assert month.chart.stats[0].previousClose is not None
    assert month.chart.warmupBarsCount > 300
    for index, bar in enumerate(month.bars):
        other = by_ts[bar.ts]
        assert month.chart.stats[index] == year.chart.stats[other]
        for name, values in month.indicators.model_dump().items():
            assert values[index] == year.indicators.model_dump()[name][other]
    assert all(call[1]["timeframe"] == "1d" for call in stub.calls)
    assert all(call[1]["start_ts"] == CALCULATION_START for call in stub.calls)


@pytest.mark.parametrize("timeframe", ["1w", "1m"])
def test_period_aggregation_uses_previous_period_close(
    session, instrument, monkeypatch, stub, timeframe,
) -> None:
    stub.rows = [
        candle("2026-10-30", 100, volume=100),
        candle("2026-11-02", 108, open=102, high=110, low=99, volume=200),
        candle("2026-11-03", 110, open=108, high=112, low=107, volume=300),
    ]
    result = load(session, instrument, monkeypatch, timeframe=timeframe)
    last = result.bars[-1]
    stats = result.chart.stats[-1]
    assert (last.o, last.h, last.l, last.c, last.v) == (102, 112, 99, 110, 500)
    assert stats.periodStart == "2026-11-02"
    assert stats.periodEnd == "2026-11-03"
    assert stats.previousClose == 100
    assert stats.changePct == 10
    assert stats.amplitudePct == 13
    assert last.amount is None


@pytest.mark.parametrize("timeframe", ["1w", "1m"])
def test_new_listing_only_bucket_is_not_deleted(
    session, instrument, monkeypatch, stub, timeframe,
) -> None:
    result = load(session, instrument, monkeypatch, timeframe=timeframe)
    assert len(result.bars) == 1
    assert result.chart.calculationBarsCount == 1
    assert result.chart.historyLimited is True
    assert result.chart.stats[0].leadingPartial is False


@pytest.mark.parametrize("timeframe", ["1w", "1m"])
def test_calculation_boundary_bucket_remains_visible_but_excluded(
    session, instrument, monkeypatch, stub, timeframe,
) -> None:
    module = history(monkeypatch)
    first = datetime.fromtimestamp(CALCULATION_START, UTC)
    stub.rows = [
        candle(first.date().isoformat()),
        candle((first + timedelta(days=40)).date().isoformat(), 105),
    ]
    request = module.ChartBarsRequest(timeframe=timeframe, range="5y")
    # Force only the display window to include the ten-year calculation boundary.
    monkeypatch.setattr(module, "range_to_epoch", lambda *a, **k: CALCULATION_START)
    result = asyncio.run(module.load_chart_bars(session, instrument, request))
    assert len(result.bars) == 2
    assert result.chart.stats[0].leadingPartial is True
    assert result.chart.stats[0].previousClose is None
    assert result.chart.stats[1].previousClose is None
    assert all(values[0] is None
               for values in result.indicators.model_dump().values())
    assert result.chart.calculationBarsCount == 1


def test_empty_display_slice_has_complete_contract(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.rows = [candle("2025-01-02")]
    result = load(session, instrument, monkeypatch, range="1m")
    assert result.bars == [] and result.chart.stats == []
    assert result.meta.fromTs is None and result.meta.toTs is None
    assert result.chart.warmupBarsCount == result.chart.calculationBarsCount == 1
    assert all(values == [] for values in result.indicators.model_dump().values())


@pytest.mark.parametrize("change", [
    {"high": 90}, {"close": float("nan")}, {"low": float("-inf")},
    {"volume": -1}, {"amount": -1}, {"ts": 1.5}, {"ts": True},
    {"ts": 10**30},
])
def test_invalid_provider_never_replaces_cache_or_refill_time(
    session, instrument, monkeypatch, stub, change,
) -> None:
    good = candle("2026-11-19")
    seed(session, instrument, [good])
    key = (instrument.id, "1d", "qfq")
    old_stamp = cache._FETCHED_AT[key]
    stub.rows = [replace(good, **change)]
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch, force=True)
    assert caught.value.detail["reason"] == "invalid_chart_series"
    assert [(r.ts, r.close) for r in stored(session, instrument)] == [(good.ts, good.close)]
    assert cache._FETCHED_AT[key] == old_stamp


def test_identical_duplicate_is_deduped_but_conflicts_and_two_day_stamps_fail(
    session, instrument, monkeypatch, stub,
) -> None:
    good = candle("2026-11-20")
    stub.rows = [good, good]
    assert len(load(session, instrument, monkeypatch).bars) == 1
    for duplicate in [replace(good, volume=2), replace(good, ts=good.ts + 3600)]:
        stub.rows = [good, duplicate]
        with pytest.raises(Unavailable):
            load(session, instrument, monkeypatch, force=True)


def test_aggregation_overflow_is_rejected_before_cache_write(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.rows = [candle("2026-11-02", volume=1e308),
                 candle("2026-11-03", volume=1e308)]
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch, timeframe="1w")
    assert caught.value.detail["reason"] == "invalid_chart_series"
    assert stored(session, instrument) == []


def test_history_limit_rejects_instead_of_silently_truncating(
    session, instrument, monkeypatch, stub,
) -> None:
    # More than 4000 valid timestamp/day samples cannot fit in a ten-year window;
    # inject the lower bound to exercise the hard limit independently of the clock.
    module = history(monkeypatch)
    monkeypatch.setattr(module, "CALCULATION_DAYS", 5000)
    first = NOW - timedelta(days=4500)
    stub.rows = [BarRow(int((first + timedelta(days=i)).timestamp()),
                        100, 100, 100, 100) for i in range(4001)]
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch)
    assert caught.value.detail["reason"] == "chart_history_limit"


def test_same_source_refill_removes_withdrawn_rows_atomically(
    session, instrument, monkeypatch, stub,
) -> None:
    seed(session, instrument, [candle("2026-11-18"), candle("2026-11-19")])
    stub.rows = [candle("2026-11-19", 110)]
    load(session, instrument, monkeypatch, force=True)
    assert [(row.ts, row.close) for row in stored(session, instrument)] == [
        (stub.rows[0].ts, 110),
    ]


def test_commit_failure_restores_database_and_original_refill_stamp(
    session, instrument, monkeypatch, stub,
) -> None:
    old = candle("2026-11-18")
    seed(session, instrument, [old])
    key = (instrument.id, "1d", "qfq")
    stamp = cache._FETCHED_AT[key]
    def fail_commit():
        raise RuntimeError("commit failed")
    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        load(session, instrument, monkeypatch, force=True)
    assert cache._FETCHED_AT[key] == stamp
    assert [(r.ts, r.close) for r in stored(session, instrument)] == [(old.ts, old.close)]


def test_provider_exception_falls_back_only_to_valid_single_source_cache(
    session, instrument, monkeypatch, stub,
) -> None:
    seed(session, instrument, [candle("2026-11-19")])
    stub.error = ProviderUnavailable("offline")
    result = load(session, instrument, monkeypatch, force=True)
    assert result.meta.cached is True
    assert result.meta.source == "akshare"
    assert result.meta.reasonCode == "provider_fallback"
    assert result.meta.warnings and result.meta.stale
    stored(session, instrument)[0].volume = -1
    session.commit()
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch, force=True)
    assert caught.value.detail["reason"] == "invalid_chart_series"


def test_covered_fresh_cache_does_not_call_provider(
    session, instrument, monkeypatch, stub,
) -> None:
    first_day = datetime.fromtimestamp(CALCULATION_START, UTC).date().isoformat()
    seed(session, instrument, [candle(first_day), candle("2026-11-20")])
    result = load(session, instrument, monkeypatch)
    assert result.meta.cached is True and stub.calls == []


@pytest.mark.parametrize("source,market,unit,size,effective", [
    ("akshare", "CN", "lot", 100, "hfq"),
    ("yfinance", "US", "share", 1, "auto"),
    ("synthetic", "CN", "unit", None, "synthetic"),
    ("other", "HK", "unknown", None, "hfq"),
])
def test_volume_and_adjust_metadata_are_honest(
    session, instrument, monkeypatch, stub, source, market, unit, size, effective,
) -> None:
    stub.source = source
    instrument.market = market
    result = load(session, instrument, monkeypatch, adjust="hfq")
    assert result.chart.volumeUnit == unit
    assert result.chart.volumeUnitSize == size
    assert result.chart.effectiveAdjust == effective
    assert result.bars[0].v == 1000


def test_session_dates_use_exchange_date_not_utc(
    session, instrument, monkeypatch, stub,
) -> None:
    local_midnight = datetime.fromisoformat("2026-11-02T00:00:00+08:00")
    stub.rows = [replace(candle("2026-11-02"), ts=int(local_midnight.timestamp()))]
    result = load(session, instrument, monkeypatch)
    assert result.chart.stats[0].periodStart == "2026-11-02"
    assert result.chart.stats[0].periodEnd == "2026-11-02"


@pytest.mark.parametrize("previous", [0, -10])
def test_nonpositive_previous_close_never_fabricates_percentages(
    session, instrument, monkeypatch, stub, previous,
) -> None:
    stub.rows = [candle("2026-11-19", previous),
                 candle("2026-11-20", 1, volume=0, amount=None)]
    result = load(session, instrument, monkeypatch)
    stat = result.chart.stats[-1]
    assert stat.previousClose == previous
    assert stat.change is None and stat.changePct is None
    assert stat.amplitudePct is None
    assert result.bars[-1].v == 0 and result.bars[-1].amount is None


def test_unsorted_history_and_future_rows_are_normalized_once(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.rows = [candle("2026-11-21"), candle("2026-11-20"),
                 candle("2026-11-19"), candle("2000-01-01")]
    result = load(session, instrument, monkeypatch)
    assert [stat.periodEnd for stat in result.chart.stats] == [
        "2026-11-19", "2026-11-20",
    ]
    assert len(stored(session, instrument)) == 2


def test_invalid_cache_can_be_repaired_by_valid_provider(
    session, instrument, monkeypatch, stub,
) -> None:
    seed(session, instrument, [candle("2026-11-19", volume=-1)])
    result = load(session, instrument, monkeypatch)
    assert result.meta.cached is False
    assert len(stub.calls) == 1
    assert [(r.ts, r.volume) for r in stored(session, instrument)] == [
        (stub.rows[0].ts, 1000),
    ]


def test_empty_provider_package_is_valid_and_replaces_requested_slice(
    session, instrument, monkeypatch, stub,
) -> None:
    seed(session, instrument, [candle("2026-11-19")])
    stub.rows = []
    result = load(session, instrument, monkeypatch, force=True)
    assert result.bars == [] and result.chart.calculationBarsCount == 0
    assert result.chart.stats == [] and result.chart.historyLimited
    assert stored(session, instrument) == []


def test_provider_failure_without_good_cache_raises_503(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.error = ProviderUnavailable("offline")
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch)
    assert caught.value.detail == {"provider": "chain"}


def test_huge_python_numeric_is_rejected_as_invalid_chart_series(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.rows = [candle("2026-11-20", volume=10**1000)]
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch)
    assert caught.value.detail["reason"] == "invalid_chart_series"


def test_chart_error_has_existing_stable_http_envelope(client, stub, instrument):
    stub.rows = [candle("2026-11-20", volume=-1)]
    response = client.get(
        "/api/v1/instruments/CN:600519.SH/bars?profile=chart"
    )
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "data_provider_unavailable"
    assert error["detail"]["reason"] == "invalid_chart_series"


def test_source_divergence_keeps_original_chain_warnings(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.warnings = ["首个数据源离线，已回退"]
    cache.write_quote(session, instrument.id, QuoteRow(
        last=200, source="yfinance", updated_at=NOW.isoformat(),
    ))
    session.commit()
    result = load(session, instrument, monkeypatch)
    assert result.meta.source == "akshare"
    assert result.meta.reasonCode == "divergent_source"
    assert result.meta.warnings[0] == stub.warnings[0]
    assert "yfinance" in result.meta.warnings[1]


def test_yfinance_none_is_reported_as_unadjusted(
    session, instrument, monkeypatch, stub,
) -> None:
    stub.source = "yfinance"
    result = load(session, instrument, monkeypatch, adjust="none")
    assert result.chart.effectiveAdjust == "none"


def test_legacy_synthetic_cache_is_not_mutated_on_invalid_refill(
    session, instrument, monkeypatch, stub,
) -> None:
    old = candle("2026-11-19")
    # Legacy synthetic is deliberately not written through cache.write_bars,
    # which tags all newly produced synthetic rows with the current generation.
    session.add(Bar(
        instrument_id=instrument.id, timeframe="1d", adjust="qfq", ts=old.ts,
        open=100, high=100, low=100, close=100, volume=1000,
        source="synthetic",
    ))
    session.commit()
    stub.rows = [candle("2026-11-20", volume=-1)]
    with pytest.raises(Unavailable):
        load(session, instrument, monkeypatch)
    assert [row.source for row in stored(session, instrument)] == ["synthetic"]


def test_registry_presort_failure_is_an_invalid_response_not_cache_fallback(
    session, instrument, monkeypatch,
) -> None:
    old = candle("2026-11-19")
    seed(session, instrument, [old])

    class CorruptProvider:
        def available(self):
            return True

        def bars(self, *args, **kwargs):
            return [replace(old, ts=None), old]

    real_registry = registry.ProviderRegistry()
    real_registry._instances = {"bad": CorruptProvider()}
    monkeypatch.setattr(registry, "_registry", real_registry)
    with pytest.raises(Unavailable) as caught:
        load(session, instrument, monkeypatch, force=True)
    assert caught.value.detail["reason"] == "invalid_chart_series"
    assert [r.ts for r in stored(session, instrument)] == [old.ts]
