"""数字分类与接地报告（spec §19.3.2，AC-46 ~ AC-48 / AC-60 / AC-61）。

分类表的每一行在这里都有独立用例：这是本轮唯一改**正确性**的改动，靠"整体比例看起来
还行"是验不出来的。特别地，AC-60 把本机实测的失败（`'2025-03-14'` 被拆成三个必然
失配的 fact claim，证据 E-29）固化成回归，AC-61 用**方向性**断言证明顶层
`keyMetrics` 真的在分母里（只比两个字段相等，它们也可能一起算错）。
"""

from __future__ import annotations

import copy
import json

import pytest

from app.services import numeric_claims as nc


def _doc(*blocks: dict, key_metrics: list[dict] | None = None, section_id: str = "thesis") -> dict:
    return {
        "asOf": "2026-02-10T01:00:00Z",
        "keyMetrics": key_metrics or [],
        "sections": [{"id": section_id, "title": "核心逻辑", "blocks": list(blocks)}],
    }


def _claims(doc: dict) -> list[nc.Claim]:
    return nc.extract_claims(doc)


def _by_class(doc: dict, cls: str) -> list[nc.Claim]:
    return [claim for claim in _claims(doc) if claim.cls == cls]


def _snapshot(**overrides) -> dict:
    payload = {
        "asOf": "2026-02-10T01:00:00Z",
        "quote": {"last": 1672.0, "changePct": 1.24},
        "performance": {"ret1y": 12.3, "maxDrawdown1y": -18.2},
        "valuation": {"peTtm": 24.1, "pb": 7.2},
        "financials": [{"period": "2025Q3", "revenue": 123456000000.0, "revenueYoY": 15.2}],
    }
    payload.update(overrides)
    return payload


# ── AC-46：分类表逐行 ────────────────────────────────────────────────────────


def test_row_a_identifier_and_date_tokens_are_phase_a_suppressed() -> None:
    """阶段 A：`RSI(14)` / `MA20` / `2025-03-14` / `Q3` 不是测量值。"""
    doc = _doc({"type": "paragraph", "text": "RSI(14) 在 MA20 上方，2025-03-14 公布，Q3 环比改善。"})
    assert _by_class(doc, "fact") == []
    assert len(_by_class(doc, "identifier")) >= 4


def test_row_b_paragraph_and_heading_are_facts() -> None:
    doc = _doc(
        {"type": "paragraph", "text": "营收 1234.5 亿元。"},
        {"type": "heading", "level": 3, "text": "毛利 91.3%"},
    )
    raws = [claim.raw for claim in _by_class(doc, "fact")]
    assert raws == ["1234.5", "91.3"]


def test_row_b_bullets_are_facts() -> None:
    doc = _doc({"type": "bullets", "items": [{"text": "份额 12.3%"}, {"text": "增速 8.1%"}]})
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["12.3", "8.1"]


def test_row_b_callout_title_and_text_are_facts() -> None:
    """★A3：`3 条` 是**计数**（量词），不是观测值 —— 归 identifier，不进分母。

    旧期望是 `["3", "1800"]`：把"关键结论 3 条"里的 `3` 当成一个需要核的数字，
    于是任何一份写了"三条理由/五个要点"的报告都会平白多一条缺口。
    """
    doc = _doc({"type": "callout", "tone": "insight", "title": "关键结论 3 条", "text": "目标价 1800 元。"})
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["1800"]
    assert [claim.raw for claim in _by_class(doc, "identifier")] == ["3 条"]


def test_row_b_metrics_values_are_facts_and_dashes_are_skipped() -> None:
    doc = _doc(
        {
            "type": "metrics",
            "items": [
                {"label": "最新价", "value": "1,672.00"},
                {"label": "股息率", "value": "n/a"},
                {"label": "回撤", "value": "—"},
            ],
        }
    )
    facts = _by_class(doc, "fact")
    assert [claim.raw for claim in facts] == ["1,672.00"]
    assert facts[0].canonical == "1672"


def test_row_b_metrics_labels_are_identifiers_not_facts() -> None:
    doc = _doc({"type": "metrics", "items": [{"label": "RSI(14)", "value": "56.2"}]})
    assert [claim.raw for claim in _by_class(doc, "identifier")] == ["RSI(14)"]
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["56.2"]


def test_row_b_table_cells_and_caption_are_facts() -> None:
    doc = _doc(
        {
            "type": "table",
            "columns": [{"key": "year", "label": "年度"}, {"key": "revenue", "label": "营收"}],
            "rows": [{"year": "2025", "revenue": "1234.5"}, {"year": "2024", "revenue": "1100.0"}],
            "caption": "合计增长 15.2%",
        }
    )
    facts = [claim.raw for claim in _by_class(doc, "fact")]
    assert facts == ["1234.5", "1100.0", "15.2"], "年份列必须被阶段 A 剔除"


def test_row_b_chart_title_and_note_are_facts() -> None:
    doc = _doc(
        {
            "type": "chart",
            "chart": {
                "kind": "line",
                "title": "营收增速 15.2%",
                "xAxis": {"type": "category", "labels": ["Q1", "Q2"]},
                "series": [{"name": "营收", "data": [1.0, 2.0]}],
                "note": "口径为 2025 年报",
            },
        }
    )
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["15.2"]


def test_row_b_chart_axis_labels_are_identifiers() -> None:
    doc = _doc(
        {
            "type": "chart",
            "chart": {
                "kind": "bar",
                "title": "季度分布",
                "xAxis": {"type": "category", "labels": ["Q1", "Q2", "Q3"]},
                "series": [{"name": "营收", "data": [1.0, 2.0, 3.0]}],
            },
        }
    )
    labels = [claim.raw for claim in _by_class(doc, "identifier")]
    assert set(labels) == {"Q1", "Q2", "Q3"}


def test_row_b_comparables_are_projection_without_any_upgrade_path() -> None:
    """★V6-P1-1：同业估值是模型的估计，**没有**"带 sourceRefs 就升格"的通道。"""
    doc = _doc(
        {
            "type": "comparables",
            "peers": [
                {"name": "五粮液", "symbol": "000858.SZ", "pe": 18.2, "pb": 4.1, "growthPct": 8.1},
                {"name": "泸州老窖", "symbol": "000568.SZ", "pe": 22.5, "pb": 6.0, "growthPct": 11.2},
            ],
        }
    )
    projections = _by_class(doc, "projection")
    assert len(projections) == 6
    assert {claim.path for claim in projections} == {
        "thesis.comparables[0].peers[0].pe",
        "thesis.comparables[0].peers[0].pb",
        "thesis.comparables[0].peers[0].growthPct",
        "thesis.comparables[0].peers[1].pe",
        "thesis.comparables[0].peers[1].pb",
        "thesis.comparables[0].peers[1].growthPct",
    }
    assert _by_class(doc, "fact") == []


def test_row_b_scenario_targets_and_probabilities_are_projection() -> None:
    doc = _doc(
        {
            "type": "scenario",
            "scenarios": [{"name": "bull", "targetPrice": 1980.0, "probability": 0.25, "assumptions": "营收增速 15%"}],
        }
    )
    assert len(_by_class(doc, "projection")) == 2
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["15"]


