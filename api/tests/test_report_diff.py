"""AC-34 / AC-42 —— 版本比较的结构 diff（§18.3.5）与语义色（§18.5.4）。

`services/report_diff.py` 是纯函数（无 IO），所以主体用极简文档直接喂给它；
只有"同版本 422"这一条契约属于 HTTP 层，走 `client` fixture 断言错误码 `diff_same_report`。

三条不可退让的规则：

* **结构比较，不做文本 LCS**：章节按 `id`、块按 `(type, 该类型内序号)`、指标按 `label`；
  图表只比形状（kind / series 数量），数据点随行情变，是噪声。
* **语义色只由服务端算**（C-9）：`tone = direction × polarity`。"PE 上升是坏事"是领域知识，
  前端不该再猜一次。
* **AC-42：没有 `polarity` 就一律 neutral**——"猜错颜色"比"没有颜色"更伤可信度。
"""

from __future__ import annotations

import pytest

from app.services.report_diff import diff_docs, parse_number, tone_for

#: ★V4-P1-1 / §18.5.4 的示例指标：毛利率是 bullish（跌是坏事），PE 是 bearish（涨是坏事）。
MARGIN = "毛利率"
PE = "PE(TTM)"
YIELD = "股息率"

#: 回归备注（缺陷已修）：`report_diff._block_hash()` 曾经忘了模块前缀而抛 `NameError`，
#: 任何含非 metrics 配对块的 diff 都会 500。修复后下面两条断言就是普通断言——它们
#: 的存在本身就是防回归的锚点（E-8 的教训：文档说「已存在」必须有机器判据）。


def metrics_doc(items: list[dict], *, section_id: str = "business", title: str = "基本面") -> dict:
    """只含一个 `metrics` 块的极简文档：指标路径与块配对都能直接对齐。"""
    return {"sections": [{"id": section_id, "title": title, "blocks": [{"type": "metrics", "items": items}]}]}


def chart_doc(block: dict, *, section_id: str = "business") -> dict:
    return {"sections": [{"id": section_id, "title": "基本面", "blocks": [block]}]}


def chart(*, kind: str = "line", title: str = "近 8 个季度营业收入同比", series: list[dict] | None = None) -> dict:
    return {
        "type": "chart",
        "chart": {
            "kind": kind,
            "title": title,
            "series": series if series is not None else [{"name": "营收同比", "data": [17.4, 10.8, 8.2, 12.4]}],
            "unit": "%",
        },
    }


def deltas_by_label(a: dict, b: dict) -> dict[str, dict]:
    return {delta["label"]: delta for delta in diff_docs(a, b)["metricsDelta"]}


# ── 同版本：没有结构差异（HTTP 层另见下面的 422 用例） ────────────────────────


def test_diff_of_the_same_document_is_flat_and_neutral() -> None:
    """AC-34：同一份文档比它自己——指标全 flat/neutral，没有块/章节/评级差异。"""
    doc = metrics_doc(
        [
            {"label": MARGIN, "value": "65.5%", "polarity": "bullish"},
            {"label": PE, "value": "28.1×", "polarity": "bearish"},
            {"label": "代码", "value": "—"},
        ]
    )
    result = diff_docs(doc, doc)

    assert result["metricsDelta"], "metrics 块里的每个指标都要出现在 metricsDelta 里"
    for delta in result["metricsDelta"]:
        assert delta["direction"] == "flat"
        assert delta["tone"] == "neutral", "没有变化就不该有颜色，哪怕 polarity 是 bullish"
        assert delta["deltaPct"] in (0.0, None)

    assert result["blocksChanged"] == []
    assert result["sectionsAdded"] == []
    assert result["sectionsRemoved"] == []
    assert result["ratingChanged"] is None

    # 解析不出来的值（"—"）必须落 None，而不是被当成 0.0 编一个 delta。
    unparsable = next(delta for delta in result["metricsDelta"] if delta["label"] == "代码")
    assert unparsable["before"] is None
    assert unparsable["after"] is None
    assert unparsable["deltaPct"] is None


def test_diff_of_the_sample_report_with_itself_is_empty(sample_report: dict) -> None:
    """AC-34：拿真实夹具（12 种块全在）比它自己，`blocksChanged` 必须为空。"""
    result = diff_docs(sample_report, sample_report)

    assert result["blocksChanged"] == []
    assert result["sectionsAdded"] == []
    assert result["sectionsRemoved"] == []
    assert result["ratingChanged"] is None
    for delta in result["metricsDelta"]:
        assert delta["direction"] == "flat"
        assert delta["tone"] == "neutral"
        assert delta["deltaPct"] in (0.0, None)


def test_diff_of_an_empty_document_says_so() -> None:
    """没有任何指标/章节变化时，摘要不许留空——读者需要一个句子。"""
    result = diff_docs({"sections": []}, {"sections": []})
    assert result["metricsDelta"] == []
    assert result["blocksChanged"] == []
    assert result["summary"] == "两个版本没有结构性差异"


