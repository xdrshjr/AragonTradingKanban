"""操作代数（spec §3.3）：共享 golden 夹具 + 不变量 + 属性式随机批。

`contracts/workflow_ops.golden.json` 是 Python 与 TS 两端共同的金标。本文件逐条断言它；
`python tests/test_workflow_ops.py --regen` 只**重写 expect**（ops 与 doc 输入保持不变）——
重写后必须人工 diff 复核，不能让夹具跟着实现漂移而没人看。
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

import pytest

from app.schemas import workflow as S
from app.services import workflow_contract as C
from app.services import workflow_ops as O

GOLDEN_PATH = Path(__file__).resolve().parents[2] / "contracts" / "workflow_ops.golden.json"


def _golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _norm(value):
    """JSON-comparable form: integral floats become ints (the golden stores `300`, Python has `300.0`)."""
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def _expected(case: dict) -> dict:
    result = O.apply_ops(S.WorkflowDoc.model_validate(case["doc"]), case["ops"])
    if result.ok:
        assert result.doc is not None
        return {"ok": True, "structural": result.structural, "doc": _norm(result.doc.model_dump(mode="json"))}
    assert result.failure is not None
    out: dict = {"ok": False, "reason": result.failure.reason, "opIndex": result.failure.opIndex}
    if result.conflicts:
        out["conflicts"] = [c.model_dump() for c in result.conflicts]
    return out


GOLDEN = _golden()


def test_golden_is_large_and_covers_every_op_and_reject_reason() -> None:
    names = {op["op"] for case in GOLDEN["ops"] for op in case["ops"] if isinstance(op, dict)}
    assert set(C.OP_NAMES) <= names
    reasons = {c["expect"]["reason"] for c in GOLDEN["ops"] if not c["expect"]["ok"]}
    assert set(C.OP_REJECT_REASONS) <= reasons, set(C.OP_REJECT_REASONS) - reasons
    assert len(GOLDEN["ops"]) >= 60 and len(GOLDEN["layout"]) >= 8
    assert len({c["name"] for c in GOLDEN["ops"]}) == len(GOLDEN["ops"])


@pytest.mark.parametrize("case", GOLDEN["ops"], ids=lambda c: c["name"])
def test_golden_ops(case: dict) -> None:
    assert _expected(case) == case["expect"]


@pytest.mark.parametrize("case", GOLDEN["layout"], ids=lambda c: c["name"])
def test_golden_layout(case: dict) -> None:
    got = O.layout_levels(S.WorkflowDoc.model_validate(case["doc"]))
    assert _norm(got) == case["expect"]


# ── invariants beyond the golden ─────────────────────────────────────────────


def _base() -> S.WorkflowDoc:
    case = next(c for c in GOLDEN["ops"] if c["name"] == "update_node_rev_conflict")
    return S.WorkflowDoc.model_validate(case["doc"])


def test_apply_ops_never_mutates_the_input_document() -> None:
    doc = _base()
    before = doc.model_dump_json()
    ops = [
        {"op": "move_node", "id": "n_s1", "position": {"x": 1, "y": 2}},
        {"op": "update_node", "id": "n_s1", "expectRev": 3, "patch": {"title": "改"}},
        {"op": "delete_node", "id": "n_g1", "expectRev": 1},
    ]
    assert O.apply_ops(doc, ops).ok
    assert doc.model_dump_json() == before


def test_failed_batch_returns_no_document() -> None:
    result = O.apply_ops(_base(), [{"op": "delete_node", "id": "ghost", "expectRev": 1}])
    assert not result.ok and result.doc is None and result.failure is not None
    assert result.failure.entityId == "ghost"


def test_rev_conflict_reports_expected_and_actual() -> None:
    result = O.apply_ops(_base(), [{"op": "update_node", "id": "n_s1", "expectRev": 1, "patch": {"title": "x"}}])
    assert [(c.entityId, c.expected, c.actual) for c in result.conflicts] == [("n_s1", 1, 3)]


def test_non_dict_op_is_rejected_not_raised() -> None:
    result = O.apply_ops(_base(), ["add_node"])  # type: ignore[list-item]
    assert not result.ok and result.failure is not None and result.failure.reason == "bad_patch"


def test_layout_is_deterministic_and_ignores_notes() -> None:
    doc = _base()
    first = O.layout_levels(doc)
    assert first == O.layout_levels(copy.deepcopy(doc))
    assert "n_note" not in first


def test_auto_layout_ops_move_only_misplaced_nodes_and_converge() -> None:
    doc = _base()
    ops = O.auto_layout_ops(doc)
    assert ops and all(op["op"] == "move_node" for op in ops)
    moved = O.apply_ops(doc, ops)
    assert moved.ok and not moved.structural and moved.doc is not None
    assert O.auto_layout_ops(moved.doc) == []


def test_anchor_only_doc_has_exactly_start_and_end() -> None:
    doc = O.create_anchor_only_doc()
    assert sorted(n.kind for n in doc.nodes) == ["end", "start"] and doc.edges == []
    assert len({n.id for n in doc.nodes}) == 2
    S.WorkflowDoc.model_validate(doc.model_dump(mode="json"))


def test_new_ids_match_contract_pattern() -> None:
    import re

    for make, prefix in ((O.new_node_id, "n_"), (O.new_edge_id, "e_")):
        value = make()
        assert value.startswith(prefix) and len(value) == 10
        assert re.fullmatch(C.ID_PATTERN, value) and value not in C.RESERVED_IDS
    assert len({O.new_node_id() for _ in range(200)}) == 200


# ── property-style: random batches never break the structural invariants ─────


def _check_structure(doc: S.WorkflowDoc) -> None:
    ids = [n.id for n in doc.nodes]
    assert len(ids) == len(set(ids)) <= C.MAX_NODES
    kinds = [n.kind for n in doc.nodes]
    assert kinds.count("start") == kinds.count("end") == 1
    by_id = {n.id: n for n in doc.nodes}
    assert len({e.id for e in doc.edges}) == len(doc.edges) <= C.MAX_EDGES
    pairs = [(e.source, e.target) for e in doc.edges]
    assert len(pairs) == len(set(pairs))
    for source, target in pairs:
        assert source != target and source in by_id and target in by_id
        assert "note" not in (by_id[source].kind, by_id[target].kind)
        assert by_id[target].kind != "start" and by_id[source].kind != "end"
    _assert_acyclic(doc)


def _assert_acyclic(doc: S.WorkflowDoc) -> None:
    indegree = {n.id: 0 for n in doc.nodes}
    out: dict[str, list[str]] = {n.id: [] for n in doc.nodes}
    for edge in doc.edges:
        out[edge.source].append(edge.target)
        indegree[edge.target] += 1
    ready = [i for i, d in indegree.items() if d == 0]
    seen = 0
    while ready:
        current = ready.pop()
        seen += 1
        for nxt in out[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    assert seen == len(doc.nodes), "cycle slipped through apply_ops"


def _random_op(rng: random.Random, doc: S.WorkflowDoc) -> dict:
    node_ids = [n.id for n in doc.nodes] + ["ghost"]
    kind = rng.choice(
        ["add_node", "update_node", "move_node", "delete_node", "add_edge", "update_edge", "delete_edge", "set_policy"]
    )
    if kind == "add_node":
        node_kind = rng.choice(["step", "step", "gate", "note", "start"])
        node = {
            "id": f"n_r{rng.randrange(40)}",
            "kind": node_kind,
            "title": "t",
            "position": {"x": 0, "y": 0},
            "rev": 1,
            "config": {},
        }
        return {"op": kind, "node": node}
    if kind == "update_node":
        node = rng.choice(doc.nodes)
        patch = rng.choice([{"title": "新"}, {"config": {"prompt": "p"}}, {"kind": "gate"}])
        return {"op": kind, "id": node.id, "expectRev": node.rev + rng.choice([0, 0, 0, 1]), "patch": patch}
    if kind == "move_node":
        return {"op": kind, "id": rng.choice(node_ids), "position": {"x": rng.randrange(900), "y": rng.randrange(900)}}
    if kind == "delete_node":
        node = rng.choice(doc.nodes)
        return {"op": kind, "id": node.id, "expectRev": node.rev}
    if kind == "add_edge":
        edge = {
            "id": f"e_r{rng.randrange(60)}",
            "source": rng.choice(node_ids),
            "target": rng.choice(node_ids),
            "label": "",
            "rev": 1,
        }
        return {"op": kind, "edge": edge}
    if kind == "set_policy":
        return {"op": kind, "patch": {"maxTurns": rng.choice([None, 5, 30])}}
    if not doc.edges:
        return {"op": "delete_edge", "id": "ghost", "expectRev": 1}
    edge_obj = rng.choice(doc.edges)
    patch_or_rev = {"id": edge_obj.id, "expectRev": edge_obj.rev}
    if kind == "update_edge":
        return {"op": kind, **patch_or_rev, "patch": {"label": rng.choice(["a", None])}}
    return {"op": kind, **patch_or_rev}


@pytest.mark.parametrize("seed", range(12))
def test_random_batches_keep_every_structural_invariant(seed: int) -> None:
    rng = random.Random(seed)
    doc = O.create_anchor_only_doc()
    accepted = 0
    for _ in range(160):
        batch = [_random_op(rng, doc) for _ in range(rng.randrange(1, 5))]
        result = O.apply_ops(doc, batch)
        if result.ok:
            assert result.doc is not None
            _check_structure(result.doc)
            doc, accepted = result.doc, accepted + 1
        else:
            assert result.failure is not None and result.failure.reason in C.OP_REJECT_REASONS
            assert 0 <= result.failure.opIndex < len(batch)
    assert accepted > 20, "the random walk should actually change the document"
    _check_structure(doc)


def test_layout_covers_every_non_note_node_after_random_walk() -> None:
    rng = random.Random(99)
    doc = O.create_anchor_only_doc()
    for _ in range(120):
        result = O.apply_ops(doc, [_random_op(rng, doc)])
        doc = result.doc if result.ok and result.doc else doc
    placed = O.layout_levels(doc)
    assert set(placed) == {n.id for n in doc.nodes if n.kind != "note"}


# ── regeneration hook ────────────────────────────────────────────────────────


def _regen() -> None:  # pragma: no cover - manual tool
    data = _golden()
    for case in data["ops"]:
        case["expect"] = _expected(case)
    for case in data["layout"]:
        case["expect"] = _norm(O.layout_levels(S.WorkflowDoc.model_validate(case["doc"])))
    GOLDEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"rewrote {len(data['ops'])} ops + {len(data['layout'])} layout expectations")


if __name__ == "__main__":  # pragma: no cover
    if "--regen" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        _regen()