def test_row_b_scenario_assumptions_are_facts() -> None:
    doc = _doc(
        {
            "type": "scenario",
            "scenarios": [
                {"name": "base", "targetPrice": 1800.0, "probability": 0.5, "assumptions": "毛利率维持 91.3%"}
            ],
        }
    )
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["91.3"]


def test_row_b_quote_and_timeline_are_facts() -> None:
    doc = _doc(
        {"type": "quote", "text": "毛利率 91.3% 是护城河。", "attribution": "2025 年报", "sourceRefs": ["S2"]},
        {
            "type": "timeline",
            "events": [{"date": "2025-10-28", "title": "三季报发布", "detail": "营收 1234.5 亿"}],
        },
    )
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["91.3", "1234.5"]


def test_row_b_top_level_key_metrics_are_in_the_denominator() -> None:
    """★V6-P1-5：顶层 `keyMetrics[]` 不在 `sections[]` 内，但它是报告门面。"""
    doc = _doc(key_metrics=[{"label": "最新价", "value": "1,672.00"}, {"label": "近 1 年收益", "value": "+12.3%"}])
    facts = _by_class(doc, "fact")
    assert [claim.raw for claim in facts] == ["1,672.00", "12.3"]
    assert {claim.block_id for claim in facts} == {"keyMetrics"}
    # ★A3：`近 1 年` 是期间长度，整段被剔除（旧期望是裸 `1`）。
    assert [claim.raw for claim in _by_class(doc, "identifier")] == ["1 年"]


def test_row_b_key_metric_label_is_an_identifier() -> None:
    doc = _doc(key_metrics=[{"label": "MA20", "value": "1650.2"}])
    assert [claim.raw for claim in _by_class(doc, "identifier")] == ["MA20"]
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["1650.2"]


def test_row_b_derived_blocks_are_counted_separately() -> None:
    """`derived: true` 的块，其数字是推算值：**计入分母**，另记 `derivedClaims`。

    ★I-50：spec 分类表第 16 行写的是"计入分母但单独计数"。落地时只加了
    `derived_claims`，推算值于是既不进分母也不进 `factMissing`——"单独计数"
    变成了"免于核查"。现在它与 fact 走同一张账，只是多一个标签供 UI 标「推算」。
    """
    doc = _doc({"type": "paragraph", "text": "按市值/PE 反算 EPS 约 69.4 元。", "derived": True})
    assert _by_class(doc, "fact") == []
    assert [claim.raw for claim in _by_class(doc, "derived")] == ["69.4"]
    report = nc.grounding_report(doc, {})
    assert report.derived_claims == 1
    assert report.fact_claims == 1, "推算值必须进分母，否则它免于核查"
    assert report.fact_missing == 1, "快照里没有这个推算值，必须记成缺口"


def test_classify_helper_agrees_with_the_extraction_table() -> None:
    assert nc.classify("valuation.comparables[1].peers[0].pe") == "projection"
    assert nc.classify("valuation.scenario[0].scenarios[1].probability") == "projection"
    assert nc.classify("fundamentals.metrics[0].items[2].label") == "identifier"
    assert nc.classify("fundamentals.table[1].rows[0].revenue") == "fact"
    assert nc.classify("thesis.paragraph[0].text", kind="derived") == "derived"


# ── AC-60：阶段 A 的日期抑制（本机实测失败的回归） ──────────────────────────


def test_phase_a_strips_dates() -> None:
    """AC-60：日期一个都不许进 fact 分母。

    这是把本机实测 E-29 固化成回归：改造前 `number_tokens('2025-03-14 公告')` 会得到
    `['2025', '-03', '-14']` 三个必然失配的 fact claim。
    """
    text = "2025-03-14 公告，2025 年营收 12.3 亿，RSI(14)=62，Q3 环比 +15.2%"
    doc = _doc({"type": "paragraph", "text": text})

    facts = [claim.raw for claim in _by_class(doc, "fact")]
    # `62` 是 RSI 的**取值**（测量），不是方法参数——它必须留在分母里。
    # spec AC-60 那一行的字面清单只写了 12.3 / 15.2，漏了 62，见 spec.md 的
    # 「实施过程发现的方案缺陷」I-37；判据本身（日期一个都不许进分母）不变。
    assert facts == ["12.3", "62", "15.2"], f"日期/标识符漏进了分母：{facts}"
    identifiers = _by_class(doc, "identifier")
    # 剔除次数 = `2025-03-14` + `2025 年` + `RSI(14)` + `Q3`（4 段）。
    # （spec §19.7.2 AC-60 把这四项写成 "5"，是那一行自己的算术笔误，见 spec.md
    # 「实施过程发现的方案缺陷」I-37；判据是"日期一个都不许进分母"，本节遵守。）
    assert len(identifiers) == 4, [(claim.raw, claim.path) for claim in identifiers]
    assert "2025" not in facts and "-03" not in facts and "-14" not in facts


def test_phase_a_strips_iso_timestamps_whole() -> None:
    """★I-44：`2026-11-16T18:02:30Z` 必须整段剔除。

    真实报告里翻过车：标识符先跑，`of 2026` 被吃掉，剩下的 `-11-16T18:02:30Z`
    变成 `-11` / `-16` / `18` / `02` / `30` 五个必然失配的 fact claim。
    所以日期必须在标识符**之前**剥离（见 `_strip_with_removals`）。
    """
    doc = _doc({"type": "paragraph", "text": "as-of 2026-11-16T18:02:30Z，营收 12.3 亿。"})
    facts = [claim.raw for claim in _by_class(doc, "fact")]
    assert facts == ["12.3"], f"时间戳漏进了分母：{facts}"


def test_phase_a_strips_two_digit_periods_before_identifiers() -> None:
    """★I-44：`26Q1` / `9M26` / `FY25` 不能被标识符拆成裸 `26` / `9` / `25`。

    真实报告里 `26` 单独作为 fact claim 出现 **28 次** —— 这正是"先标识符后日期"
    的后果（`Q1` 被 `[A-Za-z]{1,8}\d` 吃掉，剩下 `26`）。
    """
    doc = _doc({"type": "paragraph", "text": "26Q1 营收 547.03 亿元，9M26 同比 +1.82%，FY25 营收 -1.20%。"})
    facts = [claim.raw for claim in _by_class(doc, "fact")]
    assert facts == ["547.03", "1.82", "-1.20"], f"期间标签被拆成了裸数字：{facts}"


def test_numbers_in_text_keeps_method_labels_out_but_measurements_in() -> None:
    assert nc.numbers_in_text("RSI(14) 为 56.2，价格在 MA20 上方") == ["56.2"]
    assert nc.numbers_in_text("PE 24.1 倍，PB 7.2 倍") == ["24.1", "7.2"]
    assert nc.numbers_in_text("2025-03-14") == []


def test_integer_valued_metrics_survive_the_identifier_phase() -> None:
    """★I-49：`PE 24`（整数）也是**测量值**，不是方法参数。

    第一版的 `IDENTIFIER_RE` 用 `\\s*` 把字母与数字跨空格连成一个标识符，于是
    `"PE 24 倍，PB 4 倍"` 里的 `PE 24` 整段被阶段 A 剔除 —— `24` 既不进 `factClaims`
    也不进任何分母，**彻底消失**（O-6 点名的漏检方向）。只有带小数的 `PE 24.1`
    被尾部 `(?![\\d.,])` 救了下来，而真实投研报告里整数写法更常见。
    """
    assert nc.numbers_in_text("PE 24 倍，PB 4 倍") == ["24", "4"]
    # 方法参数仍然必须被剔除：贴合（`MA20`）与括号（`RSI(14)` / `BOLL(20,2)`）两种形态。
    assert nc.numbers_in_text("MA20 上穿 MA60，RSI(14)=62，BOLL(20,2) 开口") == ["62"]
    # `PE 24` 不再命中标识符：这一段**原样保留**（等长空格替换，偏移不变）。
    assert nc.strip_identifiers("PE 24 倍") == "PE 24 倍"


