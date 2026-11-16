"""Technical indicators — pure functions, no IO, no numpy (spec §9).

Every function returns a list the same length as the input, left-padded with
``None`` so the front end can plot by array index without re-aligning.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Series = Sequence[float]


def _pad(values: list[float | None], length: int) -> list[float | None]:
    if len(values) >= length:
        return values[-length:]
    return [None] * (length - len(values)) + values


def sma(values: Series, period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    window = 0.0
    for index, value in enumerate(values):
        window += value
        if index >= period:
            window -= values[index - period]
        if index >= period - 1:
            out[index] = window / period
    return out


def ema(values: Series, period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period:
        return out
    multiplier = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    previous = seed
    for index in range(period, n):
        previous = (values[index] - previous) * multiplier + previous
        out[index] = previous
    return out


def bollinger(
    values: Series, period: int = 20, mult: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(values)
    middle = sma(values, period)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for index in range(period - 1, n):
        window = values[index - period + 1 : index + 1]
        mean = middle[index]
        if mean is None:
            continue
        variance = sum((value - mean) ** 2 for value in window) / period
        deviation = math.sqrt(variance)
        upper[index] = mean + mult * deviation
        lower[index] = mean - mult * deviation
    return upper, middle, lower


def macd(
    values: Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(values)
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    dif: list[float | None] = [None] * n
    for index in range(n):
        fast_value, slow_value = fast_ema[index], slow_ema[index]
        if fast_value is not None and slow_value is not None:
            dif[index] = fast_value - slow_value
    # The signal line is an EMA over the compacted DIF series, then re-aligned.
    dense = [(index, value) for index, value in enumerate(dif) if value is not None]
    dea: list[float | None] = [None] * n
    if len(dense) >= signal:
        compact = [value for _, value in dense]
        signal_compact = ema(compact, signal)
        for (original_index, _), value in zip(dense, signal_compact, strict=True):
            dea[original_index] = value
    histogram: list[float | None] = [None] * n
    for index in range(n):
        if dif[index] is not None and dea[index] is not None:
            histogram[index] = (dif[index] - dea[index]) * 2  # 中国市场习惯 ×2
    return dif, dea, histogram


def rsi(values: Series, period: int = 14) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for index in range(period + 1, n):
        change = values[index] - values[index - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[index] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def atr(highs: Series, lows: Series, closes: Series, period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 2:
        return out
    true_ranges: list[float] = [highs[0] - lows[0]]
    for index in range(1, n):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    if n < period:
        return out
    previous = sum(true_ranges[:period]) / period
    out[period - 1] = previous
    for index in range(period, n):
        previous = (previous * (period - 1) + true_ranges[index]) / period
        out[index] = previous
    return out


def daily_returns(closes: Series) -> list[float]:
    return [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1]]


def annualized_volatility(closes: Series, periods: int = 252) -> float | None:
    if len(closes) < 3:
        return None
    returns = daily_returns(closes)[1:]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods) * 100.0


def max_drawdown(closes: Series) -> float | None:
    """Peak-to-trough drawdown as a negative percentage."""
    if not closes:
        return None
    peak = closes[0]
    worst = 0.0
    for value in closes:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def range_return(closes: Series, lookback: int) -> float | None:
    if len(closes) <= lookback or closes[-lookback - 1] == 0:
        return None
    return (closes[-1] / closes[-lookback - 1] - 1.0) * 100.0


def percentile_rank(values: Series, current: float) -> float | None:
    """Where `current` sits inside `values`, as 0-100 (used for PE percentiles)."""
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    below = sum(1 for value in clean if value <= current)
    return below / len(clean) * 100.0


def compute_indicator_bundle(closes: Series) -> dict[str, list[float | None]]:
    """The exact bundle `GET /bars` returns (spec §7.1 `indicators`)."""
    upper, _, lower = bollinger(closes)
    dif, dea, _ = macd(closes)
    return {
        "ma5": sma(closes, 5),
        "ma10": sma(closes, 10),
        "ma20": sma(closes, 20),
        "ma60": sma(closes, 60),
        "bollUpper": upper,
        "bollLower": lower,
        "macd": dif,
        "macdSignal": dea,
        "rsi14": rsi(closes, 14),
    }


def snapshot_technicals(highs: Series, lows: Series, closes: Series) -> dict[str, float | None]:
    """Last-value technical block for `snapshot.json` (spec §8.4)."""
    dif, dea, _ = macd(closes)
    upper, _, lower = bollinger(closes)

    def last(series: list[float | None]) -> float | None:
        for value in reversed(series):
            if value is not None:
                return round(value, 4)
        return None

    return {
        "ma5": last(sma(closes, 5)),
        "ma20": last(sma(closes, 20)),
        "ma60": last(sma(closes, 60)),
        "rsi14": last(rsi(closes, 14)),
        "macd": last(dif),
        "macdSignal": last(dea),
        "bollUpper": last(upper),
        "bollLower": last(lower),
        "atr14": last(atr(highs, lows, closes, 14)),
    }
