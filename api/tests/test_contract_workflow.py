"""`contracts/workflow.json` ↔ Python 常量 / Pydantic 枚举 / 校验器实际产出的码（spec §3.2、AC-W9）。

JSON 是前后端共读的真源；前端另有 `web/tests/workflow.contract.test.ts` 读同一份文件。
这里守的是后端这一侧：常量与 JSON 一致、枚举与 schema 一致、契约里登记的每个问题码都真的
有产出点，而校验器产出的每个码都登记在契约里（否则前端 `copy.workflow.ts` 会漏文案）。
"""

from __future__ import annotations

import ast
import json
import typing
from pathlib import Path

from app.schemas import workflow as schema
from app.services import workflow_contract as C
from app.services import workflow_validator as validator

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "workflow.json").read_text(encoding="utf-8"))


def test_limits_match_the_json() -> None:
    for name, value in CONTRACT["limits"].items():
        if hasattr(C, name):
            assert getattr(C, name) == value, name
    for name in ("MAX_NODES", "MAX_STEPS", "MAX_EDGES", "MAX_DOC_BYTES", "MAX_PLAN_BYTES", "INLINE_INDEX_MAX_BYTES"):
        assert name in CONTRACT["limits"] and hasattr(C, name)


def test_spec_numbers_are_the_documented_ones() -> None:
    assert (C.MAX_NODES, C.MAX_STEPS, C.MAX_EDGES) == (60, 30, 120)
    assert C.MAX_DOC_BYTES == 131_072 and C.MAX_PLAN_BYTES == 49_152 and C.WARN_PLAN_BYTES == 24_576
    assert C.INLINE_INDEX_MAX_BYTES == 2_048 and C.ASSIST_MAX_OPS == 40


def test_enums_match_the_pydantic_literals() -> None:
    assert set(typing.get_args(schema.NodeKind)) == set(C.NODE_KINDS)
    assert set(typing.get_args(schema.Role)) == set(C.ROLES)
    assert set(typing.get_args(schema.Effort)) == set(C.EFFORTS)
    assert set(typing.get_args(schema.Adherence)) == set(C.ADHERENCE)
    assert set(C.EFFORT_WEIGHTS) == set(C.EFFORTS)


def test_five_sections_and_four_files() -> None:
    assert C.SECTIONS == ("thesis", "fundamentals", "valuation", "risks", "catalysts")
    assert C.FILES == ("snapshot.json", "bars.csv", "fundamentals.md", "sources.md")
    assert set(C.SECTION_LABELS) == set(C.SECTIONS)


def test_placeholders_and_reserved_ids() -> None:
    assert "instrument.symbol" in C.PLACEHOLDERS and "asOf" in C.PLACEHOLDERS and len(C.PLACEHOLDERS) == 7
    assert {"__proto__", "constructor", "prototype"} == set(C.RESERVED_IDS)


def test_marker_pattern_is_the_documented_grammar() -> None:
    assert C.MARKER_PATTERN.fullmatch("[[wf:S12:done]]")
    assert C.MARKER_PATTERN.fullmatch("[[wf:G1:skip 数据不足]]")
    assert not C.MARKER_PATTERN.fullmatch("[[wf:X1:done]]")
    assert not C.MARKER_PATTERN.fullmatch("[[wf:S123:done]]")
    assert not C.MARKER_PATTERN.fullmatch("[wf:S1:done]")


def test_op_names_cover_the_algebra() -> None:
    expected = {
        "add_node",
        "update_node",
        "move_node",
        "delete_node",
        "add_edge",
        "update_edge",
        "delete_edge",
        "set_policy",
        "set_viewport",
    }
    assert set(C.OP_NAMES) == expected
    assert set(schema.NON_STRUCTURAL_OPS) <= expected


def test_issue_codes_are_unique_and_disjoint() -> None:
    errors, warnings = list(C.ERROR_CODES), list(C.WARNING_CODES)
    assert len(errors) == len(set(errors)) and len(warnings) == len(set(warnings))
    assert not set(errors) & set(warnings)


def _emitted_codes() -> dict[str, set[str]]:
    """String literals passed as the first argument to `_error(...)` / `_warn(...)`."""
    tree = ast.parse(Path(validator.__file__).read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {"error": set(), "warning": set()}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args):
            continue
        first = node.args[0]
        if node.func.id in ("_error", "_warn") and isinstance(first, ast.Constant) and isinstance(first.value, str):
            found["error" if node.func.id == "_error" else "warning"].add(first.value)
    return found


def test_every_registered_code_is_emitted_with_the_right_severity() -> None:
    emitted = _emitted_codes()
    assert emitted["error"] == set(C.ERROR_CODES)
    assert emitted["warning"] == set(C.WARNING_CODES)


def test_reject_reasons_are_the_ones_the_ops_layer_uses() -> None:
    ops_source = Path(__file__).resolve().parents[1] / "app" / "services" / "workflow_ops.py"
    if not ops_source.exists():  # PA's file may not have landed yet
        return
    text = ops_source.read_text(encoding="utf-8")
    for reason in C.OP_REJECT_REASONS:
        assert f'"{reason}"' in text, reason


def test_builtin_identity_and_sample_context() -> None:
    assert C.BUILTIN_TEMPLATE_ID == "wf_builtin_standard"
    assert set(C.SAMPLE_CONTEXT) == {"symbol", "name", "market", "currency", "sector", "asOf", "timeframe"}
    assert CONTRACT["global"]["aragonMaxTurns"] == 40


def test_config_keys_per_kind_are_the_documented_ones() -> None:
    assert schema.CONFIG_KEYS["step"] == {"role", "prompt", "files", "sections", "successCriteria", "effort"}
    assert schema.CONFIG_KEYS["gate"] == {"checklist"} and schema.CONFIG_KEYS["note"] == {"body"}
    assert not schema.CONFIG_KEYS["start"] and not schema.CONFIG_KEYS["end"]