def test_strip_identifiers_preserves_offsets() -> None:
    text = "2025-03-14 营收 12.3 亿"
    stripped = nc.strip_identifiers(text)
    assert len(stripped) == len(text), "等长替换是摘要切片正确的前提"
    assert "12.3" in stripped
    assert "2025" not in stripped


# ── AC-47：同业估值不进分母 ──────────────────────────────────────────────────


def test_comparables_is_projection() -> None:
    """AC-47：一份 8 同业 × 3 指标的报告，删掉 `comparables` 后 ratio 完全相等。"""
    peers = [
        {"name": f"同业{index}", "symbol": f"00000{index}.SZ", "pe": 18.0 + index, "pb": 4.0, "growthPct": 8.0}
        for index in range(8)
    ]
    prose = {"type": "paragraph", "text": "营收 1234.56 亿，增速 15.2%，现价 1672.00 元，PE 24.1 倍，PB 7.2 倍。"}
    with_peers = _doc(prose, {"type": "comparables", "peers": peers})
    without_peers = _doc(prose)

    left = nc.grounding_report(with_peers, _snapshot())
    right = nc.grounding_report(without_peers, _snapshot())

    assert left.ratio == right.ratio == 1.0
    assert left.fact_claims == right.fact_claims
    assert left.projection_claims == 24
    assert left.fact_missing == right.fact_missing == 0


# ── AC-48：样本上限与体积 ────────────────────────────────────────────────────


def test_samples_are_capped_and_locatable() -> None:
    """AC-48：`samples` ≤ 20，每条 `blockId` 可定位，序列化后 < 8 KB。"""
    blocks = [
        {"type": "paragraph", "text": f"这一段编了一个数字 {900000 + index}.7，它不在快照里。"} for index in range(30)
    ]
    report = nc.grounding_report(_doc(*blocks), _snapshot())
    assert report.fact_missing == 30
    assert report.total_missing == 30
    assert len(report.samples) <= nc.MAX_SAMPLES
    assert all(sample["blockId"].startswith("thesis#") for sample in report.samples)
    assert all(sample["path"].endswith(".text") for sample in report.samples)
    assert len(json.dumps(report.as_payload(), ensure_ascii=False)) < 8192


def test_samples_are_deduplicated_by_block() -> None:
    doc = _doc({"type": "paragraph", "text": "编造 900001.1 与 900002.2 两个数字。"})
    report = nc.grounding_report(doc, _snapshot())
    assert report.fact_missing == 2
    assert len(report.samples) == 1, "同一个块只留一条样本，避免 300 块报告撑爆一行 JSON"


def test_sample_coverage_carries_both_denominators() -> None:
    """★R3-1：块数 ≠ 值数。两个分母都要进载荷，否则面板只能说错话。

    同一块里两个不同数值 → `sampled_blocks = 1` 而 `sampled_values = 1`、
    `distinct_missing = 2`：真值是"还有 1 个数值没有样本"，而用块数近似会算出 1 也恰好撞对，
    所以第二段用**两个块 / 三个值**把它区分开：块数 2、已覆盖值 2、未展开 1。
    """
    doc = _doc(
        {"type": "paragraph", "text": "编造 900001.1 与 900002.2 两个数字。"},
        {"type": "paragraph", "text": "第三处 900003.3 也不在快照里。"},
    )
    report = nc.grounding_report(doc, _snapshot())
    assert (report.distinct_missing, report.sampled_blocks, report.sampled_values) == (3, 2, 2)
    # 未展开的数值个数 = distinct_missing − sampled_values（不是 − len(samples)）
    assert report.distinct_missing - report.sampled_values == 1


def test_sample_coverage_counts_every_block_with_a_gap() -> None:
    """截断判据只能是"块数比块数"：30 个块都有缺口、只列 20 个块。"""
    blocks = [{"type": "paragraph", "text": f"编造 {900000 + index}.7。"} for index in range(30)]
    report = nc.grounding_report(_doc(*blocks), _snapshot())
    assert len(report.samples) == nc.MAX_SAMPLES
    assert report.sampled_blocks == 30, "有缺口的块总数必须全量计数，不能被样本上限截断"
    assert report.sampled_values == nc.MAX_SAMPLES, "已列样本只覆盖到 20 个不同数值"


def test_risks_are_sampled_per_risk_not_as_one_list() -> None:
    """★R3-3：三个风险各有编造数字时，面板要能一条一条定位（D5 的"不可定位"）。"""
    doc = _doc({"type": "paragraph", "text": "正文 1672.00 元。"})
    doc["risks"] = [
        {"severity": "high", "title": "估值 900011.1", "trigger": "跌破", "mitigant": None},
        {"severity": "medium", "title": "波动 900012.2", "trigger": "放大", "mitigant": None},
        {"severity": "low", "title": "流动性 900013.3", "trigger": "收紧", "mitigant": None},
    ]
    report = nc.grounding_report(doc, _rich_snapshot())
    risk_samples = [sample for sample in report.samples if sample["blockId"].startswith("riskList[")]
    assert [sample["blockId"] for sample in risk_samples] == ["riskList[0]", "riskList[1]", "riskList[2]"]


# ── 阈值纪律 ────────────────────────────────────────────────────────────────


def test_min_fact_claims_marks_insufficient_sample() -> None:
    """`fact_claims < 5` → ratio 1.0 且 `skipped`，而不是给一个漂亮的 1.00。"""
    doc = _doc({"type": "paragraph", "text": "营收 900001.1 亿。"})
    report = nc.grounding_report(doc, _snapshot())
    assert report.fact_claims == 1
    assert report.fact_missing == 1
    assert report.ratio == 1.0
    assert report.skipped == "insufficient_sample"


def test_enough_claims_is_judged_normally() -> None:
    prose = "营收 1234.56 亿，增速 15.2%，现价 1672.00 元，PE 24.1 倍，PB 7.2 倍，回撤 -18.2%。"
    report = nc.grounding_report(_doc({"type": "paragraph", "text": prose}), _snapshot())
    assert report.skipped is None
    assert report.fact_claims == 6
    assert report.ratio == 1.0


# ── AC-61：分母一致（方向性断言） ───────────────────────────────────────────


