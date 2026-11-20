"""Indicator maths against known series (spec §9). Tolerance 1e-6."""

from __future__ import annotations

import pytest

from app.services import indicators

CLOSES = [10.0, 11.0, 12.0, 11.5, 13.0, 14.0, 15.0, 14.2, 16.0, 17.0, 16.5, 18.0, 19.0, 20.0, 19.5, 21.0]


def test_sma_is_padded_with_none() -> None:
    series = indicators.sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert series[:2] == [None, None]
    assert series[2] == pytest.approx(2.0, abs=1e-9)
    assert series[4] == pytest.approx(4.0, abs=1e-9)


def test_sma_returns_all_none_when_too_short() -> None:
    assert indicators.sma([1.0, 2.0], 5) == [None, None]


def test_sma_matches_manual_average() -> None:
    series = indicators.sma(CLOSES, 5)
    expected = sum(CLOSES[11:16]) / 5
    assert series[15] == pytest.approx(expected, abs=1e-9)


def test_ema_starts_at_the_period_seed() -> None:
    series = indicators.ema([1.0] * 6, 3)
    assert series[:2] == [None, None]
    for value in series[2:]:
        assert value == pytest.approx(1.0, abs=1e-9)


def test_ema_reacts_faster_than_sma() -> None:
    # The claim is about the transition, not the asymptote: once the series has
    # been flat long enough the SMA sits exactly on the new level while the EMA
    # is still catching up. Right after the jump the EMA must lead.
    series = [10.0] * 10 + [20.0] * 10
    ema_series = indicators.ema(series, 5)
    sma_series = indicators.sma(series, 5)
    for index in (10, 11, 12):
        assert ema_series[index] is not None and sma_series[index] is not None
        assert ema_series[index] > sma_series[index], f"EMA should lead at index {index}"


def test_bollinger_bands_bracket_the_middle_band() -> None:
    upper, middle, lower = indicators.bollinger(CLOSES, 5, 2.0)
    for index in range(4, len(CLOSES)):
        assert upper[index] is not None and lower[index] is not None and middle[index] is not None
        assert upper[index] >= middle[index] >= lower[index]


def test_bollinger_lower_band_is_symmetric_for_constant_series() -> None:
    upper, middle, lower = indicators.bollinger([5.0] * 25, 20, 2.0)
    assert upper[24] == pytest.approx(5.0, abs=1e-9)
    assert lower[24] == pytest.approx(5.0, abs=1e-9)
    assert middle[24] == pytest.approx(5.0, abs=1e-9)


def test_macd_histogram_is_twice_the_gap() -> None:
    dif, dea, histogram = indicators.macd(CLOSES, 3, 6, 3)
    for index in range(len(CLOSES)):
        if dif[index] is not None and dea[index] is not None:
            assert histogram[index] == pytest.approx((dif[index] - dea[index]) * 2, abs=1e-9)


def test_rsi_is_100_for_a_monotonic_rise() -> None:
    rising = [float(value) for value in range(1, 40)]
    series = indicators.rsi(rising, 14)
    assert series[-1] == pytest.approx(100.0, abs=1e-6)


def test_rsi_is_zero_for_a_monotonic_fall() -> None:
    falling = [float(value) for value in range(40, 1, -1)]
    series = indicators.rsi(falling, 14)
    assert series[-1] == pytest.approx(0.0, abs=1e-6)


def test_standard_flat_rsi_and_snapshot_are_frozen() -> None:
    closes = [100.0] * 40
    assert indicators.rsi(closes)[14:] == [100.0] * 26
    bundle = indicators.compute_indicator_bundle(closes)
    assert len(bundle) == 9
    assert bundle["rsi14"][-1] == 100.0
    snapshot = indicators.snapshot_technicals(closes, closes, closes)
    assert snapshot["rsi14"] == 100.0
    assert set(snapshot) == {
        "ma5", "ma20", "ma60", "rsi14", "macd", "macdSignal",
        "bollUpper", "bollLower", "atr14",
    }


def test_rsi_stays_within_bounds() -> None:
    series = indicators.rsi(CLOSES, 14)
    for value in series:
        if value is not None:
            assert 0.0 <= value <= 100.0


def test_atr_is_positive_and_padded() -> None:
    highs = [value + 0.5 for value in CLOSES]
    lows = [value - 0.5 for value in CLOSES]
    series = indicators.atr(highs, lows, CLOSES, 5)
    assert series[3] is None
    assert series[-1] is not None and series[-1] > 0


def test_max_drawdown_captures_peak_to_trough() -> None:
    assert indicators.max_drawdown([10.0, 12.0, 9.0, 13.0]) == pytest.approx(-25.0, abs=1e-9)
    assert indicators.max_drawdown([1.0, 2.0, 3.0]) == pytest.approx(0.0, abs=1e-9)
    assert indicators.max_drawdown([]) is None


def test_range_return_uses_the_lookback() -> None:
    closes = [100.0] * 300
    closes[-1] = 110.0
    assert indicators.range_return(closes, 252) == pytest.approx(10.0, abs=1e-9)
    assert indicators.range_return([1.0], 252) is None


def test_annualized_volatility_is_zero_for_a_flat_series() -> None:
    assert indicators.annualized_volatility([10.0] * 30) == pytest.approx(0.0, abs=1e-9)


def test_annualized_volatility_scales_with_dispersion() -> None:
    calm = [10.0 + (0.01 if index % 2 else -0.01) for index in range(60)]
    wild = [10.0 + (1.0 if index % 2 else -1.0) for index in range(60)]
    calm_vol = indicators.annualized_volatility(calm)
    wild_vol = indicators.annualized_volatility(wild)
    assert calm_vol is not None and wild_vol is not None
    assert wild_vol > calm_vol


def test_percentile_rank_bounds() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert indicators.percentile_rank(values, 0.5) == pytest.approx(0.0, abs=1e-9)
    assert indicators.percentile_rank(values, 5.0) == pytest.approx(100.0, abs=1e-9)
    assert indicators.percentile_rank(values, 3.0) == pytest.approx(60.0, abs=1e-9)


def test_indicator_bundle_lengths_align_with_input() -> None:
    bundle = indicators.compute_indicator_bundle(CLOSES)
    assert set(bundle) == {
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "bollUpper",
        "bollLower",
        "macd",
        "macdSignal",
        "rsi14",
    }
    for name, series in bundle.items():
        assert len(series) == len(CLOSES), f"{name} is misaligned"


def test_snapshot_technicals_uses_last_defined_values() -> None:
    highs = [value + 0.4 for value in CLOSES]
    lows = [value - 0.4 for value in CLOSES]
    technicals = indicators.snapshot_technicals(highs, lows, CLOSES)
    assert technicals["ma5"] is not None
    assert technicals["rsi14"] is not None
    assert technicals["ma60"] is None, "not enough history for MA60"
