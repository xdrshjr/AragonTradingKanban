"""图表指标独立黄金值；不以被测算法生成期望。"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.services import indicators
from app.services.market_data.base import BarRow

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "contracts/chart-indicators.fixture.json")
    .read_text(encoding="utf-8")
)
FIELDS = {
    "ma5", "ma10", "ma20", "ma60", "ema12", "ema26",
    "bollUpper", "bollMiddle", "bollLower", "macd", "macdSignal",
    "macdHistogram", "rsi14", "kdjK", "kdjD", "kdjJ", "atr14",
    "volumeMa5", "volumeMa10",
}


def compute(rows):
    """Feature absence is an assertion failure before importing the new module."""
    module_name = "app.services.chart_indicators"
    assert importlib.util.find_spec(module_name) is not None
    module = importlib.import_module(module_name)
    return module.compute_chart_indicator_bundle(rows)


def to_rows(items):
    return [
        BarRow(
            ts=row["ts"], open=row["o"], high=row["h"],
            low=row["l"], close=row["c"], volume=row["v"],
            amount=row["amount"],
        )
        for row in items
    ]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_chart_bundle_matches_independently_derived_values(case) -> None:
    rows = to_rows(case["rows"])
    bundle = compute(rows)
    assert set(bundle) == FIELDS
    assert all(len(values) == len(rows) for values in bundle.values())
    for expected in case["expected"]:
        assert bundle[expected["field"]][expected["index"]] == pytest.approx(
            expected["value"], abs=1e-9
        )


def test_empty_and_short_history_remain_aligned() -> None:
    assert all(values == [] for values in compute([]).values())
    rows = to_rows(FIXTURE["cases"][0]["rows"][:3])
    assert all(values == [None] * 3 for values in compute(rows).values())


def test_null_boundaries_are_exact() -> None:
    bundle = compute(to_rows(FIXTURE["cases"][1]["rows"]))
    first = {
        "ma5": 4, "ma10": 9, "ma20": 19, "ma60": None,
        "ema12": 11, "ema26": 25, "bollUpper": 19,
        "bollMiddle": 19, "bollLower": 19, "macd": 25,
        "macdSignal": 33, "macdHistogram": 33, "rsi14": 14,
        "kdjK": 8, "kdjD": 8, "kdjJ": 8, "atr14": 13,
        "volumeMa5": 4, "volumeMa10": 9,
    }
    for name, index in first.items():
        actual = next((i for i, v in enumerate(bundle[name]) if v is not None), None)
        assert actual == index, name


def test_chart_rsi_matches_legacy_for_nonflat_history() -> None:
    rows = to_rows(FIXTURE["cases"][1]["rows"])
    assert compute(rows)["rsi14"] == indicators.rsi([r.close for r in rows])


def test_macd_histogram_uses_double_gap_and_kdj_j_can_exceed_100() -> None:
    rows = [BarRow(i, 10, 11, 0, 10, 2) for i in range(60)]
    rows[-1] = BarRow(59, 10, 100, 0, 100, 2)
    bundle = compute(rows)
    for dif, dea, histogram in zip(
        bundle["macd"], bundle["macdSignal"], bundle["macdHistogram"],
        strict=True,
    ):
        if histogram is not None:
            assert histogram == pytest.approx(2 * (dif - dea))
    assert max(v for v in bundle["kdjJ"] if v is not None) > 100