def test_denominator_includes_key_metrics() -> None:
    """AC-61：把顶层 `keyMetrics` 的两个数字改成快照里没有的值，`fact_missing` 必须 +2。

    只断言"两个字段相等"是不够的——它们也可能一起漏掉 keyMetrics（R-33）。
    所以这里断言**方向**：改坏两个门面数字，缺失数必须正好加二。
    """
    prose = {"type": "paragraph", "text": "营收 1234.56 亿，增速 15.2%，现价 1672.00 元，PE 24.1 倍，PB 7.2 倍。"}
    good = _doc(
        prose,
        key_metrics=[{"label": "最新价", "value": "1,672.00"}, {"label": "近 1 年收益", "value": "+12.3%"}],
    )
    bad = copy.deepcopy(good)
    bad["keyMetrics"][0]["value"] = "1,999.00"
    bad["keyMetrics"][1]["value"] = "+99.9%"

    before = nc.grounding_report(good, _snapshot())
    after = nc.grounding_report(bad, _snapshot())

    assert before.fact_missing == 0
    assert after.fact_missing == before.fact_missing + 2
    assert after.fact_claims == before.fact_claims
    assert after.total_missing == 2
    # 样本按 `blockId` 去重：两个门面数字共用 `keyMetrics` 这个块 id，所以只留一条样本，
    # 但 `totalMissing` 仍是全量 2（R-22：UI 文案是"1 / 2（仅列前 20）"）。
    assert {sample["blockId"] for sample in after.samples} == {"keyMetrics"}
    assert len(after.samples) == 1


def test_ungrounded_count_equals_fact_missing(snapshot_payload: dict, sample_report: dict) -> None:
    """同一份文档上，`compute_grounding()` 的第二个返回值就是 `factMissing`。"""
    from app.services import report_validator as validator

    doc = copy.deepcopy(sample_report)
    doc["sections"][0]["blocks"].append({"type": "paragraph", "text": "编造 900001.1 与 900002.2。"})

    ratio, missing = validator.compute_grounding(doc, snapshot_payload)
    report = nc.grounding_report(doc, snapshot_payload)
    assert missing == report.fact_missing
    assert round(ratio, 6) == round(report.ratio, 6)

# ── ★A1：命名派生量目录（AC2）────────────────────────────────────────────────

#: 与真实报告同形的快照：8 期累计财报 + 日线收盘 + 技术指标 + 同业。
def _rich_snapshot() -> dict:
    return {
        "asOf": "2026-11-16T18:02:30Z",
        "quote": {"last": 1634.88, "changePct": -2.22, "prevClose": 1672.0, "volume": 44979411},
        "performance": {"ret1w": -2.933022, "ret1m": -5.419387, "ret3m": 4.808595, "ret1y": 17.234133,
                        "maxDrawdown1y": -22.791483, "volAnnualized": 27.972089},
        "valuation": {"peTtm": None, "pb": None, "netMargin": 48.4},
        "financials": [
            {"period": "20260930", "revenue": 133283652508.95, "netProfit": 64467425981.95, "netMargin": 48.4},
            {"period": "20260630", "revenue": 92278072083.21, "netProfit": 44516880421.86, "netMargin": 48.2},
            {"period": "20260331", "revenue": 54702912385.23, "netProfit": 27242512886.45, "netMargin": 49.8},
            {"period": "20251231", "revenue": 172054171890.91, "netProfit": 82320067101.68, "netMargin": 47.8},
            {"period": "20250930", "revenue": 130903889634.88, "netProfit": 64626746712.18, "netMargin": 49.4},
            {"period": "20250630", "revenue": 91093759634.88, "netProfit": 43270000000.00, "netMargin": 48.1},
            {"period": "20250331", "revenue": 51440000000.00, "netProfit": 26860000000.00, "netMargin": 52.2},
            {"period": "20241231", "revenue": 174144145000.00, "netProfit": 86228138794.38, "netMargin": 49.5},
        ],
        "technicals": {"ma5": 1743.934, "ma20": 1775.7895, "ma60": 1747.8765, "rsi14": 43.167,
                       "macd": -8.528, "macdSignal": -2.0992},
        "priceSeries": {"label": "近 60 个交易日收盘价", "close": [1700.0, 1721.63, 1729.52]},
        "peers": [{"symbol": "000858.SZ", "changePct": -1.92}, {"symbol": "000568.SZ", "changePct": 0.78}],
        "sources": [{"id": "S1", "title": "行情与估值快照"}, {"id": "S3", "title": "财务摘要（近 8 期）"}],
        "dataQuality": {"source": "synthetic", "barsCount": 783, "stale": True},
    }


def _value_at(snapshot: dict, identifier: str, doc: dict | None = None) -> float:
    """按 id 取派生量（断言"命中并说出了是哪条公式"）。"""
    derived = nc.derived_values(snapshot, doc)
    assert identifier in derived, sorted(derived)[:8]
    return derived[identifier].value


def _fin(snapshot: dict, index: int, key: str) -> float:
    return snapshot["financials"][index][key]


def _close(snapshot: dict) -> float:
    return snapshot["priceSeries"]["close"][-1]


def _single(snapshot: dict, index: int, key: str) -> float:
    """第 `index` 期的单季金额（本年累计 − 本年更早累计）。"""
    return _fin(snapshot, index, key) - _fin(snapshot, index + 1, key)


def _yoy(snapshot: dict, item: int, base: int, key: str) -> float:
    return (_fin(snapshot, item, key) - _fin(snapshot, base, key)) / _fin(snapshot, base, key) * 100


def _margin(snapshot: dict, index: int) -> float:
    return _single(snapshot, index, "netProfit") / _single(snapshot, index, "revenue") * 100


#: 分析里 15 条手算推导（`grounding_hand_verified.py` 的展品），逐条给出**目录 id** 与算式。
CAMPAIGN = [
    ("1.82", "yoy:financials[0].revenue", lambda s: _yoy(s, 0, 4, "revenue")),
    ("-0.25", "yoy:financials[0].netProfit", lambda s: _yoy(s, 0, 4, "netProfit")),
    ("-1.20", "yoy:financials[3].revenue", lambda s: _yoy(s, 3, 7, "revenue")),
    ("-4.53", "yoy:financials[3].netProfit", lambda s: _yoy(s, 3, 7, "netProfit")),
    ("410.06", "singleQuarter:financials[0].revenue", lambda s: _single(s, 0, "revenue") / 1e8),
    ("375.75", "singleQuarter:financials[1].revenue", lambda s: _single(s, 1, "revenue") / 1e8),
    ("199.51", "singleQuarter:financials[0].netProfit", lambda s: _single(s, 0, "netProfit") / 1e8),
    ("172.74", "singleQuarter:financials[1].netProfit", lambda s: _single(s, 1, "netProfit") / 1e8),
    ("48.7", "singleQuarterMargin:financials[0]", lambda s: _margin(s, 0)),
    ("46.0", "singleQuarterMargin:financials[1]", lambda s: _margin(s, 1)),
    ("-2.4", "pp:financials[2].netMargin", lambda s: _fin(s, 2, "netMargin") - _fin(s, 6, "netMargin")),
    (
        "0.83",
        "distanceMa:ma5@priceSeries.close[-1]",
        lambda s: (s["technicals"]["ma5"] - _close(s)) / _close(s) * 100,
    ),
    (
        "5.47",
        "priceGap:quote.last@priceSeries.close[-1]",
        lambda s: abs(s["quote"]["last"] - _close(s)) / _close(s) * 100,
    ),
    (
        "3.8",
        "magnitudeRatio:financials[3].netProfit/revenue",
        lambda s: abs(_yoy(s, 3, 7, "netProfit")) / abs(_yoy(s, 3, 7, "revenue")),
    ),
    (
        "3.00",
        "singleQuarterYoy:financials[0].revenue",
        lambda s: (_single(s, 0, "revenue") - _single(s, 4, "revenue")) / _single(s, 4, "revenue") * 100,
    ),
]

