"""Deterministic offline generator (spec §6.2 / AC-15).

No network, no model: it reads the same `snapshot.json` the real agent would
read and turns it into a schema-valid document using fixed rules (moving-average
trend, valuation percentile, drawdown, YoY growth). This is what makes
`AGENT_MODE=mock` a complete demo and the whole E2E suite network-free.

Per §16 verdict 6, a mock report is identified *three* times: the top banner,
the first block of the first section, and `quality.source="mock"`.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..logging import get_logger
from .runner_base import AgentError, AgentEvent, AgentEventType, AgentRequest, AgentResult, AgentUsage

logger = get_logger("mock_runner")

MOCK_CALLOUT = {
    "type": "callout",
    "tone": "warn",
    "title": "示例模式：本报告由本地规则生成，未调用 AI",
    "text": (
        "当前未连接生成引擎（或无模型 Key），以下内容由快照数据 + 固定规则拼装，"
        "用于演示产品全流程。数字来自真实/合成行情快照，但结论不是 AI 撰写的投资观点。"
    ),
}


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def _yi(value: float | None) -> str:
    """Render a raw currency amount as 亿元 when it is big enough."""
    if value is None:
        return "n/a"
    if abs(value) >= 1e8:
        return f"{value / 1e8:,.1f} 亿"
    return f"{value:,.0f}"


def _source_refs(index: int) -> list[str]:
    return [f"S{index}"]


class MockRunner:
    name = "mock"
    source = "mock"

    def __init__(self, stage_delay: float = 0.05) -> None:
        self.stage_delay = stage_delay
        self._cancelled = False

    async def cancel(self) -> None:
        self._cancelled = True

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AgentResult:
        """Consume `stream`, forward every event, and return the terminal result."""
        result: AgentResult | None = None
        async for event in self.stream(request):
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:  # pragma: no cover - a callback must never kill a run
                    logger.exception("mock event callback failed")
            if event.type == AgentEventType.log and (candidate := event.payload.get("__result")):
                result = candidate  # type: ignore[assignment]
        return result or AgentResult(
            error_code="internal",
            error_message="mock runner produced no result",
            source=self.source,
        )

    async def _workflow_markers(self, workspace: Path) -> AsyncIterator[AgentEvent]:
        """report-workflow-graph §3.6.8：工作区有 `workflow.json` 时，按其 `items` 顺序发进度标记，
        让 demo / E2E / UI 在无网络下也能看到完整时间线。报告文档本身不变。"""
        manifest = workspace / "workflow.json"
        if not manifest.exists():
            return
        try:
            items = json.loads(manifest.read_text(encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            return
        for item in items:
            key = item.get("key")
            if not isinstance(key, str):
                continue
            yield AgentEvent(AgentEventType.text, {"delta": f"[[wf:{key}:start]]\n"})
            await asyncio.sleep(0)
            yield AgentEvent(AgentEventType.text, {"delta": f"[[wf:{key}:done]]\n"})
            await asyncio.sleep(0)

    async def stream(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Async generator — yields events, never calls back (see `runner_base`).

        `on_event` is accepted and ignored so this class satisfies
        `runner_base.AgentRunner` exactly; `run()` is the only forwarding path.
        """
        snapshot_path = request.workspace / "snapshot.json"
        if not snapshot_path.exists():
            raise AgentError("snapshot_missing", f"快照文件不存在：{snapshot_path}")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        stages = [
            (0.25, {"turns": 1}, "读取资料快照"),
            (0.45, {"turns": 2}, "计算技术与估值指标"),
            (0.65, {"turns": 4}, "撰写核心逻辑与基本面"),
            (0.85, {"turns": 6}, "校验结构与接地"),
        ]
        for progress, meta, label in stages:
            if self._cancelled:
                yield AgentEvent(
                    AgentEventType.log,
                    {
                        "__result": AgentResult(
                            exit_code=None,
                            error_code="cancelled",
                            error_message="用户取消了本次生成",
                            source=self.source,
                        )
                    },
                )
                return
            await asyncio.sleep(self.stage_delay)
            yield AgentEvent(
                AgentEventType.tool,
                {
                    "name": "read_file",
                    "input": '{"path":"snapshot.json"}',
                    "isError": False,
                    "durationMs": 3,
                    "outputPreview": label,
                    "phase": "result",
                },
            )
            yield AgentEvent(AgentEventType.progress, {**meta, "toolCalls": len(stages), "pct": int(progress * 100)})
            text = self._running_text(snapshot, progress)
            if text:
                yield AgentEvent(AgentEventType.text, {"delta": text})

        async for event in self._workflow_markers(request.workspace):
            yield event

        await asyncio.sleep(self.stage_delay)
        doc = build_mock_document(snapshot)
        usage = AgentUsage(input_tokens=0, output_tokens=0, total_tokens=0, turns=6, tool_calls=len(stages))
        yield AgentEvent(
            AgentEventType.log,
            {
                "__result": AgentResult(
                    doc=doc,
                    text="（示例模式）已根据快照生成结构完整的报告。",
                    usage=usage,
                    cost_usd=None,
                    cost_known=False,
                    exit_code=0,
                    error_code=None,
                    error_message=None,
                    provider="mock",
                    model="local-rules-v1",
                    source=self.source,
                ),
                "level": "info",
                "message": "示例模式生成完成",
            },
        )

    @staticmethod
    def _running_text(snapshot: dict[str, Any], progress: float) -> str:
        instrument = snapshot.get("instrument") or {}
        name = instrument.get("name") or ""
        if progress <= 0.25:
            return f"正在阅读 {name} 的资料快照…" if name else ""
        if progress <= 0.45:
            return "对比均线趋势与估值分位…"
        if progress <= 0.65:
            return "形成核心逻辑与基本面判断…"
        return "检查数字是否都能在快照中找到出处…"


