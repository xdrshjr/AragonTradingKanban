"""Tiny document builders shared by the workflow validator / compiler / budget tests."""

from __future__ import annotations

from typing import Any

from app.schemas.workflow import Edge, Node, Policy, Position, WorkflowDoc

ALL_SECTIONS = ["thesis", "fundamentals", "valuation", "risks", "catalysts"]


def node(node_id: str, kind: str, title: str = "", **config: Any) -> Node:
    return Node(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        title=title or node_id,
        position=Position(x=0, y=0),
        config=config,
    )


def step(node_id: str, title: str = "", **config: Any) -> Node:
    config.setdefault("prompt", f"做 {node_id} 这件事，产出一段结论")
    config.setdefault("successCriteria", "已完成")
    return node(node_id, "step", title or f"步骤 {node_id}", **config)


def edge(source: str, target: str, edge_id: str | None = None, label: str = "") -> Edge:
    return Edge(id=edge_id or f"e_{source}_{target}", source=source, target=target, label=label)


def chain_doc(*middle: Node, policy: Policy | None = None, extra_edges: list[Edge] | None = None) -> WorkflowDoc:
    """`start → m1 → m2 → … → end`, plus optional extra edges."""
    nodes = [node("start", "start", "开始"), *middle, node("end", "end", "提交")]
    ids = [n.id for n in nodes]
    edges = [edge(a, b) for a, b in zip(ids, ids[1:], strict=False)]
    return WorkflowDoc(nodes=nodes, edges=[*edges, *(extra_edges or [])], policy=policy or Policy())


def covering_step(node_id: str = "s1", **config: Any) -> Node:
    config.setdefault("sections", list(ALL_SECTIONS))
    return step(node_id, **config)


def valid_doc() -> WorkflowDoc:
    return chain_doc(covering_step("s1"))


def blank_doc() -> WorkflowDoc:
    return WorkflowDoc(
        nodes=[node("start", "start", "开始"), node("end", "end", "提交")],
        edges=[],
    )
