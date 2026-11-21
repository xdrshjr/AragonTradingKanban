"""内置模板 `标准五段式研究`（spec §6.3）：确定性、形状、合法、可编译、章节全覆盖。"""

from __future__ import annotations

import pytest

from app.schemas import workflow as S
from app.services import workflow_builtin as B
from app.services import workflow_contract as C
from app.services import workflow_ops as O


def test_builtin_doc_is_deterministic_and_independent_per_call() -> None:
    first, second = B.builtin_doc(), B.builtin_doc()
    assert first.model_dump_json() == second.model_dump_json()
    first.nodes[1].config["files"].append("bars.csv")
    assert B.builtin_doc().model_dump_json() == second.model_dump_json()


def test_builtin_shape_matches_the_spec_graph() -> None:
    doc = B.builtin_doc()
    kinds = [n.kind for n in doc.nodes]
    assert kinds.count("step") == 6 and kinds.count("gate") == 1
    assert kinds.count("start") == kinds.count("end") == 1 and "note" not in kinds
    pairs = {(e.source, e.target) for e in doc.edges}
    assert pairs == {
        (B.NODE_START, B.NODE_S1),
        (B.NODE_S1, B.NODE_S2),
        (B.NODE_S1, B.NODE_S3),
        (B.NODE_S2, B.NODE_G1),
        (B.NODE_S3, B.NODE_G1),
        (B.NODE_G1, B.NODE_S4),
        (B.NODE_S4, B.NODE_S5),
        (B.NODE_S5, B.NODE_S6),
        (B.NODE_S6, B.NODE_END),
    }
    assert B.BUILTIN_TEMPLATE_ID == C.BUILTIN_TEMPLATE_ID and B.BUILTIN_NAME == "标准五段式研究"


def test_builtin_ids_are_fixed_and_valid() -> None:
    doc = B.builtin_doc()
    ids = [n.id for n in doc.nodes] + [e.id for e in doc.edges]
    assert len(ids) == len(set(ids))
    assert all(i.startswith(("n_builtin_", "e_builtin_")) for i in ids)
    S.WorkflowDoc.model_validate(doc.model_dump(mode="json"))


def test_builtin_covers_all_five_sections_and_respects_limits() -> None:
    doc = B.builtin_doc()
    served = {s for n in doc.nodes if n.kind == "step" for s in n.config.get("sections", [])}
    assert served == set(C.SECTIONS)
    for node in doc.nodes:
        assert len(node.title) <= C.TITLE_MAX
        assert len(node.config.get("prompt", "")) <= C.PROMPT_MAX
        assert len(node.config.get("successCriteria", "")) <= C.CRITERIA_MAX
        assert len(node.config.get("checklist", "")) <= C.CHECKLIST_MAX
        assert set(node.config.get("files", [])) <= set(C.FILES)


def test_builtin_keeps_the_sixty_day_bars_budget_and_hard_boundaries_in_the_prompts() -> None:
    texts = " ".join(n.config.get("prompt", "") for n in B.builtin_doc().nodes)
    assert "最后 60" in texts and "bars.csv" in texts
    assert "snapshot.json" in texts and "submit_result" not in texts  # submission is the system prompt's job


def test_builtin_positions_come_from_the_shared_layout() -> None:
    doc = B.builtin_doc()
    placed = O.layout_levels(doc)
    assert all((n.position.x, n.position.y) == (placed[n.id]["x"], placed[n.id]["y"]) for n in doc.nodes)
    assert O.auto_layout_ops(doc) == []


def test_builtin_is_an_editable_base_for_the_op_algebra() -> None:
    """'复制为可编辑副本' then adding a step must work on the very document the built-in ships."""
    doc = B.builtin_doc()
    s4 = next(n for n in doc.nodes if n.id == B.NODE_S4)
    new = {
        "id": "n_peer",
        "kind": "step",
        "title": "同业对比",
        "position": {"x": 0, "y": 0},
        "rev": 1,
        "config": {"prompt": "对比同业"},
    }
    ops = [
        {"op": "add_node", "node": new},
        {"op": "add_edge", "edge": {"id": "e_peer", "source": B.NODE_G1, "target": "n_peer", "label": "", "rev": 1}},
        {"op": "add_edge", "edge": {"id": "e_peer2", "source": "n_peer", "target": B.NODE_S4, "label": "", "rev": 1}},
        {"op": "update_node", "id": B.NODE_S4, "expectRev": s4.rev, "patch": {"title": "估值（含同业）"}},
    ]
    result = O.apply_ops(doc, ops)
    assert result.ok and result.structural


def test_builtin_is_publishable_without_errors() -> None:
    validator = pytest.importorskip("app.services.workflow_validator")
    report = validator.validate(B.builtin_doc())
    assert report.errorCount == 0 and report.publishable and not report.blank, report.issues
    codes = {i.code for i in report.issues}
    assert not codes & {"budget_tight", "plan_large", "section_not_covered"}, codes


def test_builtin_compiles_with_the_expected_keys_and_stays_under_budget() -> None:
    compiler = pytest.importorskip("app.services.workflow_compiler")
    compiled = compiler.compile_workflow(
        B.builtin_doc(), compiler.sample_context(), template_name=B.BUILTIN_NAME, version=1
    )
    assert [i.key for i in compiled.items] == ["S1", "S2", "S3", "G1", "S4", "S5", "S6"]
    assert len(compiled.inline_index.encode("utf-8")) <= C.INLINE_INDEX_MAX_BYTES
    assert len(compiled.plan_md.encode("utf-8")) < C.WARN_PLAN_BYTES
    assert set(compiled.coverage) == set(C.SECTIONS)
    again = compiler.compile_workflow(
        B.builtin_doc(), compiler.sample_context(), template_name=B.BUILTIN_NAME, version=1
    )
    assert again.plan_md == compiled.plan_md and again.sha1 == compiled.sha1


def test_builtin_content_hash_is_stable_across_calls() -> None:
    validator = pytest.importorskip("app.services.workflow_validator")
    assert validator.content_hash(B.builtin_doc()) == validator.content_hash(B.builtin_doc())
