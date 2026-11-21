"""The workspace the assistant reads (spec §3.8.3 step 3 / §3.8.5).

`graph.json` · `validation.json` · `contract.md` · `example.json` · `history.json` · `request.json`
(`schema.json` and the system prompt are copied byte-for-byte by `agent/assist_runner`, I-58).

`contract.md` is **generated from `contracts/workflow.json`** (never hand-written, so it cannot
drift from what the validator enforces). `graph.json` drops `position` / `viewport` (the model must
not reason about coordinates) but keeps ids and revs; over 24 KB every prompt is cut to 400 chars
and the file says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..schemas.workflow import ValidationReport, WorkflowDoc
from . import workflow_contract as C
from .workflow_builtin import builtin_doc

GRAPH_BYTES_SOFT_LIMIT = 24 * 1024
PROMPT_CUT = 400
HISTORY_KEPT = 20


def graph_payload(doc: WorkflowDoc, *, truncate: bool = False) -> dict[str, Any]:
    """The draft as the model sees it (no coordinates, no viewport)."""
    nodes = []
    for node in doc.nodes:
        config = dict(node.config)
        if truncate and isinstance(config.get("prompt"), str) and len(config["prompt"]) > PROMPT_CUT:
            config["prompt"] = config["prompt"][:PROMPT_CUT] + "…（已截断）"
        nodes.append({"id": node.id, "kind": node.kind, "title": node.title, "rev": node.rev, "config": config})
    edges = [{"id": e.id, "source": e.source, "target": e.target, "label": e.label, "rev": e.rev} for e in doc.edges]
    return {"nodes": nodes, "edges": edges, "policy": doc.policy.model_dump()}


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def graph_json(doc: WorkflowDoc) -> str:
    text = _dump(graph_payload(doc))
    if len(text.encode("utf-8")) > GRAPH_BYTES_SOFT_LIMIT:
        text = _dump({**graph_payload(doc, truncate=True), "note": "文档较大，各步骤提示词已截断为前 400 字"})
    return text


def contract_md() -> str:
    """Rules of the game, rendered from the shared contract."""
    sections = "\n".join(f"- `{item['id']}`：{item['label']}" for item in C.load_contract()["sections"])
    files = "\n".join(f"- `{item['name']}`：{item['label']}（{item['hint']}）" for item in C.load_contract()["files"])
    placeholders = "、".join(f"`{{{{{name}}}}}`" for name in C.PLACEHOLDERS)
    return "\n".join(
        [
            "# 研究流程规则（由代码生成）",
            "",
            "## 节点",
            "- `step` 步骤：必填 `prompt`（做什么**并**产出什么）；`role` ∈ "
            + "/".join(C.ROLES)
            + "；`effort` ∈ "
            + "/".join(C.EFFORTS)
            + "；可带 `files`、`sections`、`successCriteria`。",
            "- `gate` 检查点：上游全部完成后才继续，可带 `checklist`。",
            "- `note` 注释：不参与执行，不能连线。`start` / `end` 是锚点，不可增删。",
            "",
            "## 固定的五个章节（必须全部被至少一个可达步骤的 `sections` 覆盖）",
            sections,
            "",
            "## 资料文件（`files` 只能取这些）",
            files,
            "",
            f"## 占位符（只能用这些）\n{placeholders}",
            "",
            "## 进度标记协议",
            "Agent 执行时输出 `[[wf:S1:start]]` / `[[wf:S1:done]]` / `[[wf:S3:skip 原因]]`；",
            "它只是观测信号，不是闸门。",
            "",
            "## 限额",
            f"节点 ≤ {C.MAX_NODES}，步骤 ≤ {C.MAX_STEPS}，连线 ≤ {C.MAX_EDGES}；标题 ≤ {C.TITLE_MAX} 字，"
            f"prompt ≤ {C.PROMPT_MAX} 字，完成标准 ≤ {C.CRITERIA_MAX} 字，检查清单 ≤ {C.CHECKLIST_MAX} 字。",
            "",
            "## policy",
            "`maxTurns` / `maxDurationMinutes` 为 null = 沿用全站值，填数字只能**收紧**。",
            "",
            f"## 你的输出\n一批不超过 {C.ASSIST_MAX_OPS} 个操作；你不能发布、不能设为默认。",
            "",
        ]
    )


def example_json() -> str:
    return _dump(graph_payload(builtin_doc()))


def write_context(
    workspace: Path,
    *,
    doc: WorkflowDoc,
    report: ValidationReport,
    history: list[dict[str, str]],
    request: dict[str, Any],
) -> None:
    """Rebuild the whole workspace for one assist (idempotent; round 2 calls it again)."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "graph.json").write_text(graph_json(doc), encoding="utf-8")
    (workspace / "validation.json").write_text(_dump(report.model_dump(mode="json")), encoding="utf-8")
    (workspace / "contract.md").write_text(contract_md(), encoding="utf-8")
    (workspace / "example.json").write_text(example_json(), encoding="utf-8")
    (workspace / "history.json").write_text(_dump(history[-HISTORY_KEPT:]), encoding="utf-8")
    (workspace / "request.json").write_text(_dump(request), encoding="utf-8")