def test_diff_endpoint_rejects_comparing_a_report_with_itself(
    client, session, instrument, sample_report: dict
) -> None:
    """AC-34（§18.5.4）：HTTP 层 `a == b` 是 422 `diff_same_report`，不是空 diff、更不是 500。"""
    from app.services import report_service

    report = report_service.create_version(
        session, instrument, sample_report, status="ready", quality={"groundedRatio": 1.0}
    )
    response = client.get(f"/api/v1/reports/{report.id}/diff/{report.id}")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}, "错误只走 §7.1 的统一信封"
    assert body["error"]["code"] == "diff_same_report"
    assert {"code", "message", "requestId", "detail"} <= set(body["error"])


# ── 指标 delta：数值、方向、语义色（§18.5.4 示例） ────────────────────────────


def test_metrics_delta_reports_delta_pct_and_direction() -> None:
    """AC-34：`deltaPct` 与 `direction` 由前后两版的值算出来（毛利率 -5.19%、PE +11.74%）。"""
    before = metrics_doc(
        [
            {"label": MARGIN, "value": "65.5%", "polarity": "bullish"},
            {"label": PE, "value": "28.1×", "polarity": "bearish"},
        ]
    )
    after = metrics_doc(
        [
            {"label": MARGIN, "value": "62.1%", "polarity": "bullish"},
            {"label": PE, "value": "31.4×", "polarity": "bearish"},
        ]
    )
    deltas = deltas_by_label(before, after)

    margin = deltas[MARGIN]
    assert margin["before"] == pytest.approx(65.5)
    assert margin["after"] == pytest.approx(62.1)
    assert margin["deltaPct"] == pytest.approx(-5.19, abs=0.01)
    assert margin["direction"] == "down"
    assert margin["polarity"] == "bullish"
    assert margin["tone"] == "down"

    pe = deltas[PE]
    assert pe["before"] == pytest.approx(28.1)
    assert pe["after"] == pytest.approx(31.4)
    assert pe["deltaPct"] == pytest.approx(11.74, abs=0.01)
    assert pe["direction"] == "up"
    assert pe["polarity"] == "bearish"
    assert pe["tone"] == "down"

    # 语义色不是由 deltaPct 的正负号决定的：两个方向相反的指标得到同一个 tone。
    assert margin["tone"] == pe["tone"] == "down"

    result = diff_docs(before, after)
    assert result["blocksChanged"] == [], "只有数值变了、标签集合没变，块本身不算 changed"
    assert f"{MARGIN} -5.2%" in result["summary"]
    assert f"{PE} +11.7%" in result["summary"]


def test_polarity_decides_the_colour_not_the_sign() -> None:
    """同一组数字，polarity 一换 colour 就换：bullish 涨 = up，bearish 涨 = down。"""
    before = metrics_doc([{"label": "营收", "value": "100", "polarity": "bullish"}])
    after = metrics_doc([{"label": "营收", "value": "115", "polarity": "bullish"}])
    bullish_up = deltas_by_label(before, after)["营收"]
    assert bullish_up["direction"] == "up" and bullish_up["tone"] == "up"

    before["sections"][0]["blocks"][0]["items"][0]["polarity"] = "bearish"
    after["sections"][0]["blocks"][0]["items"][0]["polarity"] = "bearish"
    bearish_up = deltas_by_label(before, after)["营收"]
    assert bearish_up["direction"] == "up" and bearish_up["tone"] == "down"


# ── AC-42：缺省 polarity 一律 neutral，绝不猜 ────────────────────────────────


def test_missing_polarity_never_guesses_a_tone() -> None:
    """AC-42：没有 polarity（字段缺失 / None / 显式 neutral）的指标 tone 必须 neutral，方向照旧算。"""
    before = metrics_doc(
        [
            {"label": MARGIN, "value": "65.5%"},  # 字段缺失
            {"label": PE, "value": "28.1×"},  # 字段缺失
            {"label": YIELD, "value": "2.1%", "polarity": None},  # 显式 null
            {"label": "PB", "value": "7.2×", "polarity": "neutral"},
        ]
    )
    after = metrics_doc(
        [
            {"label": MARGIN, "value": "62.1%"},
            {"label": PE, "value": "31.4×"},
            {"label": YIELD, "value": "2.4%", "polarity": None},
            {"label": "PB", "value": "8.0×", "polarity": "neutral"},
        ]
    )
    deltas = deltas_by_label(before, after)

    expected_directions = {MARGIN: "down", PE: "up", YIELD: "up", "PB": "up"}
    for label, direction in expected_directions.items():
        delta = deltas[label]
        assert delta["direction"] == direction, label
        assert delta["polarity"] == "neutral", f"{label} 的缺省 polarity 应当是 neutral"
        assert delta["tone"] == "neutral", f"{label} 在缺省 polarity 下被猜了颜色：{delta['tone']}"


