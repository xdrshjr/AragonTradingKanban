"""Schema / 资料预算闸门 —— 让"先天失败"在 spawn **之前**发生（spec §20.3.3，H2 / M19）。

本轮的三道闸门都住在这个模块里，全部是纯函数（只读文件，不 spawn、不写盘）：

1. `measure()` + `limits_from_capability()` —— `report_doc.schema.json` 当前
   18392 B / depth 11 / nodes 658，而 CLI 的硬上限是 `schemaDepth 12` / `schemaBytes
   65536` / `validationNodes 5000`（E-39 / E-46）。**深度余量只有 1 层**：任何一次
   "给块再加一层嵌套"都会让每一次生成在 20 秒后以 exit 2 `result_schema_too_deep`
   结束，而那条消息过去会被分类成 `usage_error`（"参数错误"）。历史 run `06456fcb`
   就是这么死的。
2. `assert_schema_identity()` —— 交给 CLI 的那份 `workspace/schema.json` 与真源
   `schemas/report_doc.schema.json` 必须**字节一致**。两者不一致时会出现最坏的一种
   失败：CLI 接受了报告（收了你 18 万 tokens 的钱），Python 侧拒绝并丢掉它。
   实测 E-55：现存 34 份历史 `workspace/schema.json` 与真源相比 **0 份**相同。
3. `assert_prompt_budget()` —— `prompt.md + system.md + schema.json` 的合计字节数。
   现在 2.5 + 2.2 + 19 KB ≈ 24 KB，但没有任何东西阻止它们长大；把 47.6 KB 的
   `bars.csv` 内联进 prompt 是很容易顺手做的一件事，而它是本项目最贵的一个动作。

**为什么唯一实现在 `agent/` 而不是 `services/`**（★v8 V8-P0-1 / 裁决 H-17）：
`agent/` 不得 import `services/`（`test_boundaries::test_agent_never_imports_services`
用 AST 与字面量同时拦），而 `build_argv()` 必须在 spawn 前再调一次同一份断言。
把实现放在 `services/snapshot.py` 而在这里调用，只能二选一：import（CI 红）或者
两处各写一份（双源，与本轮"单一真源"主线正面冲突）。**一份实现 + 两个调用点**。
`services → agent` 是合法方向，与 `agent/fingerprint.py` 把 `schema_bytes` 作为入参
拿到手是同一个模式。

**`depth` 的口径写死，否则闸门差一层**（★v8 V8-P2-11）：根记为 1，**dict / list 与
它们的标量叶子各算一层**（`{"type": "string"}` 这个值本身算一层）。实测 E-52：
合成 schema 容器层数 12 → CLI 报 `nests 13 levels deep; the limit is 12` 并 exit 2
（3.8 s，**没有产生任何模型调用**）；容器 11 层 → 放行。也就是说 CLI 的 `nests` =
容器层数 **+ 1**，与"标量叶子也算一层"完全吻合。真源按此口径 = 11（CLI 也为它放行，
E-53），naive 的"只数容器"DFS 得 **10** —— 少一层就意味着闸门会放行一个 CLI 必拒的
schema。判据同样是 `depth > max_depth` 才拒绝（等于放行），与实测的 `nests > 12` 一致。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .preflight import AragonCapability
from .runner_base import AgentError, AgentRequest

#: `aragon info --json` 缺失/不完整时的兜底上限。**永不回退成"不限"**：
#: 一个"不限"的闸门在最需要的时刻（有人把 schema 撑到 200KB）恰好是失效的。
FALLBACK_LIMITS: dict[str, int] = {
    "schemaBytes": 65536,
    "schemaDepth": 12,
    "validationNodes": 5000,
}

#: `prompt.md + system.md + schema.json` 的默认上限（`ARAGON_MAX_PROMPT_BYTES`）。
DEFAULT_MAX_PROMPT_BYTES = 32_768


@dataclass(frozen=True, slots=True)
class SchemaBudget:
    """一次测量结果。`ok` 与 `reason` 都由同一组数字派生，没有第二份判定。"""

    path: str
    bytes: int
    depth: int
    nodes: int
    max_bytes: int
    max_depth: int
    max_nodes: int

    @property
    def ok(self) -> bool:
        """三项全部不越界才算通过。`>` 而不是 `>=`：等于上限是放行（E-52）。"""
        return self.bytes <= self.max_bytes and self.depth <= self.max_depth and self.nodes <= self.max_nodes

    @property
    def headroom(self) -> int:
        """深度余量（当前真源 = 1）。负值原样返回——它本身就是"超了"的度量。"""
        return self.max_depth - self.depth

    def reason(self) -> str | None:
        """一句中文短句，直接进 `AgentError.message`（`run.error` 会显示它）。"""
        problems: list[str] = []
        if self.depth > self.max_depth:
            problems.append(f"深度 {self.depth} > 上限 {self.max_depth}")
        if self.bytes > self.max_bytes:
            problems.append(f"体积 {self.bytes}B > 上限 {self.max_bytes}B")
        if self.nodes > self.max_nodes:
            problems.append(f"校验节点 {self.nodes} > 上限 {self.max_nodes}")
        return "；".join(problems) if problems else None

    def as_payload(self) -> dict[str, Any]:
        """落进 `workspace/schema_budget.json` 与 `capabilities.engine.schema`。"""
        return {
            "bytes": self.bytes,
            "depth": self.depth,
            "nodes": self.nodes,
            "maxBytes": self.max_bytes,
            "maxDepth": self.max_depth,
            "maxNodes": self.max_nodes,
            "headroom": self.headroom,
            "ok": self.ok,
        }


def _walk(value: Any) -> tuple[int, int]:
    """一次 DFS 同时算 `(depth, nodes)`。

    一次序遍历而不是"递归求 max 两次"：`depth` 与 `nodes` 必须是**同一棵树**的
    两个数，否则两个判据可以互相矛盾（一个说放行、一个说超标）。

    口径（V8-P2-11）：标量叶子算一层；容器（dict / list）在子项中最深者之上加一。
    空容器 `{}` / `[]` 也占一层。
    """
    if isinstance(value, dict):
        children = list(value.values())
    elif isinstance(value, list):
        children = list(value)
    else:
        return 1, 1
    if not children:
        return 1, 1
    depth = 0
    nodes = 1
    for child in children:
        child_depth, child_nodes = _walk(child)
        depth = max(depth, child_depth)
        nodes += child_nodes
    return depth + 1, nodes


def measure(path: Path, limits: dict[str, int] | None = None) -> SchemaBudget:
    """读文件 → 一次 DFS → `SchemaBudget`。

    文件读不到时抛 `AgentError("schema_over_budget")`：**"读不到"绝不能变成"通过"**。
    曾经这里写的是"不抛"，但那样一来 `bytes=0 / depth=0 / nodes=0` 三项全部不越界，
    闸门会放行一个根本不存在的 schema——一个在磁盘坏掉时恰好失效的闸门（R-37 的同型）。
    调用方若只想展示（`/capabilities`），自己 try/except 降级。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AgentError("schema_over_budget", f"读不到 schema 文件 {path}：{exc}") from exc
    resolved = {**FALLBACK_LIMITS, **(limits or {})}
    depth = 0
    nodes = 0
    try:
        depth, nodes = _walk(json.loads(raw))
    except json.JSONDecodeError:
        # 坏 JSON：CLI 会在 spawn 时炸（exit 2），但把 depth/nodes 记成 0 会让闸门
        # 放行。体积仍然是真实测量值，三项里至少有两项是诚实的 0——由 `ok` 之外
        # 的调用方（`build_argv`）先行断言可解析性。
        depth, nodes = 0, 0
    return SchemaBudget(
        path=str(path),
        bytes=len(raw),
        depth=depth,
        nodes=nodes,
        max_bytes=int(resolved["schemaBytes"]),
        max_depth=int(resolved["schemaDepth"]),
        max_nodes=int(resolved["validationNodes"]),
    )


