"""The workflow operation algebra (spec `report-workflow-graph` §3.3) — a **pure** module.

Every write to a draft — a canvas gesture or the AI assistant — is a ``list[dict]`` of ops fed to
:func:`apply_ops`. ``web/lib/workflow/ops.ts`` mirrors this file; ``contracts/workflow_ops.golden.json``
pins both sides (`tests/test_workflow_ops.py` / `web/tests/workflow.ops.test.ts`). Change a rule
here and the golden must change with it.

Rules that both implementations obey (each is a golden case):

* **Atomic**: ops run in order on a copy; the first failure aborts the whole batch and reports its
  index. Nothing is partially applied.
* ``move_node`` / ``set_viewport`` never touch any ``rev`` (and report ``structural=False`` so the
  caller does not raise ``draftRevision``) — panning must not invalidate an AI's ``baseRevision``.
* ``expectRev`` is compared with the entity's ``rev`` **at the start of the batch** (an entity added
  inside the batch counts as ``rev 1``). So one author may chain several edits of the same node in
  one batch (the assistant does), while a concurrent change by someone else is still detected.
  Each structural op then bumps the entity's ``rev`` by one; new entities always start at ``rev 1``
  whatever ``rev`` the op carried.
* ``update_*.patch``: ``title`` replaces; ``config`` merges key by key (shallow, arrays replaced
  whole, ``null`` clears the key); ``kind`` / ``id`` / ``position`` / ``rev`` (and an edge's
  ``source`` / ``target``) are immutable → ``immutable_field``.
* Edge rules: no self loop, no duplicate ``(source, target)``, no cycle, no ``note`` endpoint, no
  edge into ``start`` or out of ``end``.
* ``start`` / ``end`` cannot be added (only an anchor document has them) or deleted.

Layout (:func:`layout_levels`): Kahn layering over the non-``note`` nodes — a node's level is the
longest path to it; nodes in a cycle (or behind one) form one extra trailing level, in document
order. ``x = 80 + level × 300``; inside a level the nodes keep document order and
``y = 60 + (index + (tallestLevelCount − levelCount) / 2) × 150`` (every level is vertically
centred against the tallest one).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ..schemas import workflow as S
from . import workflow_contract as C

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"

#: Reasons beyond `contracts/workflow.json.opRejectReasons`'s base set live there too.
REV_CONFLICT = "rev_conflict"

_MODELS: dict[str, type[BaseModel]] = {
    "add_node": S.AddNode,
    "update_node": S.UpdateNode,
    "move_node": S.MoveNode,
    "delete_node": S.DeleteNode,
    "add_edge": S.AddEdge,
    "update_edge": S.UpdateEdge,
    "delete_edge": S.DeleteEdge,
    "set_policy": S.SetPolicy,
    "set_viewport": S.SetViewport,
}
_ADDABLE_KINDS = ("step", "gate", "note")
_IMMUTABLE_NODE = ("kind", "id", "position", "rev")
_IMMUTABLE_EDGE = ("id", "source", "target", "rev")
_POLICY_KEYS = ("maxTurns", "maxDurationMinutes", "progressMarkers")


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    doc: S.WorkflowDoc | None
    #: True when the batch contained a structural op (the caller then raises `draftRevision`).
    structural: bool
    failure: S.OpFailure | None = None
    conflicts: tuple[S.OpConflict, ...] = ()


class _Reject(Exception):
    def __init__(self, reason: str, message: str, entity_id: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.entity_id = entity_id


class _RevConflict(_Reject):
    def __init__(self, entity_id: str, expected: int, actual: int) -> None:
        super().__init__(REV_CONFLICT, f"{entity_id} 已被修改（期望 rev {expected}，实际 {actual}）", entity_id)
        self.expected = expected
        self.actual = actual


@dataclass
class _State:
    nodes: dict[str, dict[str, Any]]
    edges: dict[str, dict[str, Any]]
    policy: dict[str, Any]
    viewport: dict[str, Any]
    base_node_revs: dict[str, int]
    base_edge_revs: dict[str, int]

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "nodes": list(self.nodes.values()),
            "edges": list(self.edges.values()),
            "policy": self.policy,
            "viewport": self.viewport,
        }


# ── ids ──────────────────────────────────────────────────────────────────────


def _random_id(prefix: str) -> str:
    return prefix + "".join(secrets.choice(_B36) for _ in range(8))


def new_node_id() -> str:
    return _random_id("n_")


def new_edge_id() -> str:
    return _random_id("e_")


def create_anchor_only_doc() -> S.WorkflowDoc:
    """The blank template: just a `start` and an `end` anchor, no steps (`blank=true` in §3.4)."""
    return S.WorkflowDoc(
        nodes=[
            S.Node(id=new_node_id(), kind="start", title="开始", position=S.Position(x=80, y=160)),
            S.Node(id=new_node_id(), kind="end", title="结束", position=S.Position(x=380, y=160)),
        ],
        viewport=S.Viewport(x=80, y=80, zoom=1),
    )


# ── layout ───────────────────────────────────────────────────────────────────


def _levels(doc: S.WorkflowDoc) -> list[list[str]]:
    order = [n.id for n in doc.nodes if n.kind != "note"]
    known = set(order)
    out: dict[str, list[str]] = {nid: [] for nid in order}
    indegree = dict.fromkeys(order, 0)
    for source, target in dict.fromkeys((e.source, e.target) for e in doc.edges):
        if source in known and target in known and source != target:
            out[source].append(target)
            indegree[target] += 1
    index = {nid: i for i, nid in enumerate(order)}
    levels: list[list[str]] = []
    frontier = [nid for nid in order if indegree[nid] == 0]
    placed: set[str] = set()
    while frontier:
        levels.append(frontier)
        placed.update(frontier)
        nxt: list[str] = []
        for nid in frontier:
            for target in out[nid]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    nxt.append(target)
        frontier = sorted(nxt, key=index.__getitem__)
    leftover = [nid for nid in order if nid not in placed]
    if leftover:
        levels.append(leftover)
    return levels


def layout_levels(doc: S.WorkflowDoc) -> dict[str, dict[str, float]]:
    """`{nodeId: {"x", "y"}}` for every non-`note` node (see the module docstring for the rule)."""
    levels = _levels(doc)
    tallest = max((len(level) for level in levels), default=0)
    result: dict[str, dict[str, float]] = {}
    for depth, level in enumerate(levels):
        pad = (tallest - len(level)) / 2
        for index, nid in enumerate(level):
            result[nid] = {
                "x": float(C.LAYOUT_ORIGIN_X + depth * C.LAYOUT_STEP_X),
                "y": float(C.LAYOUT_ORIGIN_Y + (index + pad) * C.LAYOUT_STEP_Y),
            }
    return result


def auto_layout_ops(doc: S.WorkflowDoc) -> list[dict[str, Any]]:
    """The `auto_layout` macro: `move_node` ops for every node that is not already in place."""
    ops: list[dict[str, Any]] = []
    positions = layout_levels(doc)
    for node in doc.nodes:
        target = positions.get(node.id)
        if target and (node.position.x != target["x"] or node.position.y != target["y"]):
            ops.append({"op": "move_node", "id": node.id, "position": dict(target)})
    return ops


# ── graph helpers ────────────────────────────────────────────────────────────


def _reaches(edges: dict[str, dict[str, Any]], start: str, goal: str) -> bool:
    """Is `goal` reachable from `start` along the existing edges (iterative DFS)?"""
    adjacency: dict[str, list[str]] = {}
    for edge in edges.values():
        adjacency.setdefault(edge["source"], []).append(edge["target"])
    stack, seen = [start], {start}
    while stack:
        current = stack.pop()
        if current == goal:
            return True
        for nxt in adjacency.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def _doc_bytes(state: _State) -> int:
    return len(json.dumps(state.snapshot(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _need_node(state: _State, node_id: str) -> dict[str, Any]:
    node = state.nodes.get(node_id)
    if node is None:
        raise _Reject("unknown_node", f"节点不存在：{node_id}", node_id)
    return node


def _need_edge(state: _State, edge_id: str) -> dict[str, Any]:
    edge = state.edges.get(edge_id)
    if edge is None:
        raise _Reject("unknown_edge", f"连线不存在：{edge_id}", edge_id)
    return edge


def _check_rev(base: dict[str, int], entity_id: str, expected: int) -> None:
    actual = base.get(entity_id, 1)
    if actual != expected:
        raise _RevConflict(entity_id, expected, actual)


def _check_new_id(raw: object) -> None:
    if isinstance(raw, str):
        try:
            S.check_id(raw)
        except S.WorkflowSchemaError as exc:
            raise _Reject("reserved_id", str(exc), raw) from exc


# ── op handlers ──────────────────────────────────────────────────────────────


def _op_add_node(state: _State, op: S.AddNode) -> None:
    node = op.node.model_dump(mode="json")
    if node["kind"] not in _ADDABLE_KINDS:
        raise _Reject("bad_kind", f"不能新增 {node['kind']} 节点（起止锚点只由空白文档创建）", node["id"])
    if node["id"] in state.nodes:
        raise _Reject("duplicate_id", f"节点 id 重复：{node['id']}", node["id"])
    steps = sum(1 for n in state.nodes.values() if n["kind"] == "step")
    if len(state.nodes) >= C.MAX_NODES or (node["kind"] == "step" and steps >= C.MAX_STEPS):
        raise _Reject("limit_exceeded", "节点或步骤数量已达上限", node["id"])
    node["rev"] = 1
    state.nodes[node["id"]] = node
    state.base_node_revs[node["id"]] = 1


def _op_update_node(state: _State, op: S.UpdateNode) -> None:
    node = _need_node(state, op.id)
    _check_rev(state.base_node_revs, op.id, op.expectRev)
    for key in op.patch:
        if key in _IMMUTABLE_NODE:
            raise _Reject("immutable_field", f"{key} 不可修改", op.id)
        if key not in ("title", "config"):
            raise _Reject("bad_patch", f"不支持的字段：{key}", op.id)
    title = op.patch.get("title", node["title"])
    if not isinstance(title, str) or len(title) > C.TITLE_MAX:
        raise _Reject("bad_patch", f"标题须为不超过 {C.TITLE_MAX} 字的文本", op.id)
    config = _merge_config(node, op.patch.get("config"), op.id)
    node["title"], node["config"] = title, config
    node["rev"] += 1


def _merge_config(node: dict[str, Any], patch: object, node_id: str) -> dict[str, Any]:
    if patch is None:
        return node["config"]
    if not isinstance(patch, dict):
        raise _Reject("bad_patch", "config 须为对象", node_id)
    merged = dict(node["config"])
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    try:
        S.check_config(node["kind"], merged)
    except S.WorkflowSchemaError as exc:
        raise _Reject("bad_patch", str(exc), node_id) from exc
    return merged


def _op_move_node(state: _State, op: S.MoveNode) -> None:
    node = _need_node(state, op.id)
    node["position"] = op.position.model_dump(mode="json")


def _op_delete_node(state: _State, op: S.DeleteNode) -> None:
    node = _need_node(state, op.id)
    if node["kind"] in ("start", "end"):
        raise _Reject("protected_node", "起点与终点不可删除", op.id)
    _check_rev(state.base_node_revs, op.id, op.expectRev)
    del state.nodes[op.id]
    for edge_id in [i for i, e in state.edges.items() if op.id in (e["source"], e["target"])]:
        del state.edges[edge_id]


def _edge_endpoints(state: _State, source: str, target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    src, dst = _need_node(state, source), _need_node(state, target)
    if source == target:
        raise _Reject("invalid_edge", "不能连接到自身", source)
    if "note" in (src["kind"], dst["kind"]):
        raise _Reject("invalid_edge", "注释节点不能连线", source if src["kind"] == "note" else target)
    if dst["kind"] == "start":
        raise _Reject("invalid_edge", "起点不能有入边", target)
    if src["kind"] == "end":
        raise _Reject("invalid_edge", "终点不能有出边", source)
    return src, dst


def _op_add_edge(state: _State, op: S.AddEdge) -> None:
    edge = op.edge.model_dump(mode="json")
    if edge["id"] in state.edges:
        raise _Reject("duplicate_id", f"连线 id 重复：{edge['id']}", edge["id"])
    if len(state.edges) >= C.MAX_EDGES:
        raise _Reject("limit_exceeded", "连线数量已达上限", edge["id"])
    _edge_endpoints(state, edge["source"], edge["target"])
    if any(e["source"] == edge["source"] and e["target"] == edge["target"] for e in state.edges.values()):
        raise _Reject("invalid_edge", "连线已存在", edge["id"])
    if _reaches(state.edges, edge["target"], edge["source"]):
        raise _Reject("invalid_edge", "连线会形成环", edge["id"])
    edge["rev"] = 1
    state.edges[edge["id"]] = edge
    state.base_edge_revs[edge["id"]] = 1


def _op_update_edge(state: _State, op: S.UpdateEdge) -> None:
    edge = _need_edge(state, op.id)
    _check_rev(state.base_edge_revs, op.id, op.expectRev)
    for key in op.patch:
        if key in _IMMUTABLE_EDGE:
            raise _Reject("immutable_field", f"{key} 不可修改", op.id)
        if key != "label":
            raise _Reject("bad_patch", f"不支持的字段：{key}", op.id)
    label = op.patch.get("label", edge["label"])
    label = "" if label is None else label
    if not isinstance(label, str) or len(label) > C.EDGE_LABEL_MAX:
        raise _Reject("bad_patch", f"标签须为不超过 {C.EDGE_LABEL_MAX} 字的文本", op.id)
    edge["label"] = label
    edge["rev"] += 1


def _op_delete_edge(state: _State, op: S.DeleteEdge) -> None:
    _need_edge(state, op.id)
    _check_rev(state.base_edge_revs, op.id, op.expectRev)
    del state.edges[op.id]


def _op_set_policy(state: _State, op: S.SetPolicy) -> None:
    for key, value in op.patch.items():
        if key not in _POLICY_KEYS:
            raise _Reject("bad_patch", f"不支持的策略项：{key}")
        _set_policy_key(state.policy, key, value)


def _set_policy_key(policy: dict[str, Any], key: str, value: object) -> None:
    if key == "progressMarkers":
        if value is not None and not isinstance(value, bool):
            raise _Reject("bad_patch", "progressMarkers 须为布尔值")
        policy[key] = True if value is None else value
        return
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise _Reject("bad_patch", f"{key} 须为整数或 null")
    policy[key] = value


def _op_set_viewport(state: _State, op: S.SetViewport) -> None:
    state.viewport = op.viewport.model_dump(mode="json")


_HANDLERS: dict[str, Callable[[_State, Any], None]] = {
    "add_node": _op_add_node,
    "update_node": _op_update_node,
    "move_node": _op_move_node,
    "delete_node": _op_delete_node,
    "add_edge": _op_add_edge,
    "update_edge": _op_update_edge,
    "delete_edge": _op_delete_edge,
    "set_policy": _op_set_policy,
    "set_viewport": _op_set_viewport,
}
_GROWING_OPS = frozenset({"add_node", "update_node", "add_edge", "update_edge"})


# ── entry point ──────────────────────────────────────────────────────────────


def _parse(raw: object) -> BaseModel:
    if not isinstance(raw, dict) or raw.get("op") not in _MODELS:
        raise _Reject("bad_patch", f"未知操作：{raw.get('op') if isinstance(raw, dict) else raw!r}")
    if raw["op"] == "add_node" and isinstance(raw.get("node"), dict):
        _check_new_id(raw["node"].get("id"))
        kind = raw["node"].get("kind")
        if isinstance(kind, str) and kind not in C.NODE_KINDS:
            raise _Reject("bad_kind", f"未知节点类型：{kind}")
    if raw["op"] == "add_edge" and isinstance(raw.get("edge"), dict):
        _check_new_id(raw["edge"].get("id"))
    try:
        return _MODELS[raw["op"]].model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise _Reject("bad_patch", f"操作格式不合法：{'.'.join(str(p) for p in first['loc'])} {first['msg']}") from exc


def _initial_state(doc: S.WorkflowDoc) -> _State:
    data = doc.model_dump(mode="json")
    nodes = {n["id"]: n for n in data["nodes"]}
    edges = {e["id"]: e for e in data["edges"]}
    return _State(
        nodes=nodes,
        edges=edges,
        policy=data["policy"],
        viewport=data["viewport"],
        base_node_revs={i: n["rev"] for i, n in nodes.items()},
        base_edge_revs={i: e["rev"] for i, e in edges.items()},
    )


def _fail(index: int, exc: _Reject) -> ApplyResult:
    failure = S.OpFailure(opIndex=index, reason=exc.reason, message=exc.message, entityId=exc.entity_id)
    conflicts: tuple[S.OpConflict, ...] = ()
    if isinstance(exc, _RevConflict):
        conflicts = (S.OpConflict(entityId=exc.entity_id or "", expected=exc.expected, actual=exc.actual),)
    return ApplyResult(ok=False, doc=None, structural=False, failure=failure, conflicts=conflicts)


def apply_ops(doc: S.WorkflowDoc, ops: Sequence[dict[str, Any]], *, now: str | None = None) -> ApplyResult:
    """Apply `ops` to a copy of `doc`, all or nothing. `now` is reserved (the algebra reads no clock)."""
    del now
    state = _initial_state(doc)
    structural = False
    for index, raw in enumerate(ops):
        try:
            parsed = _parse(raw)
            _HANDLERS[raw["op"]](state, parsed)
            if raw["op"] in _GROWING_OPS and _doc_bytes(state) > C.MAX_DOC_BYTES:
                raise _Reject("limit_exceeded", "流程文档体积超出上限")
        except _Reject as exc:
            return _fail(index, exc)
        structural = structural or raw["op"] not in S.NON_STRUCTURAL_OPS
    try:
        result = S.WorkflowDoc.model_validate(state.snapshot())
    except ValidationError as exc:  # pragma: no cover - every op validates its own output
        return _fail(max(len(ops) - 1, 0), _Reject("bad_patch", f"结果文档不合法：{exc.errors()[0]['msg']}"))
    return ApplyResult(ok=True, doc=result, structural=structural)