#: 这份报告的 targetPrice 就是 MA60（1747.88 元），于是"隐含空间"和"距 MA60"是同一个数。
#: 判词取哪一条都对 —— 两条推导都成立、都可命名，测试接受这个别名。
ALLOWED_ALIASES = {"1.06": {"derived:distanceMa:ma60@priceSeries.close[-1]"}}

#: 目标价是**文档**里的判断（快照没有它），但「目标价相对现价的隐含空间」是可命名的推导。
UP_TARGET = 1747.88
UP_CASE = (
    "1.06",
    "upside:rating.targetPrice@priceSeries.close[-1]",
    lambda s: (UP_TARGET - _close(s)) / _close(s) * 100,
)


def test_ac2_every_hand_verified_derivation_is_named_not_fabricated() -> None:
    """AC2：分析里 15 条手算推导，判词必须是 `derived:<id>`（不是 FABRICATED）。

    这 15 行来自 `grounding_hand_verified.py` —— 它们在旧判据下**全部**被判成编造，
    而算式 15/15 都能从快照复现。它们也正是投研报告里最常见的五种写法：
    同比、单季轧差、单季净利率、距均线、隐含空间（外加降幅倍数与单季同比）。
    """
    snapshot = _rich_snapshot()
    doc = {"rating": {"stance": "hold", "conviction": 3, "horizon": "6-12m", "targetPrice": UP_TARGET}}
    corpus = nc.snapshot_number_corpus(snapshot)
    derived = nc.derived_values(snapshot, doc)
    for printed, identifier, formula in [*CAMPAIGN, UP_CASE]:
        # ① 目录里的值确实等于那条算式（目录自己不能算错）
        assert abs(_value_at(snapshot, identifier, doc) - formula(snapshot)) < 1e-9, identifier
        # ② 报告里印的数字能被判成"推算"，且说得清是哪条公式
        label = nc.provenance_of(printed, corpus, derived)
        assert label is not None and label.startswith("derived:"), (printed, label)
        assert label == f"derived:{identifier}" or label in ALLOWED_ALIASES.get(printed, set()), printed


def test_ac2_derived_matching_uses_the_real_rounding_interval() -> None:
    """★R3-2：容差是末位的**半个**单位 —— 只有"四舍五入到这一位"才算同一个数。

    v1 落地时写成一个单位（docstring 却写着半个），于是 `1.9` 被 `yoy 1.8179` 接地
    （1.8179 应印 1.8）、`12` 被 `scenarioUpside 12.8110` 接地（应印 13）、`1.0` 被
    `distanceMa 1.0614` 接地（应印 1.1）。全部历史报告里有 15 条这样凭空接地的 claim。
    """
    snapshot = _rich_snapshot()
    derived = nc.derived_values(snapshot, {"rating": {"targetPrice": UP_TARGET}})
    revenue_yoy = _value_at(snapshot, "yoy:financials[0].revenue", {"rating": {"targetPrice": UP_TARGET}})
    assert abs(revenue_yoy - 1.8179) < 1e-3, revenue_yoy

    def matched(raw: str) -> bool:
        claim = nc.Claim(block_id="", path="", raw=raw, canonical=nc.canonical_number(raw), cls="fact")
        return nc.match_derived(claim, derived) is not None

    assert matched("1.8"), "1.8 确实是 1.8179 的一位小数写法"
    assert not matched("1.9"), "1.8179 印成一位小数是 1.8，不是 1.9"
    assert matched("1.82"), "两位小数同样成立"
    # 整数位：410.0558 印成整数是 410；411 差 0.94，落在一个单位的容差里、半单位之外。
    assert matched("410")
    assert not matched("411")


def test_ac2_a_derivation_outside_the_catalog_is_still_missing() -> None:
    """AC2 的第二半：目录**有限**。目录外的推导仍然算缺口（宁可漏，不可误判为已核验）。"""
    snapshot = _rich_snapshot()
    # 加权平均资本成本：快照里没有任何字段能推出来，目录里也没有它。
    doc = _doc({"type": "paragraph", "text": "按我们的 WACC 假设，折现率取 8.37%。"})
    report = nc.grounding_report(doc, snapshot)
    assert report.fact_missing == 1
    assert report.derived_claims == 0, "目录外的推导不能命中派生通道"


def test_derived_catalog_never_crosses_a_fiscal_year_boundary() -> None:
    """单季轧差只在**同一财年**内成立：`26Q1 − FY25` 不是任何季度的营收。

    没有这道守卫，目录会给一个编造的负数当"裁判"，而它一旦被命中就会替编造值背书。
    """
    snapshot = _rich_snapshot()
    derived = nc.derived_values(snapshot)
    # financials[2] 是 26Q1，financials[3] 是 FY25 —— 跨年，不许有单季轧差
    assert "singleQuarter:financials[2].revenue" not in derived
    # financials[0] 是 9M26，financials[1] 是 H126 —— 同年，必须有
    assert "singleQuarter:financials[0].revenue" in derived


def test_derived_catalog_tolerates_an_empty_snapshot() -> None:
    """空快照是既有用例（`grounding_report(doc, {})`）：目录必须优雅返回空，不能抛。"""
    assert nc.derived_values({}) == {}
    assert nc.derived_values({"financials": None, "quote": None, "technicals": None}) == {}


# ── ★A2/A3：严格值匹配（AC3）────────────────────────────────────────────────


def test_ac3_rounding_and_scaling_no_longer_ground_a_coincidence() -> None:
    """AC3：四个实测的假接地必须消失。

    | token | 旧判据的"依据" | 为什么那不是"这个数" |
    | --- | --- | --- |
    | `1` | `peers[1].changePct` 取 0 位小数（`0.78`） | 舍入损失 28%，只保留 1 位有效数字 |
    | `5` | `performance.ret3m` 取 0 位小数（`4.8086`） | 同上（4%） |
    | `9` | `technicals.macd` 取绝对值（`-8.528`） | 符号是身份的一部分 |
    | `60` | `instrument.symbol`（`600519 ÷ 1e4`） | `万元` 缩放不在白名单 |
    """
    snapshot = _rich_snapshot()
    snapshot["instrument"] = {"symbol": "600519.SH"}
    corpus = nc.snapshot_number_corpus(snapshot)
    for token in ("1", "5", "9", "60"):
        assert nc.provenance_of(token, corpus, None) is None, token


def test_ac3_the_same_value_in_another_legitimate_form_still_matches() -> None:
    """反证：收紧之后，**合法的**写法一个都不能丢（否则就从假阴性掉进另一个坑）。"""
    snapshot = _rich_snapshot()
    corpus = nc.snapshot_number_corpus(snapshot)
    # 元 → 亿元：133,283,652,508.95 元 = 1332.84 亿元
    assert nc.provenance_of("1332.84", corpus)
    # 两位小数的排版自由：1634.88 → 1634.9 / 1635
    assert nc.provenance_of("1634.9", corpus)
    assert nc.provenance_of("1635", corpus)
    # 负号必须同号：-2.93 对 ret1w = -2.933022
    assert nc.provenance_of("-2.93", corpus)
    assert nc.provenance_of("2.93", corpus) is None, "符号不同的不是同一个数"