def limits_from_capability(capability: AragonCapability | None) -> dict[str, int]:
    """优先读 `capability.result`（preflight 把 `info.result` 原样存下）。

    任何一项缺失就回退 fallback ——**不回退成"不限"**；缺失本身值得一条 warning，
    因为那说明 preflight 的 payload 变了（CLI 升级的早期信号）。
    """
    payload = (capability.result if capability is not None else None) or {}
    limits: dict[str, int] = {}
    missing: list[str] = []
    for key, fallback in FALLBACK_LIMITS.items():
        value = payload.get(key)
        if isinstance(value, int) and value > 0:
            limits[key] = value
        else:
            limits[key] = fallback
            missing.append(key)
    if missing:
        from ..logging import get_logger

        get_logger("schema_budget").warning(
            "capability.result 缺少 %s，按兜底上限判定：%s", ", ".join(missing), limits
        )
    return limits


def assert_schema_identity(workspace_schema: Path, source_schema: Path) -> str:
    """两份 schema 必须**字节一致**，否则生成会在 validate 阶段丢掉一份已付费的报告。

    返回真源的 sha1（12 位十六进制），供 `schema_budget.json` 与
    `quality.engine.schemaSha1` 记账——事后任何一次"CLI 接受、Python 拒绝"都能对上号。

    两个调用点、一份实现（裁决 H-17）：
    `services/snapshot.write_schema_copy()` 写完副本后立刻比一次（快速失败），
    `agent/aragon_runner.build_argv()` 再比一次（spawn 前最后一道）。
    **故意是硬失败**（R-37）：白烧一轮 18 万 tokens 比"多改一次文件"贵得多，
    所以错误消息里直接写出两份文件的路径与 sha1。
    """
    source_bytes = source_schema.read_bytes()
    source_sha = hashlib.sha1(source_bytes).hexdigest()
    try:
        workspace_bytes = workspace_schema.read_bytes()
    except OSError as exc:
        raise AgentError(
            "schema_drift",
            f"读不到工作区 schema（{workspace_schema}）：{exc}",
        ) from exc
    workspace_sha = hashlib.sha1(workspace_bytes).hexdigest()
    if workspace_sha != source_sha:
        raise AgentError(
            "schema_drift",
            (
                "workspace/schema.json 与 schemas/report_doc.schema.json 字节不一致："
                f"{workspace_schema} ({len(workspace_bytes)}B / sha1 {workspace_sha[:12]}) ≠ "
                f"{source_schema} ({len(source_bytes)}B / sha1 {source_sha[:12]})"
            ),
        )
    return source_sha[:12]


