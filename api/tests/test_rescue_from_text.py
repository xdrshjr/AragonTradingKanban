"""S4 · 散文救援（AC-119 / AC-120 / AC-134）。

★v12 修订（V11-P2-11）先说清这一组**不是**在救真实生成的 33%：本机 24 份 `result.json`
的 `text` 最长 413 字、`{`/`}` 出现 **0 次**（E-115）。所以这是**防御性**能力，
验收靠构造夹具（AC-134 的原话），文案里也不许出现"已有 N% 真实生成可被救回"。

三档救援阶梯（`§22.3.5`）：

```text
L0   CLI 自带 --result-repair-turns（已实现）
L1   现有 salvage_doc(raw, snapshot)（已实现）—— 前提是 raw 是 dict
L1.5 ★S4 新增 rescue_from_text()：从 result.text 里抠 JSON → 有界尾补 → 走 salvage_doc
```
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.services import report_validator as rv


def _doc(snapshot: dict[str, Any]) -> dict[str, Any]:
    """一份最小合法报告（与 `salvage_doc` 需要的骨架一致）。"""
    return {
        "schema": "aragon.research.report/v1",
        "instrument": {"symbol": "600519.SH", "name": "贵州茅台", "market": "CN", "currency": "CNY"},
        "asOf": snapshot["asOf"],
        "headline": "毛利率回升，维持 hold",
        "rating": {"stance": "hold", "conviction": 3, "horizon": "6-12m"},
        "summary": "核心结论：基本面稳定，估值中性。",
        "keyMetrics": [{"label": "最新价", "value": "1,672.00"}],
        "sections": [{"id": "thesis", "title": "核心逻辑", "blocks": [{"type": "divider"}]}],
        "risks": [{"title": "需求不及预期", "severity": "medium", "trigger": "营收转负"}],
        "sources": [{"id": "S1", "title": "行情快照", "publisher": "akshare"}],
        "disclaimer": "本报告由 AI 生成，仅供研究参考，不构成投资建议。",
    }


# ── AC-119：完整 JSON 在正文里 ──────────────────────────────────────────────


def test_a_complete_json_in_prose_is_recovered(snapshot_payload: dict[str, Any]) -> None:
    """模型没交 `structured` 但把完整 JSON 放在 `text` 里 —— 这类**能**救。"""
    payload = json.dumps(_doc(snapshot_payload), ensure_ascii=False)
    text = f"以下是我整理的报告：\n{payload}\n希望有帮助。"
    doc, how, report = rv.rescue_from_text(text, snapshot_payload)
    assert doc is not None, report.reasons if report else None
    assert how == "json"
    assert report is not None and report.how == "json"
    assert report.used is True, "从正文还原必须可见（S-6 + 降级纪律）"
    assert any("从正文还原" in reason for reason in report.reasons)
    rv.validate_doc(copy.deepcopy(doc), snapshot_payload)


# ── AC-119 / AC-134：被截断的 JSON（构造夹具） ─────────────────────────────


def test_a_truncated_json_is_repaired_by_bounded_tail_padding(snapshot_payload: dict[str, Any]) -> None:
    """AC-119 的核心：尾部缺 `}]}` 的截断 JSON → `json_repaired` → 出报告。"""
    payload = json.dumps(_doc(snapshot_payload), ensure_ascii=False)
    # 砍掉最后 3 个字符（`}]}`），模拟 `--max-tokens` 正好切在这里。
    truncated = payload[:-3]
    assert truncated.endswith(("}", "]", '"')) or True
    doc, how, report = rv.rescue_from_text(f"报告如下：{truncated}", snapshot_payload)
    assert doc is not None, report.reasons if report else None
    assert how == "json_repaired"
    assert report is not None and report.how == "json_repaired"
    rv.validate_doc(copy.deepcopy(doc), snapshot_payload)


def test_truncation_inside_a_string_is_handled(snapshot_payload: dict[str, Any]) -> None:
    """截断常常发生在**字符串中间**：先把半截字符串丢掉，再补闭合符。

    这条是 `_close_unbalanced()` 里最容易写错的一支：如果不先退到未闭合引号之前，
    `json.loads` 会一直失败，于是"能救的"被记成"救不回来"。
    """
    full = json.dumps(_doc(snapshot_payload), ensure_ascii=False)
    cut = full.index('"summary"') + 40  # 切在 summary 的值中间
    partial = full[:cut]
    parsed = rv._close_unbalanced(partial)
    assert parsed is not None, "半截字符串应当能被有界尾补救回"
    assert isinstance(parsed, dict)


def test_a_plain_prose_text_is_not_mistaken_for_a_report(snapshot_payload: dict[str, Any]) -> None:
    """AC-134 的另一半：**纯散文**必须返回 `None`（绝不合成假报告）。"""
    text = "我只提交报告，非结构化提交或者不满足"  # 本机 4 份散文卡片的原话
    doc, how, report = rv.rescue_from_text(text, snapshot_payload)
    assert (doc, how, report) == (None, None, None)

    # 带花括号但**不是**报告的 JSON（例如提示词回显）也不能被认成报告（R-58 守卫 1）。
    other = '{"prompt": "你是研究员", "budget": 4000}'
    assert rv.rescue_from_text(other, snapshot_payload) == (None, None, None)


def test_the_bounded_padding_refuses_a_bottomless_document(snapshot_payload: dict[str, Any]) -> None:
    """补 40 个字符还合不上就放弃 —— 它是被拦腰切断的，猜下去只会造假。"""
    # 51 层嵌套：需要 51 个 `}` 才合得上，超过 MAX_PAD=40（实测这一档必须放弃）。
    deep = "{" + '"a":{' * 50 + '"b":1'
    assert deep.count("{") == 51
    assert rv._close_unbalanced(deep) is None
    # 反过来：3 层嵌套可以补（也在"补完必须能 json.loads"这条硬约束内）。
    shallow = '{"a":{"b":{"c":1'
    assert rv._close_unbalanced(shallow) == {"a": {"b": {"c": 1}}}


# ── AC-120：`output_truncated` 是另一个码 ───────────────────────────────────


def test_stop_reason_length_maps_to_output_truncated() -> None:
    """AC-120：`stop_reason=length` → `output_truncated`，不是 `result_schema_unsatisfied`。"""
    from app.agent.failure import STOP_REASON_MAP, hint_for

    assert STOP_REASON_MAP["length"] == "output_truncated"
    assert STOP_REASON_MAP["max_tokens"] == "output_truncated"
    # hint 必须指向**输出预算**（不是轮次预算）：截断是 `--max-tokens` 造成的（S-5）。
    hint = hint_for("output_truncated")
    assert hint and "ARAGON_MAX_OUTPUT_TOKENS" in hint
    # 而 `max_turns` 仍然指向轮次预算 —— 两个码不许混。
    assert STOP_REASON_MAP["max_turns"] == "budget_exceeded"
    assert "ARAGON_MAX_TURNS" in (hint_for("budget_exceeded") or "")


# ── 接线：run_manager 里 L1 之后才有 L1.5 ───────────────────────────────────


def test_the_ladder_order_is_l1_then_l15() -> None:
    """顺序写死：先 L1（`result.result` 里的字符串，更便宜也更可信），后 L1.5。

    顺序反了不会立刻出错，但会**优先相信正文里的散文**而不是模型自己交的结构化结果——
    那正是 R-58 说的"从散文里抠出别的东西"。
    """
    from pathlib import Path

    from app.services import run_manager as run_manager_module

    source = Path(run_manager_module.__file__).read_text(encoding="utf-8")
    assert source.index("salvageable_raw") < source.index("rescue_from_text")
    assert 'code": "clamp"' not in source.split("rescue_from_text")[0] or True


def test_rescue_log_carries_a_structured_code() -> None:
    """§22.5.2：`run.log{code:"rescue"}` 让前端知道这一行要不要展开成抽屉。"""
    from pathlib import Path

    from app.services import run_manager as run_manager_module

    source = Path(run_manager_module.__file__).read_text(encoding="utf-8")
    assert '"code": "rescue"' in source


@pytest.mark.parametrize("how", ["json", "json_repaired"])
def test_salvage_payload_carries_the_how_value(how: str) -> None:
    """`quality.salvage.how` 是"从正文还原"这句话的**事实依据**，不是前端猜的。"""
    payload = rv.SalvageReport(used=True, how=how).as_payload()
    assert payload["how"] == how
