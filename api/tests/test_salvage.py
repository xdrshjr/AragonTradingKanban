"""K5 解析抢救：七步阶梯、两个计数器不重叠、抢不出来就 None（AC-102 / AC-103 / **AC-109**）。

这一层守的是 §21.0.1 的 Q-4「丢得少」：模型给了一份 90% 合规的报告时，用户不该只看到
"这不是一份可用的投研报告"。但抢救**必须可见**（裁决 K-11）——一份"少了两块"的报告被
当成完整报告，会让用户拿它去做投资判断，那正是 v5 §19 立下"只标不改数"的同一个理由。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema

from app.schemas.report import ReportDoc
from app.services.report_validator import (
    BLOCK_REQUIRED,
    build_provisional_doc,
    salvage_doc,
    validate_doc,
)

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample_report.json"

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas" / "report_doc.schema.json").read_text(
        encoding="utf-8"
    )
)


def snapshot() -> dict:
    return {
        "instrument": {"symbol": "600519.SH", "name": "贵州茅台", "market": "CN", "currency": "CNY"},
        "asOf": "2026-02-10T01:00:00Z",
        "quote": {"last": 1672.0},
        "performance": {"ret1y": 12.3},
        "sources": [{"id": "S1", "title": "行情快照", "publisher": "akshare", "publishedAt": "2026-02-10"}],
    }


def valid_doc() -> dict:
    """一份**已知合规**的文档：直接读假 CLI 的成功样本（`fake_aragon` 的 `ok` 剧本）。

    手写一份"看起来对"的文档是这一层最容易犯的错：`risks` 少一个 `trigger` 就会
    被 schema 拒收，于是基线一红，后面每一条断言都在测"必然为 None"的那条路径。
    """
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_the_baseline_document_is_already_valid() -> None:
    jsonschema.validate(valid_doc(), SCHEMA)
    ReportDoc.model_validate(valid_doc())


# ── AC-102 / O-20 / AC-109：O-20 的文档 ─────────────────────────────────────


def messy_doc() -> dict:
    """O-20：`+1 多余顶层键 + 1 个 type:"donut" 块 + 1 个缺 text 的 paragraph`。"""
    doc = valid_doc()
    doc["internalNotes"] = "模型自己加的键"  # ← droppedKeys = 1（schema 顶层 additionalProperties:false）
    doc["sections"][0]["blocks"].append({"type": "donut", "rings": [1, 2, 3]})  # ← 未知块
    doc["sections"][0]["blocks"].append({"type": "paragraph"})  # ← 缺 text
    return doc


def test_salvage_keeps_the_report_and_counts_only_its_own_losses() -> None:
    """AC-102：`droppedKeys=1`、`droppedBlocks=1`、`prunedBlocks=1`、status='degraded'。

    计数分工（V10-P1-3）：未知块归**既有**的 `quality.prunedBlocks`，salvage 只数
    自己丢的那一个（合法 type 但字段不全）。两个计数器**不重叠**（AC-109）。
    """
    doc, report = salvage_doc(messy_doc(), snapshot())

    assert doc is not None, "90% 合规的文档必须被救回来"
    assert report.used is True
    assert report.droppedKeys == 1
    assert report.droppedSections == 0
    assert report.droppedBlocks == 1
    assert report.total == 2

    jsonschema.validate(doc, SCHEMA)
    ReportDoc.model_validate(doc)

    outcome = validate_doc(
        doc, snapshot(), salvage=report, schema_path=None, pruned_blocks=report.prunedBlocks
    )
    assert outcome.status == "degraded", "抢救过的报告永远是 degraded（裁决 K-11）"
    # 未知块在这一步被既有逻辑裁掉，计入 prunedBlocks —— 与 salvage 的计数器不重叠。
    assert outcome.pruned_blocks == 1
    assert outcome.quality["salvage"]["used"] is True
    assert outcome.quality["salvage"]["droppedBlocks"] == 1
    assert outcome.pruned_blocks + outcome.quality["salvage"]["droppedBlocks"] == 2, "AC-109"


def test_salvage_reports_no_loss_when_the_document_was_already_clean() -> None:
    doc, report = salvage_doc(valid_doc(), snapshot())
    assert doc is not None
    assert report.used is False
    assert report.total == 0


def test_unknown_block_type_is_not_counted_by_salvage() -> None:
    """分工写死：未知 `type` 交给 `prune_unknown_blocks()`，salvage 不数它。"""
    doc = valid_doc()
    doc["sections"][0]["blocks"] = [{"type": "donut"}]
    salvaged, report = salvage_doc(doc, snapshot())
    assert salvaged is not None
    assert report.droppedBlocks == 0
    assert report.used is False


# ── 逐类必填项 ──────────────────────────────────────────────────────────────


def test_each_block_kind_is_judged_by_its_own_required_fields() -> None:
    doc = valid_doc()
    doc["sections"][0]["blocks"] = [
        {"type": "table", "columns": [{"key": "a", "label": "A"}]},  # 缺 rows
        {"type": "bullets"},  # 缺 items
    ]
    salvaged, report = salvage_doc(doc, snapshot())
    assert salvaged is not None
    assert report.droppedBlocks == 2, "table 缺 rows、bullets 缺 items 各丢一块"


def test_block_required_matches_the_schema() -> None:
    """★评审修订（I-101）：必填项表是**数据**，它必须与 schema 的 `required` 同步。

    `salvage_doc()` 只用 `BLOCK_REQUIRED` 判断"这一块救不回来吗"。表里少一个字段的
    后果不是"多留一块"，而是那一条 `jsonschema.validate` **整份失败** ⇒ 返回 `None`
    ⇒ 用户拿到的还是那张"这不是一份可用的投研报告"的卡片。所以这张表要有一条
    反射守卫：schema 里每一类块的 `required`（去掉 `type`）必须与表里的一字不差。
    """
    schema_required: dict[str, tuple[str, ...]] = {}
    for branch in SCHEMA["$defs"]["block"]["anyOf"]:
        kind = branch["properties"]["type"]["enum"][0]
        schema_required[kind] = tuple(field for field in branch["required"] if field != "type")

    assert set(BLOCK_REQUIRED) == set(schema_required), "块类型的集合不一致"
    for kind, fields in schema_required.items():
        assert set(BLOCK_REQUIRED[kind]) == set(fields), f"{kind} 的必填项与 schema 不一致：{fields}"


def test_a_section_without_an_id_is_dropped_and_counted() -> None:
    doc = valid_doc()
    doc["sections"].append({"title": "无 id 的节", "blocks": []})
    salvaged, report = salvage_doc(doc, snapshot())
    assert salvaged is not None
    assert report.droppedSections == 1


def test_rating_is_clamped_instead_of_dropping_the_report() -> None:
    doc = valid_doc()
    doc["rating"] = {"stance": "YOLO", "conviction": 99}
    salvaged, report = salvage_doc(doc, snapshot())
    assert salvaged is not None
    assert salvaged["rating"]["stance"] == "watch"
    assert 1 <= salvaged["rating"]["conviction"] <= 5


def test_metrics_without_a_label_are_dropped() -> None:
    doc = valid_doc()
    doc["keyMetrics"] = [{"value": "1.0"}, {"label": "OK", "value": 2.5}]
    salvaged, report = salvage_doc(doc, snapshot())
    assert salvaged is not None
    assert [item["label"] for item in salvaged["keyMetrics"]] == ["OK"]


# ── AC-103：抢不出来就 None，且库里没有半份非法文档 ─────────────────────────


def test_unsalvageable_input_returns_none() -> None:
    salvaged, report = salvage_doc({"sections": "not-a-list"}, snapshot())
    assert salvaged is None or isinstance(salvaged, dict)
    if salvaged is None:
        assert report.used is False
        assert report.reasons


def test_a_non_dict_input_is_never_salvaged() -> None:
    for bad in ("plain text", 42, ["list"], None):
        salvaged, report = salvage_doc(bad, snapshot())
        assert salvaged is None
        assert report.used is False
        assert "unsalvageable" in report.reasons


def test_salvage_output_always_passes_validation_or_is_none() -> None:
    """AC-103：`validate_doc` 复核 —— 绝不允许"半份不合法文档"被存下来。"""
    doc, report = salvage_doc(messy_doc(), snapshot())
    assert doc is not None
    outcome = validate_doc(copy.deepcopy(doc), snapshot(), salvage=report)
    assert outcome.errors == []
    assert outcome.status == "degraded"


# ── 与 L2（provisional）的分界 ─────────────────────────────────────────────


def test_provisional_document_is_still_the_last_resort() -> None:
    doc = build_provisional_doc(
        snapshot(),
        raw_text="模型最后说的一段话",
        error_code="result_schema_unsatisfied",
        error_message="缺 paragraphs",
    )
    jsonschema.validate(doc, SCHEMA)
    assert doc["provisional"] is True
