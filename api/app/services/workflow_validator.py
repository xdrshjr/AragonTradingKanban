"""Research workflow validator (spec §3.4) — a pure function over a `WorkflowDoc`.

`analyze(doc)` walks the graph once (adjacency, cycle, reachability, topological levels) and is
shared with the compiler; `validate(doc, …)` turns the analysis plus per-node content checks into
a `ValidationReport`. The validator only emits **codes and params** — human sentences live in the
frontend (`copy.workflow.ts`). Saving a draft is always allowed; only `publishable` is gated here.

No DB, no `agent/`, no clock: same document => same report.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections import deque
from dataclasses import dataclass, field

from ..schemas.workflow import Edge, Node, ValidationIssue, ValidationReport, WorkflowDoc, doc_to_json
from . import workflow_contract as C

_PLACEHOLDER_RE = re.compile(r"(\\)?\{\{([^{}]*)\}\}")
_COMPILED_KINDS = ("step", "gate")


@dataclass(frozen=True)
class Ceilings:
    """Site-wide ceilings a workflow `policy` may only **tighten** (spec §3.2)."""

    max_turns: int
    max_duration_minutes: int


def default_ceilings() -> Ceilings:
    from ..config import get_settings

    settings = get_settings()
    return Ceilings(
        max_turns=settings.aragon_max_turns,
        max_duration_minutes=max(1, settings.aragon_max_duration_ms // 60_000),
    )


@dataclass(frozen=True)
class Analysis:
    """One traversal of the graph, shared by the validator and the compiler."""

    nodes: dict[str, Node]
    order: dict[str, int]
    #: Edges that can take part in execution order: both ends exist, no self-loop, first of
    #: any duplicate, no `note` endpoint.
    edges: tuple[Edge, ...]
    succ: dict[str, tuple[str, ...]]
    pred: dict[str, tuple[str, ...]]
    starts: tuple[str, ...]
    ends: tuple[str, ...]
    #: First cycle found, as a node-id path that closes on its first element; empty = acyclic.
    cycle: tuple[str, ...]
    reachable: frozenset[str]
    reaches_end: frozenset[str]
    #: Phase number (1-based) of every `step` / `gate`; 0 = start; nodes on a cycle are absent.
    levels: dict[str, int] = field(default_factory=dict)


def _usable_edges(doc: WorkflowDoc, nodes: dict[str, Node]) -> list[Edge]:
    seen: set[tuple[str, str]] = set()
    kept: list[Edge] = []
    for edge in doc.edges:
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None or edge.source == edge.target:
            continue
        if source.kind == "note" or target.kind == "note":
            continue
        if (edge.source, edge.target) in seen:
            continue
        seen.add((edge.source, edge.target))
        kept.append(edge)
    return kept


def _find_cycle(order: dict[str, int], succ: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Iterative three-colour DFS in document order; returns the first cycle path."""
    colour = {node_id: 0 for node_id in order}
    for root in sorted(order, key=order.__getitem__):
        if colour[root]:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path = [root]
        colour[root] = 1
        while stack:
            node_id, index = stack[-1]
            children = succ[node_id]
            if index >= len(children):
                colour[node_id] = 2
                stack.pop()
                path.pop()
                continue
            stack[-1] = (node_id, index + 1)
            child = children[index]
            if colour[child] == 1:
                return (*path[path.index(child) :], child)
            if colour[child] == 0:
                colour[child] = 1
                stack.append((child, 0))
                path.append(child)
    return ()


