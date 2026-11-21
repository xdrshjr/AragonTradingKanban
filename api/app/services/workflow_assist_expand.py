"""Turn the assistant's **model-dialect** ops into ordinary algebra ops (spec §3.8.3 steps 5–6).

A **pure** module (no DB, no agent, no clock). The model never writes `id` / `rev` / `position` /
`expectRev` (C-7):

* `add_node.ref` → a server-allocated `n_xxxxxxxx` id; `@ref` in later ops points at it.
* `near` → the new node is placed beside that node; without `near` it is placed by the shared
  `layout_levels` (the very function the canvas' "整理布局" button mirrors, RW-7).
* `expectRev` is stamped from the **snapshot** the model saw, never from the model's output.
* `delete_edge` may name an edge by `{source, target}`; `auto_layout` expands into `move_node` ops.

Anything outside the assistant's vocabulary — `publish`, `set_default`, `move_node`,
`set_viewport`… — is refused here (`forbidden_op`): the assistant cannot publish by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..schemas.workflow import WorkflowDoc
from . import workflow_contract as C
from .workflow_ops import apply_ops, auto_layout_ops, layout_levels, new_edge_id, new_node_id

ASSIST_OPS = frozenset(
    {"add_node", "update_node", "delete_node", "add_edge", "delete_edge", "set_policy", "auto_layout"}
)
_REF_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
#: Offset of a `near` placement relative to its anchor node (canvas units).
NEAR_DX, NEAR_DY = 40, 130


@dataclass(frozen=True)
class Failure:
    """The first thing wrong with a batch; `index` is the **model's** op index."""

    index: int
    reason: str
    message: str

    def line(self) -> str:
        return f"第 {self.index + 1} 个操作：{self.reason} — {self.message}"


@dataclass(frozen=True)
class Expansion:
    ok: bool
    ops: tuple[dict[str, Any], ...] = ()
    doc: WorkflowDoc | None = None
    failure: Failure | None = None
    refs: dict[str, str] = field(default_factory=dict)


class _Bad(Exception):
    def __init__(self, index: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.failure = Failure(index, reason, message)


@dataclass
class _Ctx:
    snapshot: WorkflowDoc
    refs: dict[str, str]
    positions: dict[str, tuple[float, float]]
    node_revs: dict[str, int]
    edge_revs: dict[str, int]
    edge_ids: dict[tuple[str, str], str]
    out: list[dict[str, Any]] = field(default_factory=list)
    origin: list[int] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)

    def emit(self, index: int, op: dict[str, Any]) -> None:
        self.out.append(op)
        self.origin.append(index)


def _context(snapshot: WorkflowDoc) -> _Ctx:
    return _Ctx(
        snapshot=snapshot,
        refs={},
        positions={n.id: (n.position.x, n.position.y) for n in snapshot.nodes},
        node_revs={n.id: n.rev for n in snapshot.nodes},
        edge_revs={e.id: e.rev for e in snapshot.edges},
        edge_ids={(e.source, e.target): e.id for e in snapshot.edges},
    )


def _allocate_refs(ctx: _Ctx, raw_ops: list[dict[str, Any]]) -> None:
    taken = {n.id for n in ctx.snapshot.nodes}
    for index, raw in enumerate(raw_ops):
        if raw.get("op") != "add_node":
            continue
        ref = raw.get("ref")
        if not isinstance(ref, str) or not _REF_RE.match(ref) or ref in ctx.refs:
            raise _Bad(index, "bad_patch", f"ref {ref!r} 非法或重复（须唯一且匹配 ^[a-z][a-z0-9_]{{0,23}}$）")
        node_id = new_node_id()
        while node_id in taken:  # pragma: no cover - 36**8 ids
            node_id = new_node_id()
        taken.add(node_id)
        ctx.refs[ref] = node_id


