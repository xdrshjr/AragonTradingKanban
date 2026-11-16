"""Report document validation, repair and grounding (spec §6.2, AC-12)."""

from __future__ import annotations

import copy

from app.services import report_validator as validator

GROUNDED_THRESHOLD = validator.GROUNDED_THRESHOLD


def _doc(sample: dict) -> dict:
    return copy.deepcopy(sample)


def test_valid_sample_has_no_structural_errors(sample_report: dict, snapshot_payload: dict) -> None:
    """Schema-wise the fixture is perfect; grounding is judged separately below."""
    outcome = validator.validate_doc(_doc(sample_report), snapshot_payload)
    assert outcome.errors == []
    assert 0.0 <= outcome.grounded_ratio <= 1.0


def test_missing_section_is_reported_and_repaired(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["sections"] = [section for section in doc["sections"] if section["id"] != "valuation"]
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert "valuation" in outcome.missing_sections
    assert any(section["id"] == "valuation" for section in outcome.doc["sections"])
    assert outcome.status == "degraded"
    assert any("valuation" in repair for repair in outcome.repairs)


def test_unknown_block_is_pruned_not_fatal(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["sections"][0]["blocks"].append({"type": "hologram", "text": "来自未来"})
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.pruned_blocks == 1
    assert outcome.errors == []
    assert all(block["type"] != "hologram" for block in outcome.doc["sections"][0]["blocks"])


def test_missing_key_metrics_are_filled_from_snapshot(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["keyMetrics"] = []
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["keyMetrics"], "metrics should be derived from the snapshot"
    assert any("keyMetrics" in repair for repair in outcome.repairs)


def test_fabricated_number_lowers_grounding_and_degrades(sample_report: dict, snapshot_payload: dict) -> None:
    """AC-12: a number that is nowhere in the snapshot must not pass silently."""
    doc = _doc(sample_report)
    doc["sections"][0]["blocks"].append(
        {
            "type": "paragraph",
            "text": "公司营收同比增长 987654.3%，毛利率达到 87654.2%，远超所有同业。",
        }
    )
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.grounded_ratio < GROUNDED_THRESHOLD
    assert outcome.ungrounded_places >= 2
    assert outcome.status == "degraded"


def test_a_document_built_from_the_snapshot_is_fully_grounded(snapshot_payload: dict) -> None:
    """The rule-based document can only use snapshot numbers, so it must score ~1.0."""
    from app.agent.mock_runner import build_mock_document

    doc = build_mock_document(snapshot_payload)
    outcome = validator.validate_doc(doc, snapshot_payload, mock=True)
    assert outcome.errors == []
    assert outcome.grounded_ratio >= GROUNDED_THRESHOLD, outcome.ungrounded_places
    assert outcome.status == "ready"


def test_numbers_taken_verbatim_from_the_snapshot_are_grounded(snapshot_payload: dict) -> None:
    doc = validator.build_provisional_doc(
        snapshot_payload, raw_text="占位", error_code="internal", error_message="占位"
    )
    doc["sections"][0]["blocks"][1]["text"] = "现价 1672.00 元，近一年收益 12.3%，最大回撤 −18.2%，成交 3120000 股。"
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.ungrounded_places == 0, "verbatim snapshot numbers must all match"


def test_identifier_digits_are_not_treated_as_measurements(sample_report: dict, snapshot_payload: dict) -> None:
    """`RSI(14)` and `MA20` are method names, not data points."""
    assert validator._numbers_in_text("RSI(14) 为 56.2，价格在 MA20 上方") == ["56.2"]


def test_invalid_rating_is_repaired(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["rating"] = {"stance": "moon", "conviction": 99, "horizon": ""}
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["rating"]["stance"] == "watch"
    assert outcome.doc["rating"]["conviction"] == 3
    assert any("stance" in repair for repair in outcome.repairs)


def test_missing_sources_are_backfilled_from_snapshot(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["sources"] = []
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["sources"], "snapshot sources should be used as the floor"
    assert any("sources" in repair for repair in outcome.repairs)


def test_structurally_broken_document_reports_errors(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["sections"] = [{"id": "thesis", "title": "核心逻辑", "blocks": [{"type": "paragraph", "text": ""}]}]
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert outcome.errors, "an empty paragraph must fail minLength"
    assert outcome.status == "degraded"


def test_provisional_document_keeps_its_status(snapshot_payload: dict) -> None:
    doc = validator.build_provisional_doc(
        snapshot_payload,
        raw_text="引擎返回了内容但不合结构。",
        error_code="result_schema_unsatisfied",
        error_message="structured was null",
    )
    outcome = validator.validate_doc(doc, snapshot_payload, provisional=True)
    assert outcome.status == "provisional"
    assert outcome.doc["provisional"] is True


def test_mock_documents_are_not_graded_on_grounding(sample_report: dict, snapshot_payload: dict) -> None:
    """A rule-built document has no model prose to fabricate anything (§16.6)."""
    doc = _doc(sample_report)
    doc["sections"][0]["blocks"].append({"type": "paragraph", "text": "假设增长 123456.7%。"})
    strict = validator.validate_doc(copy.deepcopy(doc), snapshot_payload)
    relaxed = validator.validate_doc(copy.deepcopy(doc), snapshot_payload, mock=True)
    assert strict.status == "degraded"
    assert relaxed.status == "ready"


def test_quality_payload_shape(sample_report: dict, snapshot_payload: dict) -> None:
    outcome = validator.validate_doc(_doc(sample_report), snapshot_payload)
    quality = outcome.quality
    # `ungroundedCount` is what lets the UI say "N 处数字未能在快照中找到来源"
    # (§16 verdict 4) — it is part of the stored record, not a UI-only guess.
    #
    # ★v6：本用例按 §19.3.2 扩了一个键（`grounding`）。这是**唯一**允许改既有测试的
    # 理由：形状本身变了（只加字段，v2 的五个键一个不少），审核记录见 spec.md
    # 「实施过程发现的方案缺陷」。`engine` 不在这里出现，因为它由调用方注入——
    # 没有指纹时该键必须缺席，而不是伪造一份（O-13）。
    assert set(quality) == {
        "groundedRatio",
        "missingSections",
        "prunedBlocks",
        "ungroundedCount",
        "source",
        "grounding",
    }
    assert 0.0 <= quality["groundedRatio"] <= 1.0
    assert quality["ungroundedCount"] == outcome.ungrounded_places
    assert quality["source"] == "aragon"
    # AC-61：两个字段是同一条事实，不是两次计算。
    assert quality["ungroundedCount"] == quality["grounding"]["factMissing"]
    assert validator.validate_doc(_doc(sample_report), snapshot_payload, mock=True).quality["source"] == "mock"


def test_sections_are_forced_into_canonical_order(sample_report: dict, snapshot_payload: dict) -> None:
    doc = _doc(sample_report)
    doc["sections"] = list(reversed(doc["sections"]))
    outcome = validator.validate_doc(doc, snapshot_payload)
    assert [section["id"] for section in outcome.doc["sections"]] == [
        "thesis",
        "fundamentals",
        "valuation",
        "risks",
        "catalysts",
    ]
