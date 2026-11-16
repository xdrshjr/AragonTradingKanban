"""Provider chain fallback, caching and offline determinism (spec §9)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.market_data import cache
from app.services.market_data.base import ProviderError, ProviderErrorKind
from app.services.market_data.registry import ProviderRegistry
from app.services.market_data.synthetic_provider import KNOWN_SYMBOLS, SyntheticProvider


@pytest.fixture()
def synthetic() -> SyntheticProvider:
    return SyntheticProvider()


def test_synthetic_is_always_available(synthetic: SyntheticProvider) -> None:
    assert synthetic.available() is True


def test_synthetic_bars_are_deterministic(synthetic: SyntheticProvider) -> None:
    start = int(time.time()) - 120 * 86400
    first = synthetic.bars("600519.SH", timeframe="1d", start_ts=start, adjust="qfq")
    second = synthetic.bars("600519.SH", timeframe="1d", start_ts=start, adjust="qfq")
    assert [bar.ts for bar in first] == [bar.ts for bar in second]
    assert [bar.close for bar in first] == [bar.close for bar in second]


def test_synthetic_bars_are_ohlc_consistent(synthetic: SyntheticProvider) -> None:
    bars = synthetic.bars("AAPL", timeframe="1d", start_ts=int(time.time()) - 60 * 86400, adjust="qfq")
    assert bars
    for bar in bars:
        assert bar.high >= max(bar.open, bar.close)
        assert bar.low <= min(bar.open, bar.close)
        assert bar.volume > 0


def test_synthetic_returns_no_weekly_or_monthly_bars(synthetic: SyntheticProvider) -> None:
    assert synthetic.bars("AAPL", timeframe="1w", start_ts=0, adjust="qfq") == []


def test_synthetic_search_matches_alias_and_name(synthetic: SyntheticProvider) -> None:
    assert any(hit.symbol == "600519.SH" for hit in synthetic.search("茅台", 5))
    assert any(hit.symbol == "AAPL" for hit in synthetic.search("aapl", 5))
    assert synthetic.search("zzzzz", 5) == []


def test_synthetic_quote_is_plausible(synthetic: SyntheticProvider) -> None:
    quote = synthetic.quote("600519.SH")
    assert quote.last is not None and quote.last > 0
    assert quote.prev_close is not None and quote.prev_close > 0
    assert quote.source == "synthetic"


def test_synthetic_fundamentals_have_periods(synthetic: SyntheticProvider) -> None:
    periods = synthetic.fundamentals("600519.SH", 8)
    assert len(periods) == 8
    assert all(period.period and "Q" in period.period for period in periods)
    assert all(period.revenue is not None for period in periods)


def test_known_symbols_are_covered_by_anchored_prices(synthetic: SyntheticProvider) -> None:
    """Demo symbols must quote at a believable price, not a random draw."""
    for symbol, _name, _market, _currency, _exchange in KNOWN_SYMBOLS:
        quote = synthetic.quote(symbol)
        assert quote.last is not None and quote.last > 0


def test_registry_chain_reports_its_source() -> None:
    registry = ProviderRegistry(timeout=5.0)
    import asyncio

    result = asyncio.run(
        registry.bars("600519.SH", timeframe="1d", start_ts=int(time.time()) - 30 * 86400, adjust="qfq")
    )
    assert result.source
    assert result.value.bars


def test_registry_falls_through_to_synthetic_when_others_fail(monkeypatch) -> None:
    """A dead akshare must not take the product with it."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "data_providers", "akshare,yfinance,synthetic", raising=False)
    registry = ProviderRegistry(timeout=1.0)
    assert len(registry._instances) == 3, "the chain must include the optional providers"
    for name, provider in list(registry._instances.items()):
        if name != "synthetic":
            monkeypatch.setattr(provider, "available", lambda: False, raising=False)

    import asyncio

    result = asyncio.run(registry.quote("600519.SH"))
    assert result.source == "synthetic"
    assert result.warnings, "skipped providers must be reported, not hidden"


def test_registry_raises_when_every_provider_is_disabled(monkeypatch) -> None:
    registry = ProviderRegistry(timeout=1.0)
    registry.allow_synthetic = False
    for _name, provider in list(registry._instances.items()):
        monkeypatch.setattr(provider, "available", lambda: False, raising=False)

    import asyncio

    with pytest.raises(ProviderError):
        asyncio.run(registry.quote("600519.SH"))


def test_registry_available_providers_shape() -> None:
    registry = ProviderRegistry(timeout=1.0)
    entries = registry.available_providers()
    assert all({"id", "available"} <= set(entry) for entry in entries)
    assert any(entry["id"] == "synthetic" for entry in entries)


def test_bar_cache_round_trip(session, instrument, synthetic: SyntheticProvider) -> None:
    bars = synthetic.bars("600519.SH", timeframe="1d", start_ts=int(time.time()) - 20 * 86400, adjust="qfq")
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=bars, source="synthetic")
    session.commit()

    # Read back from the first bar's own timestamp: the generator opens at 07:00
    # on the start date, so a mid-day `start_ts` legitimately excludes it.
    read, source = cache.read_bars(session, instrument.id, timeframe="1d", adjust="qfq", start_ts=bars[0].ts)
    assert len(read) == len(bars)
    assert source == "synthetic"


def test_bar_cache_upserts_instead_of_duplicating(session, instrument, synthetic: SyntheticProvider) -> None:
    start = int(time.time()) - 20 * 86400
    bars = synthetic.bars("600519.SH", timeframe="1d", start_ts=start, adjust="qfq")
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=bars, source="synthetic")
    session.commit()
    cache.write_bars(session, instrument.id, timeframe="1d", adjust="qfq", bars=bars, source="synthetic")
    session.commit()

    read, _ = cache.read_bars(session, instrument.id, timeframe="1d", adjust="qfq", start_ts=bars[0].ts)
    assert len(read) == len(bars), "a second write must update rows, not insert duplicates"


def test_quote_cache_freshness() -> None:
    from app.services.market_data.base import QuoteRow

    fresh = QuoteRow(updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    stale = QuoteRow(updated_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"))
    assert cache.quote_is_fresh(fresh, ttl=60) is True
    assert cache.quote_is_fresh(stale, ttl=60) is False


def test_historical_bars_never_go_stale() -> None:
    from app.services.market_data.base import BarRow

    ancient = datetime.now(UTC) - timedelta(days=400)
    bars = [BarRow(ts=int(ancient.timestamp()), open=1, high=1, low=1, close=1)]
    assert cache.is_bars_cache_fresh(bars, "CN") is True


def test_empty_bar_cache_is_never_fresh() -> None:
    assert cache.is_bars_cache_fresh([], "CN") is False


def test_market_open_heuristic() -> None:
    # Saturday must always be closed.
    saturday = datetime(2026, 2, 7, 4, 0, tzinfo=UTC)
    assert cache.is_market_open("CN", saturday) is False
    wednesday = datetime(2026, 2, 11, 4, 0, tzinfo=UTC)  # 12:00 in Shanghai
    assert cache.is_market_open("CN", wednesday) is True


def test_provider_error_classification() -> None:
    network = ProviderError(ProviderErrorKind.network, "boom")
    notfound = ProviderError(ProviderErrorKind.notfound, "nope")
    assert network.retryable is True
    assert notfound.retryable is False
