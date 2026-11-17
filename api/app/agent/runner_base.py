"""Runner protocol and the event/result dataclasses shared by both engines."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class AgentEventType(StrEnum):
    stage = "agent.stage"
    tool = "agent.tool"
    text = "agent.text"
    progress = "agent.progress"
    log = "agent.log"
    #: ★v7 §20.3.4 / H3：CLI 的 `retry` 事件（429 / 5xx / 网络错误的带抖动退避）。
    #: `agent.*` 是 CLI 的词汇，转发到 `run_events` 时改名 `run.retry`。
    retry = "agent.retry"


@dataclass(slots=True)
class AgentEvent:
    """One chunk of a running generation, forwarded to the SSE stream."""

    type: AgentEventType
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    turns: int | None = None
    tool_calls: int | None = None

    def as_payload(self) -> dict[str, int | None]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "turns": self.turns,
            "toolCalls": self.tool_calls,
        }


@dataclass(slots=True)
class AgentResult:
    """Whatever the attempt produced — including a readable fallback.

    `doc` is a schema-valid report document (real, mock, or provisional);
    `text` is the model's own last message, kept so a failed attempt can still
    be shown to the user (★P1-10).
    """

    doc: dict[str, Any] | None = None
    text: str = ""
    usage: AgentUsage = field(default_factory=AgentUsage)
    cost_usd: float | None = None
    cost_known: bool = False
    exit_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider: str | None = None
    model: str | None = None
    source: str = "aragon"
    ndjson_path: str | None = None
    #: ★v7 §20.3.7 / H6：`system` 事件里的 `model` 三元组
    #: `{provider, model, baseUrl}`。**没有就留 None，不猜**：`baseUrl` 不在
    #: `aragon info --json` 里（E-39），它只出现在 `exec` 的 system 事件里。
    engine_info: dict[str, Any] | None = None
    #: ★v7 §20.3.4：本地累计的 `retry` 事件数。**不能用 `attempt` 当累计**
    #: （实测同一进程里 attempt 会从 1 重新开始），所以由 runner/local 自增。
    retries: int = 0
    #: `result.structured.attempts`（CLI 侧已用掉的修复轮），失败时也带出来。
    structured_attempts: int | None = None

    @property
    def ok(self) -> bool:
        return self.doc is not None and self.error_code is None


@dataclass(slots=True)
class AgentRequest:
    """Everything a runner needs, assembled by `run_manager` / `snapshot`."""

    run_id: str
    workspace: Path
    prompt_path: Path
    system_prompt_path: Path
    schema_path: Path
    instrument_symbol: str
    instrument_name: str
    max_turns: int = 40
    max_duration_ms: int = 900_000
    #: ★实施发现（I-60）：`schema_path` 交给 CLI 的那份（工作区副本）；这里是它的
    #: **真源**，`build_argv()` 拿它做字节身份断言（`schema_drift`）。
    #: 设计里这道闸门被写成"与 `schemas_dir/report_doc.schema.json` 相比"——但 `argv`
    #: 契约是生成与 deep ask **共用**的（裁决 H-2），而 ask 交的是 `ask.schema.json`
    #: （943B）。硬编码真源会让每一次深度追问都判成漂移。所以真源是**入参**：
    #: 生成传 report_doc，追问传 ask，`None` 表示这条路径不做身份断言。
    schema_source_path: Path | None = None


@runtime_checkable
class AgentRunner(Protocol):
    name: str
    source: str

    def stream(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def run(self, request: AgentRequest, on_event: Callable[[AgentEvent], None] | None = None) -> AgentResult: ...

    async def cancel(self) -> None: ...


class AgentError(Exception):
    """Raised when the runner cannot even start (capability, spawn, config)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