def prompt_total_bytes(request: AgentRequest) -> int:
    """`prompt.md + system.md + schema.json` 的合计字节数（纯测量，**不判定**）。

    判定在 `assert_prompt_budget()`；这里单独存在是为了让 `schema_budget.json`
    能记下**当时的数字**——闸门拦下的那一次最需要它。
    """
    total = 0
    for path in (request.prompt_path, request.system_prompt_path, request.schema_path):
        try:
            total += path.stat().st_size
        except OSError as exc:
            raise AgentError("prompt_over_budget", f"读不到资料文件 {path}：{exc}") from exc
    return total


def assert_prompt_budget(request: AgentRequest, max_bytes: int) -> int:
    """`prompt.md + system.md + schema.json` 合计不得超过 `max_bytes`。

    返回合计字节数，供 `schema_budget.json` 记账。上限 `<= 0` 视为"关闭"（运维出口），
    其余情况一律硬失败：提示词膨胀是**每一次**生成都多花钱，而这一次会被拦下。
    """
    total = prompt_total_bytes(request)
    if max_bytes and max_bytes > 0 and total > max_bytes:
        raise AgentError(
            "prompt_over_budget",
            f"提示词与 schema 合计 {total}B 超出上限 {max_bytes}B（prompt + system + schema）",
        )
    return total


__all__ = [
    "DEFAULT_MAX_PROMPT_BYTES",
    "FALLBACK_LIMITS",
    "SchemaBudget",
    "assert_prompt_budget",
    "assert_schema_identity",
    "limits_from_capability",
    "measure",
    "prompt_total_bytes",
]