def test_a3_non_measure_numbers_are_identifiers_not_claims() -> None:
    """★A3：`3/5`、`60 日`、`9M`、`-2.4pp`、`第 3`、`≥ +3.0` 都不是测量值。

    少了这一步，覆盖补齐（A5）会把「信心度 3/5」里的 `3`、`5` 当成"可追溯"（R-E8）。
    """
    doc = _doc(
        {
            "type": "paragraph",
            "text": "信心度 3/5，60 日低点，9M 累计，净利率 -2.4pp，第 3 位，门槛 ≥ +3.0%，3 条理由，5 个收盘。",
        }
    )
    assert [claim.raw for claim in _by_class(doc, "fact")] == []
    assert len(_by_class(doc, "identifier")) >= 8



def test_ac7_a_report_written_in_derivations_has_no_gap_at_all() -> None:
    """AC7′ 的机制：一份**只**用同比/轧差/距均线说话的诚实报告，缺口必须是 0。

    这正是本轮的根因病灶：旧判据下这份报告的 6 个数字全部 FABRICATED → ratio 0 →
    `degraded` → `asks.py:76` 把 `deep` 追问整档关掉（N1）。现在它们都命中命名目录，
    分母照算、缺口为零，状态机不需要动一个字。
    """
    snapshot = _rich_snapshot()
    doc = _doc(
        {
            "type": "paragraph",
            "text": "营收同比 +1.82%，净利同比 -0.25%；单季营收 410.06 亿元、单季净利率 48.7%；"
            "现价距 MA5 0.83%，隐含空间 +1.06%。",
        }
    )
    doc["rating"] = {"stance": "hold", "conviction": 3, "horizon": "6-12m", "targetPrice": UP_TARGET}
    report = nc.grounding_report(doc, snapshot)
    assert report.fact_claims >= 6
    assert report.fact_missing == 0, [(s["raw"], s["path"]) for s in report.samples]
    assert report.derived_claims >= 6
    assert report.ratio == 1.0


# ── ★A4：按值去重计数 ──────────────────────────────────────────────────────


def test_ac4_distinct_counts_separate_values_from_references() -> None:
    """AC4：同一处数字被多个块引用时，"不同数值"与"引用次数"必须分开计。"""
    snapshot = _rich_snapshot()
    blocks = [
        {"type": "paragraph", "text": "编造 910001.5%。"},
        {"type": "paragraph", "text": "又一处 910001.5%。"},
        {"type": "paragraph", "text": "还有 910001.5%。"},
        {"type": "paragraph", "text": "另一处 910002.5%。"},
    ]
    report = nc.grounding_report(_doc(*blocks), snapshot)
    assert report.fact_missing == 4
    assert report.total_missing == 4
    assert report.distinct_missing == 2, "4 次引用只是 2 个不同数值"
    assert report.distinct_claims >= report.distinct_missing


# ── ★A5：覆盖补齐（摘要 / 标题 / 风险清单）──────────────────────────────────


def test_ac5_summary_headline_and_risks_are_audited_with_container_ids() -> None:
    """AC5：读者第一屏也必须被审计，且 id **不含 `#`**（评审 R-E7）。

    `summary#0` 会被 `parseBlockId()` 解析成"章节 summary 的第 0 块"，
    点击后 `setActiveSection('summary')` 把正文过滤成空；`risks#N` 会安静地跳错块。
    所以三个容器一律用顶层名字，由前端的"非块 id"安全分支处理。
    """
    doc = _doc({"type": "paragraph", "text": "正文 1672.00 元。"})
    doc["summary"] = "本季营收 900001.7 亿元，同比 900002.3%。"
    doc["headline"] = "目标价 900003.9 元"
    doc["risks"] = [
        {"severity": "high", "title": "估值回归", "trigger": "PE 分位升破 900004.1%", "mitigant": "对冲"},
    ]

    paths = {claim.path for claim in nc.extract_claims(doc)}
    assert "summary" in paths
    assert "headline" in paths
    assert "riskList[0].trigger" in paths
    assert not any(path.startswith(("summary#", "headline#", "risks[")) for path in paths)

    block_ids = {claim.block_id for claim in nc.extract_claims(doc)}
    assert {"summary", "headline", "riskList[0]"} <= block_ids
    # 分量：真块 id 一律是 `section#index`；**不带 `#` 的只能是容器**，而容器恰好是
    # summary / headline / riskList[i]（`keyMetrics` 是 v6 的老容器，本 doc 有指标卡才有）。
    container_ids = {block_id for block_id in block_ids if "#" not in block_id}
    expected = {"summary", "headline", "riskList[0]"}
    if "keyMetrics" in block_ids:
        expected.add("keyMetrics")
    assert container_ids == expected

    report = nc.grounding_report(doc, _rich_snapshot())
    assert report.fact_missing >= 4, "摘要/标题/风险清单里的编造数字必须计入缺口"


def test_ac5_covered_fields_do_not_change_the_ratio_when_they_are_clean() -> None:
    """覆盖补齐不能靠"多算数字"来改变比例：干净的摘要不产生缺口。"""
    snapshot = _rich_snapshot()
    doc = _doc({"type": "paragraph", "text": "现价 1634.88 元。"})
    doc["summary"] = "现价 1634.88 元，近一年 17.23%。"
    doc["headline"] = "贵州茅台：持有"
    doc["risks"] = [{"severity": "high", "title": "价格波动", "trigger": "跌破 1634.88 元", "mitigant": "减仓"}]
    report = nc.grounding_report(doc, snapshot)
    assert report.fact_missing == 0
    assert report.distinct_missing == 0


# ── ★A6：口径版本与输入指纹（AC1″）──────────────────────────────────────────


def test_ac6_payload_carries_rules_and_input_fingerprints() -> None:
    """AC1″：`rules + docHash + snapshotHash` 让判词**可机械复算**。

    AC1 是"两份数据碰巧一致"，AC1″ 是"输入指纹对得上"—— 后者才能在规则变化后
    证明"这条判词不是今天这套判据算的"。
    """
    snapshot = _rich_snapshot()
    doc = _doc({"type": "paragraph", "text": "现价 1634.88 元。"})
    first = nc.grounding_report(doc, snapshot)
    second = nc.grounding_report(copy.deepcopy(doc), copy.deepcopy(snapshot))

    # ★v12（S5）：口径升到 `claims-v4`（TTM/前高/布林带三类命名派生量 + 区间破折号 +
    # 近似推算层 + 一位小数的舍入豁免）。**升版本号本身就是纪律**：落库值不带 rules
    # 或 rules 不同 ⇒ 界面不给可比百分比（§22.6.3 / S-11）。
    assert first.rules == nc.RULES_VERSION == "claims-v4"
    assert len(first.doc_hash) == 12 and len(first.snapshot_hash) == 12
    # ① 同一输入 → 同一指纹，且判词逐字相同
    assert first.doc_hash == second.doc_hash
    assert first.snapshot_hash == second.snapshot_hash
    assert first.as_payload() == second.as_payload()
    # ② 输入变了 → 指纹变（否则"可复算"是假的）
    doc["summary"] = "换了一段摘要。"
    assert nc.grounding_report(doc, snapshot).doc_hash != first.doc_hash
    # ③ 载荷里三个字段都在（前端靠 `rules` 决定给不给可比百分比）
    payload = first.as_payload()
    assert payload["rules"] == first.rules
    assert payload["docHash"] == first.doc_hash
    assert payload["snapshotHash"] == first.snapshot_hash


