"""图表专用 aragon-chart-v1 指标，不改变报告及 standard 算法。"""

from __future__ import annotations

from .indicators import atr, bollinger, ema, macd, sma
from .market_data.base import BarRow


def chart_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI；无涨幅且无跌幅时取中性 50，短历史返回空值。"""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(v, 0.0) for v in changes[:period]) / period
    loss = sum(max(-v, 0.0) for v in changes[:period]) / period
    out[period] = _rsi_value(gain, loss)
    for index in range(period + 1, len(closes)):
        change = changes[index - 1]
        gain = (gain * (period - 1) + max(change, 0.0)) / period
        loss = (loss * (period - 1) + max(-change, 0.0)) / period
        out[index] = _rsi_value(gain, loss)
    return out


def _rsi_value(gain: float, loss: float) -> float:
    if loss == 0:
        return 50.0 if gain == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def kdj(rows: list[BarRow]) -> tuple[
    list[float | None], list[float | None], list[float | None]
]:
    """KDJ(9,3,3)，K/D 初值50，J 不截断。"""
    k_values: list[float | None] = [None] * len(rows)
    d_values: list[float | None] = [None] * len(rows)
    j_values: list[float | None] = [None] * len(rows)
    previous_k = previous_d = 50.0
    for index in range(8, len(rows)):
        window = rows[index - 8:index + 1]
        highest = max(row.high for row in window)
        lowest = min(row.low for row in window)
        rsv = (
            50.0 if highest == lowest
            else 100.0 * (rows[index].close - lowest) / (highest - lowest)
        )
        previous_k = 2 / 3 * previous_k + rsv / 3
        previous_d = 2 / 3 * previous_d + previous_k / 3
        k_values[index] = previous_k
        d_values[index] = previous_d
        j_values[index] = 3 * previous_k - 2 * previous_d
    return k_values, d_values, j_values


def compute_chart_indicator_bundle(
    rows: list[BarRow],
) -> dict[str, list[float | None]]:
    """返回与输入等长的19条图表指标，保留原始浮点精度。"""
    closes = [row.close for row in rows]
    volumes = [row.volume for row in rows]
    upper, middle, lower = bollinger(closes)
    dif, dea, histogram = macd(closes)
    k_values, d_values, j_values = kdj(rows)
    return {
        "ma5": sma(closes, 5),
        "ma10": sma(closes, 10),
        "ma20": sma(closes, 20),
        "ma60": sma(closes, 60),
        "ema12": ema(closes, 12),
        "ema26": ema(closes, 26),
        "bollUpper": upper, "bollMiddle": middle, "bollLower": lower,
        "macd": dif, "macdSignal": dea, "macdHistogram": histogram,
        "rsi14": chart_rsi(closes),
        "kdjK": k_values, "kdjD": d_values, "kdjJ": j_values,
        "atr14": atr(
            [row.high for row in rows], [row.low for row in rows], closes,
        ),
        "volumeMa5": sma(volumes, 5), "volumeMa10": sma(volumes, 10),
    }