def _levels(nodes: dict[str, Node], order: dict[str, int], succ: dict, pred: dict) -> dict[str, int]:
    """Longest-path phase numbers via Kahn (document order breaks ties, so the result is stable)."""
    indegree = {node_id: len(pred[node_id]) for node_id in nodes if nodes[node_id].kind != "note"}
    heap = [(order[node_id], node_id) for node_id, degree in indegree.items() if degree == 0]
    heapq.heapify(heap)
    levels: dict[str, int] = {}
    while heap:
        _, node_id = heapq.heappop(heap)
        kind = nodes[node_id].kind
        base = max((levels[p] for p in pred[node_id] if p in levels), default=0)
        levels[node_id] = 0 if kind == "start" else (base + 1 if kind in _COMPILED_KINDS else base)
        for child in succ[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(heap, (order[child], child))
    return {node_id: level for node_id, level in levels.items() if nodes[node_id].kind in (*_COMPILED_KINDS, "start")}


def _closure(seeds: tuple[str, ...], graph: dict[str, tuple[str, ...]]) -> frozenset[str]:
    seen = set(seeds)
    queue = deque(seeds)
    while queue:
        for nxt in graph[queue.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return frozenset(seen)


def analyze(doc: WorkflowDoc) -> Analysis:
    nodes = {node.id: node for node in doc.nodes}
    order = {node.id: index for index, node in enumerate(doc.nodes)}
    edges = _usable_edges(doc, nodes)
    succ_lists: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    pred_lists: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        succ_lists[edge.source].append(edge.target)
        pred_lists[edge.target].append(edge.source)
    succ = {key: tuple(value) for key, value in succ_lists.items()}
    pred = {key: tuple(value) for key, value in pred_lists.items()}
    starts = tuple(n.id for n in doc.nodes if n.kind == "start")
    ends = tuple(n.id for n in doc.nodes if n.kind == "end")
    cycle = _find_cycle(order, succ)
    return Analysis(
        nodes=nodes,
        order=order,
        edges=tuple(edges),
        succ=succ,
        pred=pred,
        starts=starts,
        ends=ends,
        cycle=cycle,
        reachable=_closure(starts, succ),
        reaches_end=_closure(ends, pred),
        levels=_levels(nodes, order, succ, pred),
    )


# ── issue helpers ────────────────────────────────────────────────────────────


def _issue(code: str, severity: str, kind: str, entity_id: str | None = None, **params: object) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, entityKind=kind, entityId=entity_id, params=params)  # type: ignore[arg-type]


def _error(code: str, kind: str, entity_id: str | None = None, **params: object) -> ValidationIssue:
    return _issue(code, "error", kind, entity_id, **params)


def _warn(code: str, kind: str, entity_id: str | None = None, **params: object) -> ValidationIssue:
    return _issue(code, "warning", kind, entity_id, **params)


def _text(node: Node, key: str) -> str:
    value = node.config.get(key)
    return value if isinstance(value, str) else ""


def is_blank(doc: WorkflowDoc) -> bool:
    """A brand-new graph: exactly one `start` and one `end`, nothing to execute (spec §3.4)."""
    kinds = [n.kind for n in doc.nodes]
    return kinds.count("start") == 1 and kinds.count("end") == 1 and not any(k in _COMPILED_KINDS for k in kinds)


# ── error checks ─────────────────────────────────────────────────────────────


def _check_limits(doc: WorkflowDoc) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    steps = sum(1 for n in doc.nodes if n.kind == "step")
    if len(doc.nodes) > C.MAX_NODES:
        issues.append(_error("node_limit_exceeded", "graph", count=len(doc.nodes), limit=C.MAX_NODES))
    if len(doc.edges) > C.MAX_EDGES:
        issues.append(_error("edge_limit_exceeded", "graph", count=len(doc.edges), limit=C.MAX_EDGES))
    if steps > C.MAX_STEPS:
        issues.append(_error("step_limit_exceeded", "graph", count=steps, limit=C.MAX_STEPS))
    size = len(doc_to_json(doc).encode("utf-8"))
    if size > C.MAX_DOC_BYTES:
        issues.append(_error("doc_too_large", "graph", bytes=size, limit=C.MAX_DOC_BYTES))
    return issues


def _check_anchors(analysis: Analysis, blank: bool) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not analysis.starts:
        issues.append(_error("no_start", "graph"))
    elif len(analysis.starts) > 1:
        issues.append(_error("multiple_start", "graph", count=len(analysis.starts)))
    if len(analysis.ends) != 1:
        issues.append(_error("no_end", "graph", count=len(analysis.ends)))
    has_step = any(n.kind == "step" for n in analysis.nodes.values())
    if not has_step and not blank:
        issues.append(_error("no_step", "graph"))
    return issues


def _edge_issues(edge: Edge, nodes: dict[str, Node], seen: set[tuple[str, str]]) -> list[ValidationIssue]:
    source, target = nodes.get(edge.source), nodes.get(edge.target)
    if source is None or target is None:
        missing = edge.source if source is None else edge.target
        return [_error("dangling_edge", "edge", edge.id, missing=missing)]
    issues: list[ValidationIssue] = []
    if edge.source == edge.target:
        issues.append(_error("self_loop", "edge", edge.id))
    elif (edge.source, edge.target) in seen:
        issues.append(_error("duplicate_edge", "edge", edge.id))
    seen.add((edge.source, edge.target))
    if "note" in (source.kind, target.kind):
        issues.append(_error("note_has_edge", "edge", edge.id))
    if target.kind == "start":
        issues.append(_error("start_has_input", "edge", edge.id))
    if source.kind == "end":
        issues.append(_error("end_has_output", "edge", edge.id))
    return issues


def _check_edges(doc: WorkflowDoc, analysis: Analysis) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str]] = set()
    for edge in doc.edges:
        issues.extend(_edge_issues(edge, analysis.nodes, seen))
    if analysis.cycle:
        issues.append(_error("cycle", "graph", path=list(analysis.cycle)))
    return issues


