"""Read-only probe of the production data source capabilities.

Not part of the app; run on the server with the API venv to see which
provider calls still work in the installed library versions.
"""

import json

import akshare as ak
import yfinance as yf

out = {}

try:
    frame = ak.stock_zh_a_hist_em(
        symbol="300750",
        period="daily",
        start_date="20250801",
        end_date="20261120",
        adjust="qfq",
    )
    out["hist_em"] = {
        "columns": [str(c) for c in frame.columns],
        "rows": len(frame),
        "tail": frame.tail(2).to_dict("records"),
    }
except Exception as exc:  # noqa: BLE001
    out["hist_em_err"] = repr(exc)[:400]

try:
    frame = ak.stock_individual_info_em(symbol="300750")
    out["individual_info"] = frame.to_dict("records")
except Exception as exc:  # noqa: BLE001
    out["individual_info_err"] = repr(exc)[:400]

try:
    frame = ak.stock_financial_abstract(symbol="SH600519")
    out["abstract_rows"] = frame["指标"].astype(str).tolist()[:60]
    out["abstract_cols"] = [str(c) for c in frame.columns][:8]
except Exception as exc:  # noqa: BLE001
    out["abstract_err"] = repr(exc)[:400]

try:
    ticker = yf.Ticker("300750.SZ")
    info = ticker.info
    keys = [
        "trailingPE",
        "forwardPE",
        "priceToBook",
        "marketCap",
        "dividendYield",
        "trailingEps",
        "sector",
        "industry",
        "sharesOutstanding",
        "currentPrice",
    ]
    out["yf_info"] = {key: info.get(key) for key in keys}
except Exception as exc:  # noqa: BLE001
    out["yf_info_err"] = repr(exc)[:400]

try:
    frame = yf.Ticker("300750.SZ").quarterly_income_stmt
    out["yf_income_rows"] = [str(index) for index in frame.index][:40]
except Exception as exc:  # noqa: BLE001
    out["yf_income_err"] = repr(exc)[:400]

try:
    frame = yf.Ticker("300750.SZ").quarterly_cashflow
    out["yf_cash_rows"] = [str(index) for index in frame.index][:40]
except Exception as exc:  # noqa: BLE001
    out["yf_cash_err"] = repr(exc)[:400]

print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
