# 取证脚本（只读）

本目录是本轮（Iteration 9 / §26）设计节点用到的**只读**取证脚本。全部在生产机
`47.82.166.177` 上用 API 自带的 venv 跑过，命令与实测输出见下方与 `spec.md` §26.0。

生产机的 API venv：`/opt/aragon-trading/api/.venv/bin/python`（akshare 1.20.65 /
yfinance 1.8.0 / Python 3.12.3）。

## 1. `market_probe.py` — akshare / yfinance 能力面

```text
scp market_probe.py root@47.82.166.177:/tmp/
ssh root@47.82.166.177 "cd /tmp && /opt/aragon-trading/api/.venv/bin/python market_probe.py 2>/dev/null"
```

给出：`stock_zh_a_hist_em` 的列名与行数、`stock_individual_info_em` 是否可用、
`stock_financial_abstract` 的指标行清单、yfinance `.info` 的估值字段、
yfinance 季度利润表 / 现金流量表的行名。

## 2. `cross_source_probe.py` — 两个数据源的同一根 K 线

对 300750 / 001232 / 688018 同时取 akshare 与 yfinance 的日线，逐日比对收盘价，
并统计 OHLC 自洽性违规（`high < max(open,close)` 等）。

> 注意：脚本里的 `yf_series()` 用 `auto_adjust=True`，与项目里
> `yfinance_provider.bars(adjust="qfq")` 等价。

## 3. `ak_endpoints_probe.py` — 每个端点的失败域

一次性试 `stock_zh_a_hist_em` / `stock_zh_a_daily` / `stock_zh_a_hist_tx` /
`stock_financial_abstract` / `stock_financial_analysis_indicator` /
`stock_board_industry_name_em` / `stock_board_industry_cons_em` /
`stock_individual_info_em` / `stock_a_indicator_lg`，打印每个调用的成功与否、
耗时与错误原文。用来证明「push2 被限流」与「改名漂移」是两类不同故障。

## 4. `db_probe3.py` — 生产库与工作区的事实

只读打开 `/opt/aragon-trading/api/data/app.db`（`mode=ro`），打印：最近一次 run 的
warn/error 事件、`instruments`（含 `sector` 为空的行）、每个标的缓存的 bar 序列
（来源 / 根数 / 首末 ts）、**OHLC 不自洽的缓存行**、`quotes` 与最后一根 bar 的偏离。

## 复跑纪律

- 三个网络脚本都会打真实数据源；akshare 的 push2 端点在生产 IP 上已被限流，
  **连续跑会延长限流**（错误原文自己写了 5 分钟 / 40 分钟两档）。取证时各跑一次即可。
- `db_probe3.py` 只读，不会写库。
- 这些脚本不是测试、不进 `api/tests`；实施节点若需要回归夹具，请把断言搬进
  `api/tests/test_data_source_integrity.py` 等文件，不要依赖本目录。
