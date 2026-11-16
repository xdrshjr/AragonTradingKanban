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
