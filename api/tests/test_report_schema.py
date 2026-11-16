"""AC-24 — the schema itself: size, depth, and that every block type validates."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from app.agent.schemas import load_schema_dict
from app.schemas.report import BLOCK_TYPES
from app.services import report_validator

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "agent" / "schemas" / "report_doc.schema.json"

#: `aragon info --json` -> `result` limits, verified on CLI 0.6.68.
MAX_SCHEMA_BYTES = 65536
MAX_SCHEMA_DEPTH = 12
MAX_VALIDATION_NODES = 5000


def schema_depth(value, depth: int = 1) -> int:
    """Mirror of the CLI's `schemaDepth()` — raw document nesting, scalar = 1."""
    if isinstance(value, list):
        deepest = depth
        for item in value:
            deepest = max(deepest, schema_depth(item, depth + 1))
        return deepest
    if not isinstance(value, dict):
        return depth
    deepest = depth
    for key in value:
        deepest = max(deepest, schema_depth(value[key], depth + 1))
    return deepest


def count_nodes(value) -> int:
    """Approximate the compiled validation-node count: one per schema keyword."""
    if isinstance(value, dict):
        return 1 + sum(count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_nodes(item) for item in value)
    return 1


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_is_smaller_than_60kb() -> None:
    size = SCHEMA_PATH.stat().st_size
    assert size <= 60_000, f"schema is {size} bytes; the guard is 60000 (§8.3)"
    assert size <= MAX_SCHEMA_BYTES


def test_schema_depth_is_within_the_cli_limit(schema: dict) -> None:
    """A 13-deep schema is rejected outright by the real CLI (found by C-2)."""
    depth = schema_depth(schema)
    assert depth <= MAX_SCHEMA_DEPTH, f"schema nests {depth} levels; the CLI limit is {MAX_SCHEMA_DEPTH}"


def test_validation_node_count_is_within_the_limit(schema: dict) -> None:
    nodes = count_nodes(schema)
    assert nodes < MAX_VALIDATION_NODES, f"{nodes} validation nodes exceeds {MAX_VALIDATION_NODES}"


def test_schema_declares_all_twelve_block_types(schema: dict) -> None:
    variants = schema["$defs"]["block"]["anyOf"]
    declared = {variant["properties"]["type"]["enum"][0] for variant in variants}
    assert declared == set(BLOCK_TYPES), f"missing from schema: {set(BLOCK_TYPES) - declared}"


def test_schema_uses_no_pattern_or_format() -> None:
    """§8.3: no `pattern`/`format` — those degrade silently without ajv."""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    assert '"pattern"' not in text
    assert '"format"' not in text


def test_every_object_forbids_additional_properties(schema: dict) -> None:
    """`additionalProperties: false` is what stops the model from free-styling."""

    def walk(node, path="root"):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False, f"{path} allows extra properties"
        for key, value in node.items():
            walk(value, f"{path}.{key}")
        if isinstance(node, list):
            return

    walk(schema)


def test_sample_report_validates(sample_report: dict, schema: dict) -> None:
    jsonschema.Draft202012Validator(schema).validate(sample_report)


@pytest.mark.parametrize("block_type", BLOCK_TYPES)
def test_each_block_type_validates_individually(block_type: str, sample_report: dict, schema: dict) -> None:
    """One document per block type: schema and Pydantic mirror must both accept it."""
    blocks = [block for section in sample_report["sections"] for block in section["blocks"]]
    chosen = next((block for block in blocks if block["type"] == block_type), None)
    if chosen is None:
        pytest.skip(f"sample fixture has no {block_type} block")

    doc = json.loads(json.dumps(sample_report))
    doc["sections"] = [{"id": "thesis", "title": "核心逻辑", "blocks": [chosen]}]
    jsonschema.validate(doc, schema)

    from app.schemas.report import ReportDoc

    ReportDoc.model_validate(doc)


def test_provisional_document_matches_the_minimal_shape(snapshot_payload: dict, schema: dict) -> None:
    """★P1-10: the fallback document must pass the same schema, or it cannot be stored."""
    doc = report_validator.build_provisional_doc(
        snapshot_payload,
        raw_text="模型写了一段话，但没能通过结构校验。" * 20,
        error_code="result_schema_unsatisfied",
        error_message="structured was null",
    )
    jsonschema.validate(doc, schema)
    assert doc["provisional"] is True
    assert doc["sections"][0]["id"] == "raw"
    assert doc["sections"][0]["blocks"][0]["type"] == "callout"


def test_provisional_truncates_runaway_text(snapshot_payload: dict) -> None:
    doc = report_validator.build_provisional_doc(
        snapshot_payload,
        raw_text="长" * 20_000,
        error_code="budget_exceeded",
        error_message="exit 3",
    )
    paragraph = doc["sections"][0]["blocks"][1]
    assert len(paragraph["text"]) <= 4000


def test_unknown_block_type_fails_validation(schema: dict, sample_report: dict) -> None:
    doc = json.loads(json.dumps(sample_report))
    doc["sections"][0]["blocks"] = [{"type": "hologram", "text": "未来"}]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_extra_top_level_field_fails_validation(schema: dict, sample_report: dict) -> None:
    doc = json.loads(json.dumps(sample_report))
    doc["bonusField"] = "不该存在"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_validator_loads_the_same_schema_file() -> None:
    """`report_validator` must validate against the exact file the CLI was given."""
    loaded = report_validator.load_schema()
    assert loaded == json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert report_validator.schema_bytes() == SCHEMA_PATH.stat().st_size


def test_load_schema_dict_helper() -> None:
    assert load_schema_dict()["title"] == "Aragon Research Report v1"
