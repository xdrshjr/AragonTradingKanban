"""Regression tests: snapshot-owned fields must not be paraphrased by the model.

Both cases were found by screenshotting a real generation:

* `provisional: true` written by the model into a good report (labelled 临时版).
* `asOf: "2026-11-16T07:31:04Z (Asia/Shanghai)"` — the header rendered 数据时点 —.
"""

from __future__ import annotations

import copy

from app.services import report_validator


def test_model_supplied_provisional_flag_is_reset(sample_report: dict, snapshot_payload: dict) -> None:
    doc = copy.deepcopy(sample_report)
    doc["provisional"] = True

    outcome = report_validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["provisional"] is False
    assert any("provisional" in repair for repair in outcome.repairs)
    assert outcome.status != "provisional"


def test_pipeline_sets_provisional_only_for_the_fallback_document(snapshot_payload: dict) -> None:
    doc = report_validator.build_provisional_doc(
        snapshot_payload, raw_text="失败产出", error_code="internal", error_message="boom"
    )
    outcome = report_validator.validate_doc(doc, snapshot_payload, provisional=True)
    assert outcome.doc["provisional"] is True
    assert outcome.status == "provisional"


def test_absent_provisional_flag_stays_false(sample_report: dict, snapshot_payload: dict) -> None:
    """A missing flag is the normal case and must not be reported as a repair."""
    doc = copy.deepcopy(sample_report)
    doc.pop("provisional", None)
    outcome = report_validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["provisional"] is False
    assert outcome.status != "provisional"


def test_as_of_with_a_timezone_suffix_is_corrected(sample_report: dict, snapshot_payload: dict) -> None:
    doc = copy.deepcopy(sample_report)
    doc["asOf"] = "2026-11-16T07:31:04Z (Asia/Shanghai)"

    outcome = report_validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["asOf"] == snapshot_payload["asOf"]
    assert any("asOf" in repair for repair in outcome.repairs)


def test_as_of_always_comes_from_the_snapshot(sample_report: dict, snapshot_payload: dict) -> None:
    doc = copy.deepcopy(sample_report)
    doc["asOf"] = "2020-01-01T00:00:00Z"
    outcome = report_validator.validate_doc(doc, snapshot_payload)
    assert outcome.doc["asOf"] == snapshot_payload["asOf"]


def test_as_of_is_left_empty_when_the_snapshot_has_none(sample_report: dict) -> None:
    doc = copy.deepcopy(sample_report)
    doc["asOf"] = ""
    outcome = report_validator.validate_doc(doc, {})
    assert outcome.doc["asOf"] == ""
