"""Read-only probe: which akshare endpoints still work from this host, and why.

Each endpoint is tried once, in order, with its error classified. The point is to
learn the failure domain of every call the fix would rely on.
"""

import json
import time

import akshare as ak

out = {}


def attempt(name, func):
    started = time.time()
    try:
        value = func()
        out[name] = {
            "ok": True,
            "seconds": round(time.time() - started, 2),
            "shape": str(type(value).__name__),
            "rows": len(value) if hasattr(value, "__len__") else None,
            "head": value.head(2).to_dict("records") if hasattr(value, "head") else str(value)[:200],
        }
    except Exception as exc:  # noqa: BLE001
        out[name] = {
            "ok": False,
            "seconds": round(time.time() - started, 2),
            "error": repr(exc)[:300],
        }


attempt(
    "em_hist_300750",
    lambda: ak.stock_zh_a_hist_em(
        symbol="300750", period="daily", start_date="20260101", end_date="20261120", adjust="qfq"
    ),
)
attempt(
    "sina_daily_300750",
    lambda: ak.stock_zh_a_daily(symbol="sz300750", start_date="20260101", end_date="20261120", adjust="qfq"),
)
attempt(
    "tx_hist_300750",
    lambda: ak.stock_zh_a_hist_tx(symbol="sz300750", start_date="20260101", end_date="20261120", adjust="qfq"),
)
attempt("abstract_SZ001232", lambda: ak.stock_financial_abstract(symbol="SZ001232"))
attempt("financial_indicator_001232", lambda: ak.stock_financial_analysis_indicator(symbol="001232", start_year="2024"))
attempt("industry_boards", lambda: ak.stock_board_industry_name_em())
attempt("concept_cons", lambda: ak.stock_board_industry_cons_em(symbol="电力设备"))
attempt("individual_info_600519", lambda: ak.stock_individual_info_em(symbol="600519"))
attempt("a_indicator_lg", lambda: ak.stock_a_indicator_lg(symbol="600519"))

print(json.dumps(out, ensure_ascii=False, default=str, indent=1))