def _resolve(ctx: _Ctx, index: int, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _Bad(index, "bad_patch", "缺少节点引用")
    if value.startswith("@"):
        node_id = ctx.refs.get(value[1:])
        if node_id is None:
            raise _Bad(index, "unknown_node", f"引用了不存在的 {value}")
        return node_id
    if value not in ctx.node_revs:
        raise _Bad(index, "unknown_node", f"节点 {value!r} 不在 graph.json 里（只能引用已有节点 id）")
    return value


def _place_near(ctx: _Ctx, index: int, raw: dict[str, Any]) -> tuple[float, float]:
    near = raw.get("near")
    if near is None:
        return (0.0, 0.0)
    anchor = ctx.positions.get(_resolve(ctx, index, near))
    if anchor is None:
        raise _Bad(index, "unknown_node", f"near 指向的节点 {near!r} 不存在")
    return (anchor[0] + NEAR_DX, anchor[1] + NEAR_DY)


def _add_node(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    if raw.get("kind") not in ("step", "gate", "note"):
        raise _Bad(index, "bad_kind", "add_node.kind 只能是 step / gate / note")
    node_id = ctx.refs[raw["ref"]]
    x, y = _place_near(ctx, index, raw)
    ctx.positions[node_id] = (x, y)
    ctx.node_revs[node_id] = 1
    if raw.get("near") is None:
        ctx.unplaced.append(node_id)
    node = {
        "id": node_id,
        "kind": raw["kind"],
        "title": str(raw.get("title", "")),
        "rev": 1,
        "position": {"x": x, "y": y},
        "config": raw.get("config") or {},
    }
    ctx.emit(index, {"op": "add_node", "node": node})


def _update_node(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    node_id = _resolve(ctx, index, raw.get("id"))
    patch = raw.get("patch")
    if not isinstance(patch, dict):
        raise _Bad(index, "bad_patch", "update_node.patch 须为对象")
    ctx.emit(index, {"op": "update_node", "id": node_id, "expectRev": ctx.node_revs.get(node_id, 1), "patch": patch})


def _delete_node(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    node_id = _resolve(ctx, index, raw.get("id"))
    ctx.emit(index, {"op": "delete_node", "id": node_id, "expectRev": ctx.node_revs.get(node_id, 1)})


def _add_edge(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    edge = {
        "id": new_edge_id(),
        "source": _resolve(ctx, index, raw.get("source")),
        "target": _resolve(ctx, index, raw.get("target")),
        "label": str(raw.get("label", "")),
        "rev": 1,
    }
    ctx.emit(index, {"op": "add_edge", "edge": edge})


def _delete_edge(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    edge_id = raw.get("id")
    if not edge_id and raw.get("source") and raw.get("target"):
        pair = (_resolve(ctx, index, raw["source"]), _resolve(ctx, index, raw["target"]))
        edge_id = ctx.edge_ids.get(pair)
    if not isinstance(edge_id, str) or edge_id not in ctx.edge_revs:
        raise _Bad(index, "unknown_edge", "要删除的连线不存在（用 {id} 或已有的 {source,target}）")
    ctx.emit(index, {"op": "delete_edge", "id": edge_id, "expectRev": ctx.edge_revs[edge_id]})


def _set_policy(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    if not isinstance(raw.get("patch"), dict):
        raise _Bad(index, "bad_patch", "set_policy.patch 须为对象")
    ctx.emit(index, {"op": "set_policy", "patch": raw["patch"]})


def _auto_layout(ctx: _Ctx, index: int, raw: dict[str, Any]) -> None:
    trial = apply_ops(ctx.snapshot, ctx.out)
    if not trial.ok or trial.doc is None:
        raise _Bad(index, "bad_patch", "auto_layout 之前的操作已不成立，无法整理布局")
    for move in auto_layout_ops(trial.doc):
        ctx.emit(index, move)


_HANDLERS = {
    "add_node": _add_node,
    "update_node": _update_node,
    "delete_node": _delete_node,
    "add_edge": _add_edge,
    "delete_edge": _delete_edge,
    "set_policy": _set_policy,
    "auto_layout": _auto_layout,
}


def _expand_all(ctx: _Ctx, raw_ops: list[dict[str, Any]]) -> None:
    if len(raw_ops) > C.ASSIST_MAX_OPS:
        raise _Bad(C.ASSIST_MAX_OPS, "limit_exceeded", f"一批最多 {C.ASSIST_MAX_OPS} 个操作，实际 {len(raw_ops)} 个")
    for index, raw in enumerate(raw_ops):
        name = raw.get("op") if isinstance(raw, dict) else None
        if name not in ASSIST_OPS:
            raise _Bad(index, "forbidden_op", f"助手不能使用操作 {name!r}（只能改草稿：{sorted(ASSIST_OPS)}）")
    _allocate_refs(ctx, raw_ops)
    for index, raw in enumerate(raw_ops):
        _HANDLERS[raw["op"]](ctx, index, raw)


def _apply_checked(ctx: _Ctx) -> WorkflowDoc:
    result = apply_ops(ctx.snapshot, ctx.out)
    if result.ok and result.doc is not None:
        return result.doc
    failure = result.failure
    assert failure is not None
    origin = ctx.origin[failure.opIndex] if failure.opIndex < len(ctx.origin) else failure.opIndex
    raise _Bad(origin, failure.reason, failure.message or failure.reason)


def _place_unanchored(ctx: _Ctx, doc: WorkflowDoc) -> WorkflowDoc:
    """New nodes without `near` get their coordinates from the shared `layout_levels`."""
    if not ctx.unplaced:
        return doc
    layout = layout_levels(doc)
    for node_id in ctx.unplaced:
        if node_id in layout:
            ctx.emit(len(ctx.origin), {"op": "move_node", "id": node_id, "position": layout[node_id]})
    return _apply_checked(ctx)


def expand(snapshot: WorkflowDoc, raw_ops: list[dict[str, Any]]) -> Expansion:
    """Expand and trial-apply `raw_ops` against the snapshot the model saw."""
    ctx = _context(snapshot)
    try:
        _expand_all(ctx, raw_ops)
        doc = _place_unanchored(ctx, _apply_checked(ctx))
    except _Bad as bad:
        return Expansion(ok=False, failure=bad.failure, refs=dict(ctx.refs))
    return Expansion(ok=True, ops=tuple(ctx.out), doc=doc, refs=dict(ctx.refs))


def summarize(ops: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, int]:
    """`{added, updated, deleted, edges}` for the change card (`edges` = connections added)."""
    names = [op["op"] for op in ops]
    nodes = [op["node"]["kind"] for op in ops if op["op"] == "add_node"]
    return {
        "added": len(nodes),
        "gates": sum(1 for kind in nodes if kind == "gate"),
        "updated": names.count("update_node") + names.count("set_policy"),
        "deleted": names.count("delete_node"),
        "edges": names.count("add_edge"),
    }