# ── ★v12 S5：区间破折号 / 中文月日 / 三类派生量 / 近似推算 ───────────────────


def test_ac122_a_range_dash_is_not_a_minus_sign() -> None:
    """AC-122 的前半句：`1,296.22–1,509.76` 只产出**两个正数** claim。

    实测（E-112）：`目标价区间 1,296.22–1,509.76 元` 会被抽成 `1,296.22` + `-1,509.76`，
    而第二个数**必然**未接地（与语料差 98.44%）——两端的数本来都在快照里。
    """
    doc = _doc({"type": "paragraph", "text": "目标价区间 1,296.22–1,509.76 元。"})
    raws = [claim.raw for claim in _by_class(doc, "fact")]
    assert raws == ["1,296.22", "1,509.76"], raws
    assert all(float(claim.canonical) >= 0 for claim in _by_class(doc, "fact"))


def test_ac122_the_ascii_hyphen_is_equally_affected() -> None:
    """★v12 修订（V11-P1-7）：ASCII 连字符同样中招，所以不能只处理 `–` / `−`。"""
    doc = _doc({"type": "paragraph", "text": "目标价区间 1,296.22-1,509.76 元。"})
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["1,296.22", "1,509.76"]

@pytest.mark.parametrize("separator", [*nc.RANGE_DASHES, nc.RANGE_WORD, "-"])
def test_ac122_every_separator_is_handled(separator: str) -> None:
    """★评审修正（E-114）：AC-122 要求**参数化覆盖六种分隔符**，而上一版只验了两种。

    这条用例直接遍历生产常量（`RANGE_DASHES` + `RANGE_WORD` + ASCII `-`），于是
    "哪些字符算区间分隔符"只有**一份**声明：往常量里加一个字符，这条用例立刻多跑一条；
    在归一化里漏折一个字符，它立刻红。上一版的实际情况是常量写五个、生产代码折三个、
    用例测两个——三份清单，没有任何断言在管它们是否一致。

    每种分隔符都断言同一件事：两个**正数**，没有负号。
    """
    text = f"目标价区间 1,296.22{separator}1,509.76 元。"
    raws = [claim.raw for claim in _by_class(_doc({"type": "paragraph", "text": text}), "fact")]
    assert raws == ["1,296.22", "1,509.76"], f"分隔符 {separator!r} 产出 {raws}"
    assert all(not raw.startswith("-") for raw in raws), f"分隔符 {separator!r} 造出了负号"


def test_a_real_negative_number_keeps_its_sign() -> None:
    """区间的守卫**不能**顺手把真正的负数也掰成正数。

    `最大回撤 −18.2%` 里破折号前面不是数字，所以 `_is_range_separator()` 第 1 条就挡住了；
    它的符号是身份的一部分（A2）。
    """
    doc = _doc({"type": "paragraph", "text": "最大回撤 −18.2%，较上月 -3.4%。"})
    raws = [claim.raw for claim in _by_class(doc, "fact")]
    assert "-18.2" in raws, raws
    # `-3.4pp` 归 identifier 是**设计**（`NON_MEASURE_RE` 的 `Npp` 分支：单位不是快照字段），
    # 这里用百分号写法，避免把两条不同判据混在一起。
    assert "-3.4" in raws, raws


def test_a_range_with_a_negative_left_endpoint_keeps_the_right_one_positive() -> None:
    """★评审修正（评审节点 I-125）：左端点是负数，**不改变**破折号的性质。

    上一版多了一条"左侧数字本身不带符号"的判据，于是 `-1,000–2,000` 被读成
    `['-1,000', '-2,000']` —— 右端点凭空多了一个负号，而它在那个句子里是正的。
    这正是本条守卫要消灭的那一类**假缺口**（与 E-112 的 `-1,509.76` 同源）：
    快照里只有 `2,000`，而报告被记成"编造了 -2,000"。

    反向的边界同时钉住：`-10–-5` 两端都是负数，靠"破折号右邻不是数字"这一条保住符号。
    """
    for separator in ("–", "-"):
        text = f"回撤区间 -1,000{separator}2,000 元。"
        raws = [claim.raw for claim in _by_class(_doc({"type": "paragraph", "text": text}), "fact")]
        assert raws == ["-1,000", "2,000"], f"分隔符 {separator!r} 产出 {raws}"
    both_negative = _doc({"type": "paragraph", "text": "区间 -10–-5 元。"})
    assert [claim.raw for claim in _by_class(both_negative, "fact")] == ["-10", "-5"]


def test_ac122_chinese_month_day_is_still_clean() -> None:
    """AC-122 的后半句**改成回归**用例：中文月日今天已经零 fact claim。

    `NON_MEASURE_RE` 的 `\d{1,4}\s*(?:日|年|周|月)` 早已覆盖，`DATE_LIKE_RE` 也覆盖
    `2025 年 3 月 14 日`。所以本轮**不动**它，只用一条回归钉住（E-112 的实测）。
    """
    doc = _doc({"type": "paragraph", "text": "11 月 2 日披露，2025 年 3 月 14 日复牌。"})
    assert _by_class(doc, "fact") == [], [c.raw for c in _by_class(doc, "fact")]
    assert len(_by_class(doc, "identifier")) >= 2


def test_ac135_the_three_new_derived_prefixes_exist() -> None:
    """AC-135：`ttm:` / `extreme:` / `bollinger:` 三类**新增**必须真的产出条目。"""
    snapshot = {
        "asOf": "2026-02-10T01:00:00Z",
        "quote": {"last": 1672.0},
        "technicals": {"bollUpper": 1700.0, "bollLower": 1600.0, "ma60": 1598.4},
        "priceSeries": {"close": [1650.0 + index for index in range(10)]},
        # 真实快照是**季度与年度交错**的（实测 E-119）：`_YOY_LAG = 4` 只有在
        # "9M26 的下四条是 9M25"这种排布下才成立。
        "financials": [
            {"period": "20260930", "revenue": 133.0e9, "netProfit": 64.0e9},
            {"period": "20260630", "revenue": 92.0e9, "netProfit": 44.0e9},
            {"period": "20260331", "revenue": 54.0e9, "netProfit": 27.0e9},
            {"period": "20251231", "revenue": 172.0e9, "netProfit": 82.0e9},
            {"period": "20250930", "revenue": 130.0e9, "netProfit": 63.0e9},
        ],
    }
    derived = nc.derived_values(
        snapshot,
        # `upside` / `scenarioUpside` 读的是**本文档**的两处投影（目标价与情景价），
        # 快照里没有它们（那是模型的判断，属 projection 类），所以必须给一份 doc。
        {
            "rating": {"targetPrice": 1800.0},
            "sections": [
                {
                    "id": "valuation",
                    "title": "估值",
                    "blocks": [
                        {
                            "type": "scenario",
                            "scenarios": [
                                {"name": "bull", "targetPrice": 1900.0, "probability": 0.3, "assumptions": "乐观"},
                                {"name": "base", "targetPrice": 1800.0, "probability": 0.5, "assumptions": "中性"},
                            ],
                        }
                    ],
                }
            ],
        },
    )
    prefixes = {key.split(":")[0] for key in derived}
    assert "ttm" in prefixes, sorted(prefixes)
    assert "extreme" in prefixes, sorted(prefixes)
    assert "bollinger" in prefixes, sorted(prefixes)
    # 防"补 5 类"时手滑删掉已有的三类（V11-P2-5）。
    assert {"distanceMa", "upside", "scenarioUpside"} <= prefixes, sorted(prefixes)