def _placeholder_names(text: str) -> list[str]:
    """Names of unescaped `{{name}}` occurrences (`\\{{x}}` is the one escape)."""
    names: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(text):
        name = match.group(2).strip()
        if not match.group(1) and name not in C.PLACEHOLDERS and name not in names:
            names.append(name)
    return names


def _check_step_content(node: Node) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _text(node, "prompt").strip():
        issues.append(_error("empty_prompt", "node", node.id))
    for file in node.config.get("files") or []:
        if file not in C.FILES:
            issues.append(_error("invalid_file", "node", node.id, value=str(file)))
    for section in node.config.get("sections") or []:
        if section not in C.SECTIONS:
            issues.append(_error("invalid_section", "node", node.id, value=str(section)))
    return issues


def _check_node_content(doc: WorkflowDoc, analysis: Analysis) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in doc.nodes:
        if node.kind not in _COMPILED_KINDS:
            continue
        if node.kind == "step":
            issues.extend(_check_step_content(node))
        if analysis.starts and node.id not in analysis.reachable:
            issues.append(_error("unreachable_node", "node", node.id))
        fields = (node.title, _text(node, "prompt"), _text(node, "successCriteria"), _text(node, "checklist"))
        for name in _placeholder_names("\n".join(fields)):
            issues.append(_error("undefined_placeholder", "node", node.id, name=name))
    return issues


def _check_coverage(analysis: Analysis, blank: bool) -> list[ValidationIssue]:
    steps = [n for n in analysis.nodes.values() if n.kind == "step"]
    if blank or not steps:
        return []
    live = [n for n in steps if n.id in analysis.reachable] if analysis.starts else steps
    covered = {s for n in live for s in (n.config.get("sections") or [])}
    return [_error("section_not_covered", "graph", section=s) for s in C.SECTIONS if s not in covered]


def _check_policy(doc: WorkflowDoc, ceilings: Ceilings) -> list[ValidationIssue]:
    policy = doc.policy
    issues: list[ValidationIssue] = []
    turns, minutes = policy.maxTurns, policy.maxDurationMinutes
    if turns is not None and not 1 <= turns <= ceilings.max_turns:
        issues.append(_error("policy_out_of_range", "graph", field="maxTurns", limit=ceilings.max_turns))
    if minutes is not None and not 1 <= minutes <= ceilings.max_duration_minutes:
        issues.append(
            _error("policy_out_of_range", "graph", field="maxDurationMinutes", limit=ceilings.max_duration_minutes)
        )
    return issues


# ── warnings ─────────────────────────────────────────────────────────────────


def _node_warnings(node: Node, analysis: Analysis) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if node.kind == "note":
        if not _text(node, "body").strip():
            issues.append(_warn("orphan_note", "node", node.id))
        return issues
    if node.kind == "step":
        if len(_text(node, "prompt")) > C.LONG_PROMPT:
            issues.append(_warn("long_prompt", "node", node.id, length=len(_text(node, "prompt"))))
        if not _text(node, "successCriteria").strip():
            issues.append(_warn("missing_success_criteria", "node", node.id))
        if not analysis.pred[node.id] and not analysis.succ[node.id]:
            issues.append(_warn("isolated_step", "node", node.id))
    if node.kind == "gate" and not _text(node, "checklist").strip():
        issues.append(_warn("empty_gate", "node", node.id))
    if (
        node.kind in _COMPILED_KINDS
        and analysis.ends
        and node.id in analysis.reachable
        and node.id not in analysis.reaches_end
    ):
        issues.append(_warn("node_not_reaching_end", "node", node.id))
    if len(analysis.succ[node.id]) > C.HIGH_FANOUT:
        issues.append(_warn("high_fanout", "node", node.id, outDegree=len(analysis.succ[node.id])))
    return issues


