"""数字分类与接地报告（spec §19.3.2，AC-46 ~ AC-48 / AC-60 / AC-61）。

分类表的每一行在这里都有独立用例：这是本轮唯一改**正确性**的改动，靠"整体比例看起来
还行"是验不出来的。特别地，AC-60 把本机实测的失败（`'2025-03-14'` 被拆成三个必然
失配的 fact claim，证据 E-29）固化成回归，AC-61 用**方向性**断言证明顶层
`keyMetrics` 真的在分母里（只比两个字段相等，它们也可能一起算错）。
"""

from __future__ import annotations

import copy
import json

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
    doc = _doc({"type": "callout", "tone": "insight", "title": "关键结论 3 条", "text": "目标价 1800 元。"})
    assert [claim.raw for claim in _by_class(doc, "fact")] == ["3", "1800"]


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
    assert [claim.raw for claim in _by_class(doc, "identifier")] == ["1"]


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