def test_the_ttm_formula_matches_a_hand_calculation() -> None:
    """TTM = 最新累计 + 上年全年 − 上年同期累计（手算一遍，签名与口径一起钉住）。

    实测（E-119）：v12 的 `TTM 营收 1,744.3 亿元` 就是
    `133.28 + 172.05 − 130.90 = 174.43` 十亿元。这条用例把公式本身固化成断言。
    """
    snapshot = {
        "financials": [
            {"period": "20260930", "revenue": 133_283_652_508.95},
            {"period": "20260630", "revenue": 92_278_072_083.21},
            {"period": "20260331", "revenue": 54_702_912_385.23},
            {"period": "20251231", "revenue": 172_054_171_890.91},
            {"period": "20250930", "revenue": 130_903_889_634.88},
        ]
    }
    derived = nc.derived_values(snapshot, {})
    ttm = derived["ttm:revenue@20260930"]
    assert ttm.unit == "亿元"
    assert round(ttm.value, 1) == 1744.3, ttm.value


def test_ttm_needs_all_three_inputs() -> None:
    """目录宁可漏，也不能给一个编造的换算背书：缺"上年全年"就一条都不生成。"""
    snapshot = {
        "financials": [
            {"period": "20260930", "revenue": 133.0e9},
            {"period": "20260630", "revenue": 92.0e9},
            {"period": "20260331", "revenue": 54.0e9},
            {"period": "20250930", "revenue": 130.0e9},
        ]
    }
    assert not [key for key in nc.derived_values(snapshot, {}) if key.startswith("ttm:")]


def test_extreme_uses_the_close_series_not_the_day_high() -> None:
    """前高/前低用**收盘序列**的窗口极值（`quote.dayHigh/dayLow` 只是当日高低）。"""
    snapshot = {
        "quote": {"last": 100.0},
        "priceSeries": {"close": [90.0, 110.0, 95.0]},
    }
    derived = nc.derived_values(snapshot, {})
    # (100 - 110)/110*100 ≈ -9.09%
    assert round(derived["extreme:high60@quote.last"].value, 2) == -9.09
    assert round(derived["level:high60@quote.last"].value, 2) == 110.0


def test_approx_layer_rescues_a_rounding_gap_but_not_a_fabrication() -> None:
    """AC-121 后半句：≤1% 的口径差能被**命名**，编造值不能。

    四道门槛（相对差 ≤1% / ≥3 位有效数字 / 单位一致 / 取最接近）里任何一条不过就继续
    算缺口——`match_derived()` 的半单位容差一个字都没动（R-59）。
    """
    derived = nc.derived_values(
        {
            "quote": {"last": 100.0},
            "technicals": {"ma60": 90.0},  # distanceMa = (90-100)/100*100 = -10.0%
            "performance": {"ret1y": 12.3, "volAnnualized": 25.0},
        },
        {},
    )
    # 口径差 0.5%：模型按盘中价算成 -10.1%
    close_claim = nc.Claim(block_id="b", path="p", raw="-10.1", canonical="-10.1", cls="fact", context="距 MA60 -10.1%")
    assert nc.match_derived(close_claim, derived) is None, "精确层不该认这条（差 1%）"
    assert nc.match_derived_approx(close_claim, derived) is not None, "近似层应当命名它"
    # 编造值（差 30%）两边都不认
    fake = nc.Claim(block_id="b", path="p", raw="-7.0", canonical="-7.0", cls="fact", context="距 MA60 -7.0%")
    assert nc.match_derived(fake, derived) is None
    assert nc.match_derived_approx(fake, derived) is None


def test_approx_layer_requires_three_significant_digits() -> None:
    """一位有效数字（`5` / `9`）不享受 1% 的宽容——那是 R-E8 的原始理由。"""
    derived = {"x": nc.DerivedValue(id="x", value=5.0, unit="倍", note="测试")}
    one_digit = nc.Claim(block_id="b", path="p", raw="5", canonical="5", cls="fact", context="信心度 5 倍")
    assert nc.match_derived_approx(one_digit, derived) is None
    # `_significant_digits("5.02")` = 3；用 `5.0` 会是 2（"50".lstrip("0")），测不到这条判据。
    three_digits = nc.Claim(block_id="b", path="p", raw="5.02", canonical="5.02", cls="fact", context="比值 5.02 倍")
    assert nc.match_derived_approx(three_digits, derived) is not None


def test_approx_layer_requires_a_matching_unit() -> None:
    """单位不一致就不认：`6 个月` 撞上一个 `6.0 倍` 的公式是纯粹的巧合。"""
    derived = {"x": nc.DerivedValue(id="x", value=6.0, unit="倍", note="测试")}
    months = nc.Claim(
        block_id="b", path="p", raw="6.05", canonical="6.05", cls="fact", context="未来 6.05 个月的催化剂"
    )
    assert nc.match_derived_approx(months, derived) is None
    times = nc.Claim(block_id="b", path="p", raw="6.05", canonical="6.05", cls="fact", context="降幅 6.05 倍")
    assert nc.match_derived_approx(times, derived) is not None


def test_approx_claims_are_counted_separately() -> None:
    """`approxClaims` 与 `derivedClaims` 分开计数：界面上这两句话的硬程度不同。"""
    payload = nc.GroundingReport().as_payload()
    assert "approxClaims" in payload
    assert payload["approxClaims"] == 0
    assert payload["derivedClaims"] == 0


def test_a_one_decimal_rounding_is_not_a_fabrication() -> None:
    """★v12 落地修正（I-118）：`-0.2`（一位有效数字但**带小数**）不该被 0.1% 门槛挡掉。

    实测 v12 的 `营业收入 ... 归母 -0.2%` 正是 `yoy:financials[0].netProfit = -0.2465`
    保留一位小数的样子。半单位容差 `[-0.25, -0.15)` 已经是**定义明确**的舍入区间，
    再叠一个 0.1% 的相对门槛就把一个诚实的舍入判成了编造。
    那条门槛的原意只针对**整数**的一位数（`3` / `5` / `9`，它们多是计数与档位）。
    """
    derived = {"yoy:x": nc.DerivedValue(id="yoy:x", value=-0.2465, unit="%", note="同比")}
    one_decimal = nc.Claim(block_id="b", path="p", raw="-0.2", canonical="-0.2", cls="fact", context="归母 -0.2%")
    assert nc.match_derived(one_decimal, derived) is not None, "带小数的一位数是确定的舍入"
    integer_claim = nc.Claim(block_id="b", path="p", raw="3", canonical="3", cls="fact", context="信心度 3")
    # 相对差 = 0.004/3.004 ≈ 0.13% > 0.1% 的门槛 ⇒ 必须被拒（3.002 只差 0.07%，测不出来）。
    integer_derived = {"y": nc.DerivedValue(id="y", value=3.004, unit="倍", note="测试")}
    assert nc.match_derived(integer_claim, integer_derived) is None, "整数的一位数仍要 0.1% 的相对门槛"