def _duplicate_titles(doc: WorkflowDoc) -> list[ValidationIssue]:
    groups: dict[str, list[Node]] = {}
    for node in doc.nodes:
        if node.kind in _COMPILED_KINDS and node.title.strip():
            groups.setdefault(node.title.strip(), []).append(node)
    return [
        _warn("duplicate_title", "node", node.id, title=title)
        for title, group in groups.items()
        if len(group) > 1
        for node in group
    ]


def estimated_turns(doc: WorkflowDoc) -> int:
    """Heuristic only (R-W2): 1 turn to read `workflow.md` (RW-13) + effort weights + gates."""
    total = 1
    for node in doc.nodes:
        if node.kind == "step":
            total += C.EFFORT_WEIGHTS[node.config.get("effort") or "normal"]
        elif node.kind == "gate":
            total += C.GATE_WEIGHT
    return total


def effective_max_turns(doc: WorkflowDoc, ceilings: Ceilings) -> int:
    return min(doc.policy.maxTurns or ceilings.max_turns, ceilings.max_turns)


def _budget_warning(doc: WorkflowDoc, ceilings: Ceilings) -> list[ValidationIssue]:
    estimate, limit = estimated_turns(doc), effective_max_turns(doc, ceilings)
    if estimate > C.BUDGET_TIGHT_RATIO * limit:
        return [_warn("budget_tight", "graph", estimated=estimate, maxTurns=limit)]
    return []


def _plan_issues(plan_bytes: int | None) -> list[ValidationIssue]:
    if plan_bytes is None:
        return []
    if plan_bytes > C.MAX_PLAN_BYTES:
        return [_error("plan_too_large", "graph", bytes=plan_bytes, limit=C.MAX_PLAN_BYTES)]
    if plan_bytes > C.WARN_PLAN_BYTES:
        return [_warn("plan_large", "graph", bytes=plan_bytes, limit=C.WARN_PLAN_BYTES)]
    return []


def validate(
    doc: WorkflowDoc,
    *,
    ceilings: Ceilings | None = None,
    plan_bytes: int | None = None,
) -> ValidationReport:
    """Authoritative validation (spec §3.4). `plan_bytes` is the compiled `workflow.md` size when
    the caller has one (`workflow_compiler.validate_with_plan`)."""
    ceilings = ceilings or default_ceilings()
    analysis = analyze(doc)
    blank = is_blank(doc)
    issues = [
        *_check_limits(doc),
        *_check_anchors(analysis, blank),
        *_check_edges(doc, analysis),
        *_check_node_content(doc, analysis),
        *_check_coverage(analysis, blank),
        *_check_policy(doc, ceilings),
        *_plan_issues(plan_bytes),
    ]
    for node in doc.nodes:
        issues.extend(_node_warnings(node, analysis))
    issues.extend(_duplicate_titles(doc))
    issues.extend(_budget_warning(doc, ceilings))
    errors = sum(1 for i in issues if i.severity == "error")
    return ValidationReport(
        issues=issues,
        errorCount=errors,
        warningCount=len(issues) - errors,
        publishable=errors == 0 and not blank,
        blank=blank,
    )


# ── content hash (§3.7, RW-10) ───────────────────────────────────────────────


def _present(value: object) -> bool:
    return value is not None and value != "" and value != []


def content_hash(doc: WorkflowDoc) -> str:
    """`sha1` (first 12 hex) of the canonical content: non-note nodes by id, edges by
    `(source, target)`, policy. Excludes position / viewport / rev / notes, so dragging nodes or
    editing a note never lights up "unpublished changes". `null`/empty optional config keys are
    equivalent to absent."""
    nodes = sorted(
        (
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "config": {k: v for k, v in n.config.items() if _present(v)},
            }
            for n in doc.nodes
            if n.kind != "note"
        ),
        key=lambda item: item["id"],
    )
    edges = sorted(
        ({"source": e.source, "target": e.target, "label": e.label} for e in doc.edges),
        key=lambda item: (item["source"], item["target"]),
    )
    payload = {"nodes": nodes, "edges": edges, "policy": doc.policy.model_dump(mode="json")}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - content id, not security
