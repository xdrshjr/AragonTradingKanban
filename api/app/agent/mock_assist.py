"""Deterministic stand-in for the AI workflow assistant (`AGENT_MODE=mock`, §3.8.7).

Never spawns anything. It reads `request.json` (message, focus ids, repair flag) and `graph.json`
from the workspace and answers from a few Chinese keyword rules, so the demo, the E2E suite and
the unit tests need no network:

* "整理布局 / 自动布局"              -> `auto_layout`
* "删除 / 删掉 <节点名>"              -> `delete_node`
* "加 / 新增 / 添加 …步骤 [和检查点]" -> a new step (+ gate) spliced in before "在<X>前" (default: the end)

It speaks the **model's** dialect (`ref`, `near`, `@ref`, no ids of its own), exactly like a real
engine, so the whole expand → apply → validate → commit path is exercised.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from .runner_base import AgentEvent, AgentEventType, AgentRequest, AgentResult, AgentUsage

_ADD_RE = re.compile(r"(?:加|新增|添加|增加)(?:一个|一步|个)?\s*([^，,。步检在]{0,20}?)\s*(步骤|检查点)")
_BEFORE_RE = re.compile(r"在\s*([^，,。前]{1,20}?)\s*(?:之)?前")
_DELETE_RE = re.compile(r"(?:删除|删掉|移除)\s*(?:步骤|节点|检查点)?\s*[「『\"“]?([^」』\"”，,。]{1,20})")


def _load(workspace: Path, name: str) -> dict[str, Any]:
    try:
        return json.loads((workspace / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _find_node(nodes: list[dict[str, Any]], needle: str, kinds: tuple[str, ...]) -> dict[str, Any] | None:
    needle = needle.strip()
    if not needle:
        return None
    for node in nodes:
        if node.get("kind") in kinds and needle in str(node.get("title", "")):
            return node
    return None


def _predecessors(edges: list[dict[str, Any]], node_id: str) -> list[str]:
    return [edge["source"] for edge in edges if edge.get("target") == node_id]


def _step_op(title: str, before: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "add_node",
        "ref": "s_new",
        "kind": "step",
        "title": title[:40],
        "near": before["id"],
        "config": {
            "role": "analysis",
            "prompt": f"围绕「{title}」展开研究，只使用资料文件中的数字；产出：一段可供后续步骤引用的{title}结论。",
            "files": ["snapshot.json", "fundamentals.md"],
            "sections": list((before.get("config") or {}).get("sections") or ["valuation"]),
            "successCriteria": f"已给出{title}的结论并标明依据与口径。",
            "effort": "normal",
        },
    }


def _plan_add(message: str, graph: dict[str, Any]) -> dict[str, Any] | None:
    match = _ADD_RE.search(message)
    if match is None:
        return None
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    title = (match.group(1) or "").strip() or "新增研究"
    anchor = _BEFORE_RE.search(message)
    before = _find_node(nodes, anchor.group(1), ("step", "gate")) if anchor else None
    before = before or next((n for n in nodes if n.get("kind") == "end"), None)
    if before is None:
        return None
    wants_gate = "检查点" in message
    ops: list[dict[str, Any]] = [_step_op(title, before)]
    if wants_gate:
        ops.append(
            {
                "op": "add_node",
                "ref": "g_new",
                "kind": "gate",
                "title": f"{title}核对"[:40],
                "near": before["id"],
                "config": {"checklist": f"{title}的结论是否与上游一致？数字口径是否统一？"},
            }
        )
    for source in _predecessors(edges, before["id"]):
        ops.append({"op": "delete_edge", "source": source, "target": before["id"]})
        ops.append({"op": "add_edge", "source": source, "target": "@s_new"})
    tail = "@s_new"
    if wants_gate:
        ops.append({"op": "add_edge", "source": "@s_new", "target": "@g_new"})
        tail = "@g_new"
    ops.append({"op": "add_edge", "source": tail, "target": before["id"]})
    added = "1 个步骤和 1 个检查点" if wants_gate else "1 个步骤"
    reply = f"已在「{before.get('title', '')}」之前加入{added}（{title}），并接好连线。请审阅提示词后再发布。"
    return {"reply": reply, "ops": ops}


def _plan_delete(message: str, graph: dict[str, Any]) -> dict[str, Any] | None:
    match = _DELETE_RE.search(message)
    node = _find_node(graph.get("nodes", []), match.group(1), ("step", "gate", "note")) if match else None
    if node is None:
        return None
    return {"reply": f"已删除「{node['title']}」及其连线。", "ops": [{"op": "delete_node", "id": node["id"]}]}


def plan(message: str, graph: dict[str, Any]) -> dict[str, Any]:
    """The whole mock brain: message + graph -> `{reply, ops, openQuestions}`."""
    if re.search(r"整理布局|自动布局|排版", message):
        return {"reply": "已按层级整理布局。", "ops": [{"op": "auto_layout"}], "openQuestions": []}
    for planner in (_plan_delete, _plan_add):
        planned = planner(message, graph)
        if planned is not None:
            return {**planned, "openQuestions": []}
    return {
        "reply": "我没能确定要改哪里。",
        "ops": [],
        "openQuestions": ["想加一个步骤、删除某个节点，还是整理布局？可以说得更具体一些。"],
    }


class MockAssistRunner:
    name = "mock-assist"
    source = "mock"

    def __init__(self) -> None:
        self._cancelled = False

    async def cancel(self) -> None:
        self._cancelled = True

    async def stream(
        self, request: AgentRequest, on_event: Callable[[AgentEvent], None] | None = None
    ) -> AsyncIterator[AgentEvent]:  # pragma: no cover - the assist pipeline uses `run`
        result = await self.run(request, on_event)
        yield AgentEvent(AgentEventType.text, {"delta": result.text})

    async def run(self, request: AgentRequest, on_event: Callable[[AgentEvent], None] | None = None) -> AgentResult:
        body = _load(request.workspace, "request.json")
        graph = _load(request.workspace, "graph.json")
        if on_event is not None:
            on_event(AgentEvent(AgentEventType.text, {"delta": "正在阅读当前流程…"}))
        await asyncio.sleep(0)
        if self._cancelled:
            return AgentResult(error_code="cancelled", error_message="已取消", source=self.source)
        doc = plan(str(body.get("message", "")), graph)
        if body.get("repair"):
            doc = {"reply": "上一轮的修改无法应用，本次不再改动流程。", "ops": [], "openQuestions": []}
        return AgentResult(
            doc=doc,
            text=doc["reply"],
            usage=AgentUsage(input_tokens=1200, output_tokens=300, total_tokens=1500, turns=1, tool_calls=1),
            source=self.source,
            exit_code=0,
        )
