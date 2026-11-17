"""Run payloads, stage enum and SSE event models (spec §7.1 / §7.4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "timeout", "budget_exceeded"]

TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "timeout", "budget_exceeded"})
ACTIVE: frozenset[str] = frozenset({"queued", "running", "cancelling"})


class RunStage(StrEnum):
    """Six stages, in order — pct is monotonic (★P1-4)."""

    resolve = "resolve"
    snapshot = "snapshot"
    prepare = "prepare"
    research = "research"
    validate = "validate"
    persist = "persist"


STAGE_ORDER: tuple[str, ...] = tuple(stage.value for stage in RunStage)

#: (pct, chinese label) at the *end* of each stage — drives both the SSE
#: `run.stage` payload and the overlay's progress bar.
STAGE_BOUNDS: dict[str, tuple[int, str]] = {
    "resolve": (5, "解析标的"),
    "snapshot": (15, "抓取行情与财务"),
    "prepare": (20, "组装资料与提示词"),
    "research": (70, "AI 研究与撰写"),
    "validate": (85, "结构校验与接地检查"),
    "persist": (100, "写入版本"),
}

STAGE_START: dict[str, int] = {
    "resolve": 0,
    "snapshot": 5,
    "prepare": 15,
    "research": 20,
    "validate": 70,
    "persist": 85,
}


class RunUsage(BaseModel):
    inputTokens: int | None = None
    outputTokens: int | None = None
    totalTokens: int | None = None
    turns: int | None = None
    toolCalls: int | None = None


class RunOut(BaseModel):
    id: str
    instrumentId: str
    status: RunStatus
    stage: str | None = None
    pct: int = 0
    startedAt: str
    finishedAt: str | None = None
    exitCode: int | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    reportId: str | None = None
    costUsd: float | None = None
    costKnown: bool = False
    usage: RunUsage = Field(default_factory=RunUsage)
    # ── ★v7（§20.5.1，8 个新字段）。响应模型是**第二道门**：漏了等于没实现
    #    （`to_run_out` 会 `TypeError`，pydantic 会静默丢弃）。
    #: 本次 run 观察到多少次 CLI 重试（`run.retry` 的本地累计）。
    retryCount: int = 0
    #: `result.structured.attempts`——CLI 侧已用掉的修复轮。
    structuredAttempts: int | None = None
    #: 真正干活的 provider/model（来自 `system` 事件；没有就空）。
    agentProvider: str | None = None
    agentModel: str | None = None
    #: `reported | estimated | unknown`——成本三态。
    costSource: str | None = None
    #: 估算金额（`costSource === 'estimated'` 时才有值）。
    costUsdEst: float | None = None
    #: 同一根因的指纹（12 位）；`>= 3` 次时可换成更强措辞。
    failureFingerprint: str | None = None
    #: 服务端下发的一句"下一步做什么"（前端不拼文案，H-12）。
    hint: str | None = None


class RunEventOut(BaseModel):
    seq: int
    runId: str
    type: str
    payload: dict[str, Any]
    createdAt: str


class RunListResponse(BaseModel):
    items: list[RunOut]
    total: int


class CancelResponse(BaseModel):
    status: Literal["cancelling", "cancelled"]
    runId: str


class EventsTokenResponse(BaseModel):
    token: str
    expiresIn: int = 60


class StagesResponse(BaseModel):
    stages: list[dict[str, Any]]


def stage_label(stage: str) -> str:
    return STAGE_BOUNDS.get(stage, (0, stage))[1]


def stage_end_pct(stage: str) -> int:
    return STAGE_BOUNDS.get(stage, (0, ""))[0]