def _period_end_date(period: Any) -> str:
    """`2025Q4` -> `2025-12-31`, so timeline dates are real ISO dates."""
    text = str(period or "").strip()
    match = re.match(r"^(\d{4})\D*Q?([1-4])$", text, re.IGNORECASE)
    if not match:
        return "2026-01-01"
    year, quarter = int(match.group(1)), int(match.group(2))
    month = quarter * 3
    day = 31 if month in (3, 12) else 30
    return f"{year:04d}-{month:02d}-{day:02d}"


def _price_chart_block(price_series: dict[str, Any], currency: str) -> dict[str, Any]:
    """A real (not decorative) line chart from the snapshot's close series."""
    dates: list[str] = [str(value) for value in price_series.get("dates") or []]
    closes: list[float | None] = [float(value) for value in price_series.get("close") or []]
    if len(closes) < 2:
        return {
            "type": "chart",
            "chart": {
                "kind": "line",
                "title": "价格走势（数据不足）",
                "xAxis": {"type": "category", "labels": ["n/a"]},
                "series": [{"name": "收盘价", "data": [0]}],
                "note": "快照中没有足够的收盘价序列",
            },
        }
    step = max(1, len(dates) // 8)
    return {
        "type": "chart",
        "chart": {
            "kind": "line",
            "title": f"近 {len(closes)} 个交易日收盘价",
            "xAxis": {"type": "category", "labels": [label[:10] for label in dates]},
            "series": [{"name": f"收盘价（{currency}）", "data": closes}],
            "unit": currency,
            "note": (
                f"共 {len(closes)} 个交易日，横轴每 {step} 个点显示一个日期标签；"
                "图表直接取自快照，未经模型改写"
            ),
        },
    }


def build_mock_document(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Turn a snapshot into a schema-valid `aragon.research.report/v1` document."""
    instrument = snapshot.get("instrument") or {}
    quote = snapshot.get("quote") or {}
    performance = snapshot.get("performance") or {}
    valuation = snapshot.get("valuation") or {}
    technicals = snapshot.get("technicals") or {}
    financials: list[dict[str, Any]] = snapshot.get("financials") or []
    peers: list[dict[str, Any]] = snapshot.get("peers") or []
    sources: list[dict[str, Any]] = snapshot.get("sources") or []
    warnings: list[str] = (snapshot.get("dataQuality") or {}).get("warnings") or []
    price_series: dict[str, Any] = snapshot.get("priceSeries") or {}

    symbol = instrument.get("symbol", "")
    name = instrument.get("name", symbol)
    currency = instrument.get("currency", "CNY")
    market = instrument.get("market", "CN")
    as_of = snapshot.get("asOf") or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    last = quote.get("last")
    change_pct = quote.get("changePct")
    ret_1y = performance.get("ret1y")
    drawdown = performance.get("maxDrawdown1y")
    pe = valuation.get("peTtm")
    pe_pct = valuation.get("pePercentile3y")
    pb = valuation.get("pb")
    ma20 = technicals.get("ma20")
    ma60 = technicals.get("ma60")
    rsi = technicals.get("rsi14")

    latest_period = financials[0] if financials else {}
    rev_yoy = latest_period.get("revenueYoY")
    profit_yoy = latest_period.get("netProfitYoY")
    gross_margin = latest_period.get("grossMargin")

    # Trend rule: current price against MA20/MA60 decides the stance.
    above = []
    if last is not None and ma20 is not None:
        above.append(last >= ma20)
    if last is not None and ma60 is not None:
        above.append(last >= ma60)
    bullish = all(above) if above else True
    stance = "buy" if bullish else "watch"
    conviction = 3 if bullish else 2

    target = None
    upside = None
    if last:
        target = round(last * (1.18 if bullish else 1.04), 2)
        upside = round((target / last - 1) * 100, 1)

    # Valuation rule: percentile decides which way the argument leans.
    if pe_pct is not None and pe_pct < 35 and bullish:
        valuation_note = f"PE(TTM) {_num(pe)}×，位于近三年 {pe_pct:.0f}% 分位，估值处于偏低区间。"
    elif pe_pct is not None and pe_pct > 70:
        valuation_note = f"PE(TTM) {_num(pe)}×，位于近三年 {pe_pct:.0f}% 分位，估值已偏高，需业绩兑现支撑。"
    else:
        valuation_note = f"PE(TTM) {_num(pe)}×，位于近三年 {_num(pe_pct, 0)}% 分位，估值处于中性区间。"

    trend_note = (
        f"现价 {_num(last)} 位于 MA20（{_num(ma20)}）与 MA60（{_num(ma60)}）上方，中期趋势偏强。"
        if bullish and ma20 and ma60
        else f"现价 {_num(last)} 相对 MA20（{_num(ma20)}）/ MA60（{_num(ma60)}）偏弱，趋势尚未确认。"
    )

    key_metrics = [
        {"label": "最新价", "value": _num(last), "tone": "neutral", "sourceRefs": _source_refs(1)},
        {
            "label": "PE(TTM)",
            "value": f"{_num(pe)}×",
            "tone": "pos" if (pe_pct or 50) < 50 else "neg",
            "sourceRefs": _source_refs(1),
        },
        {"label": "PB", "value": f"{_num(pb)}×", "tone": "neutral", "sourceRefs": _source_refs(1)},
        {
            "label": "近 1 年收益",
            "value": _pct(ret_1y),
            "tone": "pos" if (ret_1y or 0) >= 0 else "neg",
            "sourceRefs": _source_refs(1),
        },
        {
            "label": "最大回撤",
            "value": _pct(drawdown),
            "tone": "neg" if (drawdown or 0) < -15 else "neutral",
            "sourceRefs": _source_refs(1),
        },
        {
            "label": "营收同比",
            "value": _pct(rev_yoy),
            "tone": "pos" if (rev_yoy or 0) >= 0 else "neg",
            "sourceRefs": _source_refs(3),
        },
    ]

    bullets = [
        {"text": trend_note, "sourceRefs": _source_refs(1)},
        {"text": valuation_note, "sourceRefs": _source_refs(1)},
    ]
    if rev_yoy is not None:
        bullets.append(
            {
                "text": f"最近一期（{latest_period.get('period', 'n/a')}）营收同比 {_pct(rev_yoy)}，归母净利同比 {_pct(profit_yoy)}。",
                "sourceRefs": _source_refs(3),
            }
        )
    if rsi is not None:
        bullets.append(
            {
                "text": f"RSI(14) 为 {_num(rsi, 1)}，短线{'偏热' if rsi > 70 else ('偏冷' if rsi < 30 else '处于中性区间')}。",
                "sourceRefs": _source_refs(1),
            }
        )

    sections: list[dict[str, Any]] = []

    thesis_blocks: list[dict[str, Any]] = [MOCK_CALLOUT]
    if warnings:
        thesis_blocks.append(
            {
                "type": "callout",
                "tone": "info",
                "title": "数据质量提示",
                "text": "；".join(warnings[:3]),
            }
        )
    thesis_blocks.append(
        {
            "type": "paragraph",
            "text": (
                f"{name}（{symbol}）当前报价 {_num(last)}（{_pct(change_pct)}）。{trend_note}"
                f"{valuation_note}综合趋势与估值，本报告给出的立场是"
                f"{'偏多' if stance == 'buy' else '观察'}"
                f"，{('目标价 ' + _num(target)) if target else '不设目标价'}。"
            ),
        }
    )
    thesis_blocks.append({"type": "bullets", "items": bullets})
    sections.append({"id": "thesis", "title": "核心逻辑", "blocks": thesis_blocks})

    fundamentals_blocks: list[dict[str, Any]] = [
        {
            "type": "metrics",
            "items": [
                {
                    "label": "营业收入",
                    "value": _yi(latest_period.get("revenue")),
                    "deltaPct": rev_yoy,
                    "tone": "pos" if (rev_yoy or 0) >= 0 else "neg",
                    "sourceRefs": _source_refs(3),
                },
                {
                    "label": "归母净利",
                    "value": _yi(latest_period.get("netProfit")),
                    "deltaPct": profit_yoy,
                    "tone": "pos" if (profit_yoy or 0) >= 0 else "neg",
                    "sourceRefs": _source_refs(3),
                },
                {
                    "label": "经营现金流",
                    "value": _yi(latest_period.get("ocf")),
                    "tone": "neutral",
                    "sourceRefs": _source_refs(3),
                },
                {
                    "label": "毛利率",
                    "value": f"{_num(gross_margin, 1)}%",
                    "tone": "pos" if (gross_margin or 0) > 30 else "neutral",
                    "sourceRefs": _source_refs(3),
                },
            ],
        }
    ]
    if financials:
        fundamentals_blocks.append(
            {
                "type": "table",
                "caption": "近几期营收与利润（原始单位）",
                "columns": [
                    {"key": "period", "label": "期间", "align": "left"},
                    {"key": "revenue", "label": "营业收入", "align": "right"},
                    {"key": "yoy", "label": "营收同比", "align": "right"},
                    {"key": "netProfit", "label": "归母净利", "align": "right"},
                    {"key": "profitYoy", "label": "净利同比", "align": "right"},
                ],
                "rows": [
                    {
                        "period": item.get("period", "n/a"),
                        "revenue": _yi(item.get("revenue")),
                        "yoy": _pct(item.get("revenueYoY")),
                        "netProfit": _yi(item.get("netProfit")),
                        "profitYoy": _pct(item.get("netProfitYoY")),
                    }
                    for item in financials[:6]
                ],
            }
        )
    sections.append({"id": "fundamentals", "title": "基本面", "blocks": fundamentals_blocks})

    valuation_blocks: list[dict[str, Any]] = [
        {"type": "heading", "level": 3, "text": "估值位置"},
        {"type": "paragraph", "text": valuation_note},
    ]
    if peers:
        valuation_blocks.append(
            {
                "type": "comparables",
                "peers": [
                    {
                        "name": peer.get("name") or peer.get("symbol", ""),
                        "symbol": peer.get("symbol", ""),
                        "pe": peer.get("peTtm"),
                        "pb": peer.get("pb"),
                        "growthPct": peer.get("revenueYoY"),
                    }
                    for peer in peers[:6]
                ],
            }
        )
    if target:
        base_price = target
        valuation_blocks.append(
            {
                "type": "scenario",
                "scenarios": [
                    {
                        "name": "bear",
                        "targetPrice": round(base_price * 0.78, 2),
                        "probability": 0.25,
                        "assumptions": "趋势转弱且估值分位回落，给予折价。",
                    },
                    {
                        "name": "base",
                        "targetPrice": round(base_price, 2),
                        "probability": 0.5,
                        "assumptions": "趋势与估值维持当前状态。",
                    },
                    {
                        "name": "bull",
                        "targetPrice": round(base_price * 1.18, 2),
                        "probability": 0.25,
                        "assumptions": "盈利超预期并带动估值上修。",
                    },
                ],
            }
        )
    valuation_blocks.append(_price_chart_block(price_series, currency))
    sections.append({"id": "valuation", "title": "估值", "blocks": valuation_blocks})

    severity = "high" if (drawdown or 0) < -25 else ("medium" if (drawdown or 0) < -15 else "low")
    risks = [
        {
            "title": "趋势反转",
            "severity": severity,
            "trigger": f"收盘跌破 MA60（{_num(ma60)}）",
            "mitigant": "以 MA60 为止损参考，跌破后减仓观察",
            "sourceRefs": _source_refs(1),
        },
        {
            "title": "估值回归",
            "severity": "medium" if (pe_pct or 50) > 60 else "low",
            "trigger": f"PE 分位升破 80%（当前 {_num(pe_pct, 0)}%）",
            "mitigant": "以盈利增速对冲估值压力",
            "sourceRefs": _source_refs(1),
        },
    ]
    risk_blocks: list[dict[str, Any]] = [
        {
            "type": "callout",
            "tone": "warn",
            "title": "首要风险",
            "text": f"{risks[0]['title']}：{risks[0]['trigger']}。{risks[0]['mitigant']}。",
        },
        {
            "type": "bullets",
            "items": [
                {
                    "text": f"{item['title']}（{item['severity']}）：{item['trigger']} → {item['mitigant']}",
                    "sourceRefs": item["sourceRefs"],
                }
                for item in risks
            ],
        },
    ]
    sections.append({"id": "risks", "title": "风险", "blocks": risk_blocks})

    # Timeline first, closing note last: the note reads like an epilogue, so it
    # must be the final block of the final section, not something a later append
    # can land behind.
    catalysts_blocks: list[dict[str, Any]] = [
        {
            "type": "bullets",
            "items": [
                {"text": "下一期财报：验证营收与利润增速是否延续。", "sourceRefs": _source_refs(3)},
                {"text": f"技术面：观察能否守住 MA20（{_num(ma20)}）。", "sourceRefs": _source_refs(1)},
                {"text": f"估值面：关注 PE 分位能否维持在 {_num(pe_pct, 0)}% 附近。", "sourceRefs": _source_refs(1)},
            ],
        },
    ]
    if len(financials) >= 2:
        catalysts_blocks.append(
            {
                "type": "timeline",
                "events": [
                    {
                        "date": _period_end_date(item.get("period")),
                        "title": f"{item.get('period')} 营收 {_yi(item.get('revenue'))}",
                        "detail": f"营收同比 {_pct(item.get('revenueYoY'))}，净利同比 {_pct(item.get('netProfitYoY'))}。",
                        "sourceRefs": _source_refs(3),
                    }
                    for item in financials[:6]
                ],
            }
        )
    catalysts_blocks.append({"type": "divider"})
    catalysts_blocks.append(
        {
            "type": "paragraph",
            "text": "以上跟踪项任一恶化即触发结论复核；本报告为示例模式产物，仅用于展示报告结构与交互，请勿据此决策。",
        }
    )
    sections.append({"id": "catalysts", "title": "催化剂与跟踪项", "blocks": catalysts_blocks})

    doc = {
        "schema": "aragon.research.report/v1",
        "provisional": False,
        "instrument": {
            "symbol": symbol,
            "name": name,
            "market": market if market in ("CN", "HK", "US") else "CN",
            "currency": currency,
        },
        "asOf": as_of,
        "headline": (
            f"{name}：{'趋势偏强' if bullish else '趋势待确认'}，估值处于{_num(pe_pct, 0)}% 分位"
            if pe_pct is not None
            else f"{name}：示例模式报告"
        )[:160],
        "rating": {
            "stance": stance,
            "conviction": conviction,
            "horizon": "6-12m",
            **({"targetPrice": target} if target else {}),
            **({"upsidePct": upside} if upside is not None else {}),
        },
        "summary": (
            f"{trend_note}{valuation_note}"
            f"营收同比 {_pct(rev_yoy)}、净利同比 {_pct(profit_yoy)}。"
            "本段为示例模式自动生成，用于演示阅读体验。"
        )[:600],
        "keyMetrics": key_metrics,
        "sections": sections,
        "risks": risks,
        "sources": [
            {
                "id": item.get("id", "S1"),
                "title": item.get("title", "数据源"),
                "publisher": item.get("publisher", "unknown"),
                "publishedAt": item.get("publishedAt"),
                "url": item.get("url"),
            }
            for item in sources[:20]
        ]
        or [{"id": "S1", "title": "行情快照", "publisher": "synthetic"}],
        "disclaimer": "本报告由 AI 生成，仅供研究参考，不构成投资建议。",
    }
    return doc
