"""Text rendering of a compiled research workflow (spec §3.5.3 / §3.5.4 / appendix B).

Split out of `workflow_compiler.py` to keep each file small: this module only turns already
validated, already interpolated data into the Markdown the agent reads (`workflow.md`) and the
≤2 KB signpost injected into `prompt.md`. Pure functions, no I/O, no clock.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from . import workflow_contract as C

ROLE_LABELS = {
    "data": "数据核对",
    "analysis": "分析研判",
    "synthesis": "综合归纳",
    "review": "复核自检",
    "custom": "自定义",
}
DEFAULT_GATE_CHECKLIST = "确认上游结论互相一致、数字口径统一。"
_ELLIPSIS_TRAILER = "…共 {steps} 步 / {gates} 个检查点，详见 workflow.md"
_FULL_TRAILER = "共 {steps} 步 / {gates} 个检查点（详见 workflow.md）"


@dataclass(frozen=True)
class PlanItem:
    """One reportable entry of the plan (a step or a gate)."""

    key: str  # "S1".."Sn" / "G1".."Gm"
    node_id: str
    kind: Literal["step", "gate"]
    title: str
    role: str | None
    level: int  # 1-based phase number
    upstream: tuple[str, ...]  # direct upstream keys (start does not count)
    sections: tuple[str, ...]
    files: tuple[str, ...]


@dataclass(frozen=True)
class PlanEntry:
    """A `PlanItem` plus the interpolated free text an admin wrote for it."""

    item: PlanItem
    goal: str = ""
    criteria: str = ""
    checklist: str = ""


@dataclass(frozen=True)
class PlanSpec:
    template_name: str
    version: int | None
    markers: bool
    entries: tuple[PlanEntry, ...]
    levels: tuple[tuple[str, ...], ...]
    coverage: dict[str, tuple[str, ...]] = field(default_factory=dict)


def version_label(version: int | None) -> str:
    return f"v{version}" if version is not None else "草稿"


def _comment_safe(text: str) -> str:
    """The name sits in an HTML comment: it must not be able to close it."""
    return text.replace("--", "—").replace("<", "＜").replace(">", "＞")


def _bullet(label: str, value: str) -> str:
    return f"- {label}：" + value.strip().replace("\n", "\n  ")


def _join_keys(keys: tuple[str, ...]) -> str:
    return "、".join(keys) if keys else "—"


def _protocol(markers: bool) -> list[str]:
    if not markers:
        return ["## 进度汇报协议", "本流程**不要求**输出进度标记，按阶段与步骤研究即可。", ""]
    return [
        "## 进度汇报协议",
        "每开始 / 完成一个步骤或检查点，单独占一行输出标记（标记不会展示给读者，也不需要解释）：",
        "`[[wf:S1:start]]`  `[[wf:S1:done]]`  `[[wf:S3:skip 数据不足，已在 summary 说明]]`",
        "同一阶段内的步骤可以任意顺序、穿插完成；检查点必须在其上游全部 done/skip 后再 start。",
        "标记只是进度信号——**漏标不会让生成失败，但会降低流程遵循度的记录**。",
        "",
    ]


def _item_block(entry: PlanEntry) -> list[str]:
    item = entry.item
    if item.kind == "gate":
        waits = _join_keys(item.upstream) if item.upstream else "无前置步骤"
        head = f"### {item.key} · 检查点：{item.title}  （等待 {waits}）"
        return [head, _bullet("检查清单", entry.checklist or DEFAULT_GATE_CHECKLIST)]
    role = ROLE_LABELS.get(item.role or "")
    head = f"### {item.key} · {item.title}" + (f"  〔{role}〕" if role else "")
    lines = [head, _bullet("目标与产出", entry.goal)]
    if item.files:
        lines.append(_bullet("重点资料", "、".join(f"`{name}`" for name in item.files)))
    if item.sections:
        lines.append(_bullet("服务章节", "、".join(item.sections)))
    if entry.criteria:
        lines.append(_bullet("完成标准", entry.criteria))
    lines.append(_bullet("上游", _join_keys(item.upstream)))
    return lines


def _phases(spec: PlanSpec) -> list[str]:
    by_key = {entry.item.key: entry for entry in spec.entries}
    lines: list[str] = []
    for number, keys in enumerate(spec.levels, start=1):
        lines.append(f"## 阶段 {number}")
        if len(keys) > 1:
            lines.append("（本阶段内的步骤互不依赖，可任意顺序、可穿插完成）")
        for key in keys:
            lines.extend(_item_block(by_key[key]))
        lines.append("")
    return lines


def _coverage(spec: PlanSpec) -> list[str]:
    parts = [f"{section} ← {_join_keys(spec.coverage.get(section, ()))}" for section in C.SECTIONS]
    return ["## 章节覆盖", "　".join(parts), ""]


def _preface(spec: PlanSpec) -> list[str]:
    title = f"# 研究流程：{spec.template_name}（{version_label(spec.version)}）"
    return [
        title,
        "",
        "本流程由管理员配置，规定**研究的过程**：先做什么、依据哪些资料、每一步做到什么程度才算完成。",
        "它**不能**改变：事实边界（只用资料文件里的数字）、来源标注规则、输出 JSON Schema、提交前自检清单。",
        "若与系统提示词冲突，**以系统提示词为准**。",
        "",
    ]


def render_body(spec: PlanSpec) -> str:
    """Everything after the header comment (the sha1 in the header is computed over this)."""
    lines = [
        *_preface(spec),
        *_protocol(spec.markers),
        *_phases(spec),
        *_coverage(spec),
        "## 收尾",
        "全部步骤与检查点 done/skip 后，**只调用一次** `submit_result` 提交完整报告文档。",
        "",
    ]
    return "\n".join(lines)


def render_plan(spec: PlanSpec) -> tuple[str, str]:
    """`(plan_md, sha1)`; the hash covers the body, so the header can carry it without a cycle."""
    body = render_body(spec)
    sha1 = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - content id
    name = _comment_safe(spec.template_name)
    header = f"<!-- aragon-workflow v1 · {name} · {version_label(spec.version)} · sha1 {sha1} -->\n"
    return header + body, sha1


def render_inline_index(spec: PlanSpec) -> str:
    """The signpost for `prompt.md`: never the admin's prompt text, always ≤ `INLINE_INDEX_MAX_BYTES`
    (measured in UTF-8 bytes — a Chinese character is three)."""
    steps = sum(1 for e in spec.entries if e.item.kind == "step")
    gates = len(spec.entries) - steps
    name = f"{spec.template_name} {version_label(spec.version)}"
    marker_hint = "，并输出进度标记 `[[wf:<键>:start|done|skip]]`" if spec.markers else ""
    head = [
        f"# 研究流程（管理员配置：{name}）",
        f"**第一步，先完整读取 `workflow.md`**，之后严格按它的阶段与步骤研究{marker_hint}。",
    ]
    labels = [f"{e.item.key} {e.item.title}" for e in spec.entries]
    full = "\n".join([*head, " → ".join(labels) + "\n" + _FULL_TRAILER.format(steps=steps, gates=gates)])
    if len(full.encode("utf-8")) <= C.INLINE_INDEX_MAX_BYTES:
        return full
    trailer = _ELLIPSIS_TRAILER.format(steps=steps, gates=gates)
    kept = list(labels)
    while kept:
        text = "\n".join([*head, " → ".join(kept) + " " + trailer])
        if len(text.encode("utf-8")) <= C.INLINE_INDEX_MAX_BYTES:
            return text
        kept.pop()
    return "\n".join([*head, trailer])
