"""Read-only probe: compare akshare vs yfinance series, and check OHLC integrity."""

import json
from datetime import UTC, datetime, timedelta

import akshare as ak
import yfinance as yf

START = (datetime.now(UTC) - timedelta(days=365 * 3)).strftime("%Y%m%d")
END = datetime.now(UTC).strftime("%Y%m%d")


def ak_series(code: str) -> dict[str, float]:
    frame = ak.stock_zh_a_hist_em(
        symbol=code, period="daily", start_date=START, end_date=END, adjust="qfq"
    )
    return {
        str(row["日期"])[:10]: float(row["收盘"]) for _, row in frame.iterrows()
    }, {
        str(row["日期"])[:10]: (float(row["开盘"]), float(row["最高"]), float(row["最低"]), float(row["收盘"]))
        for _, row in frame.iterrows()
    }


def yf_series(symbol: str) -> dict[str, tuple[float, float, float, float]]:
    frame = yf.Ticker(symbol).history(start=f"{START[:4]}-{START[4:6]}-{START[6:]}", interval="1d", auto_adjust=True)
    out = {}
    for index, item in frame.iterrows():
        day = index.strftime("%Y-%m-%d")
        out[day] = (float(item["Open"]), float(item["High"]), float(item["Low"]), float(item["Close"]))
    return out


def ohlc_violations(series: dict[str, tuple[float, float, float, float]]) -> list[tuple[str, tuple]]:
    bad = []
    for day, (open_, high, low, close) in sorted(series.items()):
        if high < max(open_, close) - 1e-6 or low > min(open_, close) + 1e-6 or high < low:
            bad.append((day, (round(open_, 2), round(high, 2), round(low, 2), round(close, 2))))
    return bad


for code, yahoo in (("300750", "300750.SZ"), ("001232", "001232.SZ"), ("688018", "688018.SS")):
    report = {"code": code}
    try:
        ak_closes, ak_rows = ak_series(code)
        report["akshare_bars"] = len(ak_closes)
        report["akshare_first"] = min(ak_closes) if ak_closes else None
        report["akshare_last"] = sorted(ak_closes.items())[-3:] if ak_closes else None
        report["akshare_violations"] = ohlc_violations(ak_rows)[:5]
    except Exception as exc:  # noqa: BLE001
        report["akshare_err"] = repr(exc)[:200]
    try:
        rows = yf_series(yahoo)
        report["yfinance_bars"] = len(rows)
        report["yfinance_first"] = min(rows) if rows else None
        report["yfinance_last"] = sorted(rows.items())[-3:] if rows else None
        report["yfinance_violations"] = ohlc_violations(rows)[:8]
    except Exception as exc:  # noqa: BLE001
        report["yfinance_err"] = repr(exc)[:200]
    if "akshare_bars" in report and "yfinance_bars" in report:
        common = sorted(set(ak_closes) & set(rows))
        if common:
            ratios = []
            for day in common[-120:]:
                ak_close = ak_closes[day]
                yf_close = rows[day][3]
                if ak_close:
                    ratios.append((day, round(yf_close / ak_close, 4)))
            report["ratio_first_last"] = (ratios[0], ratios[-1])
            report["ratio_unique"] = sorted({value for _, value in ratios})[:10]
            diffs = [
                (day, round(yf_close / ak_closes[day] - 1, 4))
                for day in common[-120:]
                for yf_close in [rows[day][3]]
                if ak_closes[day] and abs(yf_close / ak_closes[day] - 1) > 0.005
            ]
            report["pct_diff_over_0_5pct"] = len(diffs)
            report["pct_diff_sample"] = diffs[:6]
    print(json.dumps(report, ensure_ascii=False, default=str, indent=1))
    print("-" * 60)