@pytest.mark.parametrize(
    ("direction", "polarity", "expected"),
    [
        ("up", "bullish", "up"),
        ("up", "bearish", "down"),
        ("down", "bullish", "down"),
        ("down", "bearish", "up"),
        ("flat", "bullish", "neutral"),  # 没有变化就没有颜色
        ("flat", "bearish", "neutral"),
        ("up", None, "neutral"),  # AC-42：未知 polarity 不猜
        ("down", None, "neutral"),
        ("up", "neutral", "neutral"),
        ("down", "neutral", "neutral"),
        ("up", "sideways", "neutral"),  # 非法取值也只落 neutral，不抛异常
    ],
)
def test_tone_for_is_the_only_semantic_colour_table(direction: str, polarity, expected: str) -> None:
    """`tone_for` 是全链路唯一的颜色表（C-9）：四种组合 + flat + 未知一律 neutral。"""
    assert tone_for(direction, polarity) == expected


# ── 章节增删 ────────────────────────────────────────────────────────────────


def test_added_and_removed_sections_are_reported_by_title() -> None:
    """AC-34：只在 b 里有的章节进 `sectionsAdded`，只在 a 里有的进 `sectionsRemoved`（按标题）。"""
    a = {
        "sections": [
            {"id": "business", "title": "业务", "blocks": []},
            {"id": "risk", "title": "风险", "blocks": []},
        ]
    }
    b = {
        "sections": [
            {"id": "business", "title": "业务", "blocks": []},
            {"id": "catalysts", "title": "催化剂", "blocks": []},
        ]
    }
    result = diff_docs(a, b)

    assert result["sectionsAdded"] == ["催化剂"]
    assert result["sectionsRemoved"] == ["风险"]
    assert "催化剂" in result["summary"] and "风险" in result["summary"]


def test_section_alignment_uses_ids_not_positions() -> None:
    """章节对齐键是 `id`：只是顺序变了，既不算新增也不算移除。"""
    forward = {
        "sections": [
            {"id": "business", "title": "业务", "blocks": []},
            {"id": "risk", "title": "风险", "blocks": []},
        ]
    }
    reversed_ = {"sections": list(reversed(forward["sections"]))}
    result = diff_docs(forward, reversed_)
    assert result["sectionsAdded"] == []
    assert result["sectionsRemoved"] == []


# ── 图表只比形状 ────────────────────────────────────────────────────────────


def test_chart_projection_ignores_data_points() -> None:
    """§18.3.5：图表的数据点随行情变，是噪声——文本投影里只有标题/series 名/备注。"""
    from app.services import block_text

    same_shape = chart(series=[{"name": "营收同比", "data": [1.0, 2.0, 3.0]}])
    other_points = chart(series=[{"name": "营收同比", "data": [99.0, 1.0, 0.5]}])
    assert block_text.block_to_text(same_shape) == block_text.block_to_text(other_points)


def test_chart_blocks_with_different_data_points_are_not_changed() -> None:
    """AC-34：同 kind、同 series 数量、只有数据点不同 → 不得出现在 `blocksChanged` 里。"""
    before = chart_doc(chart(series=[{"name": "营收同比", "data": [17.4, 10.8, 8.2, 12.4]}]))
    after = chart_doc(chart(series=[{"name": "营收同比", "data": [9.9, 1.2, 3.3, 5.5]}]))
    assert diff_docs(before, after)["blocksChanged"] == []


# ── 数值解析 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("62.1%", 62.1),
        ("24.1×", 24.1),
        ("—", None),
        (3, 3.0),
        ("+15.2%", 15.2),
        ("-3.4pct", -3.4),
        ("1,234.6 亿", 1234.6),
        ("", None),
        ("n/a", None),
        ("-0.5", -0.5),
        (True, None),  # bool 不是数值：不许把 True 当成 1.0
        (None, None),
        ({"value": 1}, None),
    ],
)
def test_parse_number(raw, expected) -> None:
    """§18.3.5：能读就给出 float，读不出来就返回 None（调用方渲染 "—"，不编造 delta）。"""
    parsed = parse_number(raw)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == pytest.approx(expected)


# ── statusChanged 只报「质量」变化，不报 supersede ──────────────────────────


def test_adjacent_versions_do_not_report_a_fake_status_change(
    client, session, instrument, sample_report: dict
) -> None:
    """`superseded` 只等于「有更新版本」，每一对相邻版本都成立。

    Diffing it verbatim made `statusChanged` non-null on every comparison — and
    therefore meaningless (§18.5.4 only ever illustrates `ready → degraded`).
    """
    from app.services import report_service

    first = report_service.create_version(session, instrument, sample_report, status="ready")
    second = report_service.create_version(session, instrument, sample_report, status="ready")

    response = client.get(f"/api/v1/reports/{first.id}/diff/{second.id}")
    assert response.status_code == 200
    body = response.json()
    assert first.status == "superseded", "the incumbent is marked superseded by design"
    assert body["statusChanged"] is None

    # 真正的质量变化仍然要报出来。
    third = report_service.create_version(
        session, instrument, sample_report, status="degraded", quality={"groundedRatio": 0.4}
    )
    changed = client.get(f"/api/v1/reports/{second.id}/diff/{third.id}")
    assert changed.status_code == 200
    assert changed.json()["statusChanged"] == {"from": "ready", "to": "degraded"}
