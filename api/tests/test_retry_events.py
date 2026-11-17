"""`retry` 事件：累计计数不重置，且 SSE 与 `run_events` 同名同形（spec §20.3.4 / M20）。

三条刻意的性质：

* **`attempt` 会重置**（E-45 实测：同一进程里出现过 `attempt:1,2` 然后又是 `1..6`），
  所以累计值必须是本地自增。拿 `attempt` 当累计会少报一半重试，而"重试了 8 次"
  与"重试了 3 次"对用户意味着完全不同的事。
* **`agent.retry` → `run.retry`**：`agent.*` 是 CLI 的词汇，`run.*` 是 run 的词汇
  （v3 已经这样分过一次，`web/lib/sse.ts` 里那张表就是证据）。
* **SSE 与 `run_events` 载荷逐字段相等**：两条读路径必须看到同一份事实，否则
  "重连之后计数丢了"这类缺陷只在一个客户端上出现。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.aragon_runner import AragonRunner
from app.agent.preflight import AragonCapability
from app.agent.runner_base import AgentEvent, AgentEventType
from app.models import Run, RunEvent
from app.services import run_manager as run_manager_module

ALL_TOOLS = ["read_file", "glob", "grep", "list_dir", "submit_result"]


def capability() -> AragonCapability:
    return AragonCapability(
        available=True,
        cli="0.6.68",
        versionOk=True,
        provider="openai",
        model="deepseek-flash",
        hasApiKey=True,
        ajv=True,
        canStructured=True,
        hasSubmitResult=True,
        extraAllowTools=list(ALL_TOOLS),
        tools=list(ALL_TOOLS),
        result={"schemaBytes": 65536, "schemaDepth": 12, "validationNodes": 5000, "maxRepairTurns": 2},
    )


def retry_line(attempt: int, delay: int = 1000, *, max_retries: int = 10, total: int | None = None) -> dict:
    """实测形状（E-45，逐字）。注意 `attempt` 是 CLI 的计数，会重置。"""
    return {
        "type": "retry",
        "sessionId": "session-1",
        "attempt": attempt,
        "maxRetries": max_retries,
        "errorType": "network_error",
        "delayMs": delay,
        "tier": 1,
        "tierCount": 2,
        "totalAttempts": total if total is not None else attempt,
    }


def blank_observed() -> dict:
    """`_handle_line` 需要的观测字典（与 `AragonRunner.stream()` 里的形状一致）。"""
    return {
        "result": None,
        "text": [],
        "usage": None,
        "error": None,
        "retries": 0,
        "engine": None,
        "last_retry": None,
    }


def handle(line: dict) -> tuple[dict, list[AgentEvent]]:
    """把一行 NDJSON 喂给 `_handle_line`，返回 `(observed, 收集到的事件)`。"""
    observed = blank_observed()
    events: list[AgentEvent] = []
    AragonRunner(capability())._handle_line(line, observed, events.append, {"toolCalls": 0})
    return observed, events


def test_retry_counter_does_not_reset_on_new_request() -> None:
    """AC-75：第二段请求的 `attempt` 从 1 重新开始时，累计 `retries` **不许**跟着重置。"""
    observed = blank_observed()
    events: list[AgentEvent] = []
    runner = AragonRunner(capability())
    sequence = [retry_line(1), retry_line(2), retry_line(1), retry_line(2), retry_line(3)]
    for line in sequence:
        runner._handle_line(line, observed, events.append, {"toolCalls": 0})

    assert observed["retries"] == 5, "累计必须是本地自增：attempt 出现在两段 1..n 里"
    assert [event.payload["retries"] for event in events] == [1, 2, 3, 4, 5]
    assert [event.payload["attempt"] for event in events] == [1, 2, 1, 2, 3], "attempt 原样透传，供界面显示'第 n/m 次'"
    assert all(event.type == AgentEventType.retry for event in events)


def test_retry_payload_carries_every_measured_field() -> None:
    """载荷形状与 §20.5.2 的 SSE 样例逐字段一致（多一个本地 `retries`）。"""
    _, events = handle(retry_line(3, delay=3743, total=7))
    payload = events[0].payload
    assert set(payload) == {
        "attempt",
        "maxRetries",
        "errorType",
        "delayMs",
        "tier",
        "tierCount",
        "totalAttempts",
        "retries",
    }
    assert payload["delayMs"] == 3743
    assert payload["errorType"] == "network_error"
    assert payload["retries"] == 1


def test_system_event_fills_the_engine_triple() -> None:
    """`baseUrl` 只出现在 `exec` 的 system 事件里（E-39 的 info 没有它）。"""
    observed, events = handle(
        {
            "type": "system",
            "subtype": "init",
            "model": {"provider": "openai", "id": "deepseek-flash", "baseUrl": "https://api.deepseek.com"},
        }
    )
    assert events == [], "system 事件不该被转发成 SSE 事件"
    assert observed["engine"] == {
        "provider": "openai",
        "model": "deepseek-flash",
        "baseUrl": "https://api.deepseek.com",
    }


def test_a_system_event_without_model_stays_empty() -> None:
    """没有 system 事件时**留空，不猜**（§20.3.7）。"""
    observed, _ = handle({"type": "system", "subtype": "init", "text": "hello"})
    assert observed["engine"] is None


# ── run_manager：`run.retry` 落库，retry_count 自增 ─────────────────────────


@pytest.fixture()
def run_row(session, instrument) -> Run:
    row = Run(
        id="run-retry",
        instrument_id=instrument.id,
        status="running",
        pct=30,
        started_at="2026-02-10T00:00:00Z",
        workspace_path="workspace/runs/run-retry",
        cost_known=0,
    )
    session.add(row)
    session.commit()
    return row


def test_run_retry_is_recorded_and_counted(session, run_row) -> None:
    """`_forward_agent_event` 把 `agent.retry` 写成 `run_events.type='run.retry'`。"""
    manager = run_manager_module.RunManager()
    for attempt in (1, 2, 3):
        manager._forward_agent_event(
            session,
            run_row,
            AgentEvent(AgentEventType.retry, {**retry_line(attempt), "retries": attempt, "retryCount": attempt}),
        )
    session.refresh(run_row)

    rows = session.execute(select(RunEvent).where(RunEvent.run_id == run_row.id)).scalars().all()
    assert [row.type for row in rows] == ["run.retry", "run.retry", "run.retry"]
    assert run_row.retry_count == 3

    payloads = [row.payload for row in rows]
    assert [payload["retries"] for payload in payloads] == [1, 2, 3]
    assert all(payload["errorType"] == "network_error" for payload in payloads)


def test_retry_event_shape_parity(session, run_row, tmp_path: Path) -> None:
    """AC-75 后半：SSE 与 `run_events` 的载荷**逐字段相等**。

    ★v8 评审（这条用例原来是个同义反复）：旧写法拿 `manager.replay(...)` 的信封与
    `json.loads(row.payload_json)` 相比——两者都是同一列，于是**删掉整条 SSE 路径**
    它也照样绿。现在 SSE 那半边取自 `subscribe()` 的真实订阅队列（`_record` →
    `_publish` → queue 正是 `stream_events` 读到的东西）：把 `_publish` 换成空实现，
    这条用例必红。
    """
    manager = run_manager_module.RunManager()
    queue = manager.subscribe(run_row.id)
    try:
        emitted = AgentEvent(AgentEventType.retry, {**retry_line(4, delay=6030), "retries": 4})
        manager._forward_agent_event(session, run_row, emitted)

        row = session.execute(select(RunEvent).where(RunEvent.run_id == run_row.id)).scalar_one()
        stored = json.loads(row.payload_json)

        # SSE 实时送出的那一帧（订阅队列）与落库的那一列，必须是同一份事实。
        live = queue.get_nowait()
        assert live is not None
        assert live["type"] == "run.retry"
        assert live["type"] == row.type, "SSE 类型名与 run_events.type 必须同名"
        assert live["seq"] == row.seq, "SSE 的 seq 必须与落库的 seq 是同一个游标"
        assert live["payload"] == stored, "两条读路径必须看到同一份事实"
        # 断线重连走 `replay()`：看到的信封与实时那一帧逐字段相等。
        assert manager.replay(session, run_row.id)[-1] == live, "重放与实时必须是同一个信封"
        for key, value in emitted.payload.items():
            assert stored[key] == value, f"{key} 在落库时被改过"
    finally:
        manager.unsubscribe(run_row.id, queue)


def test_retry_does_not_touch_pct(session, run_row) -> None:
    """重试不是进度：`touch_run=False`，`pct` / `stage` 不被改写。"""
    manager = run_manager_module.RunManager()
    session.refresh(run_row)
    before = (run_row.pct, run_row.stage)
    manager._forward_agent_event(session, run_row, AgentEvent(AgentEventType.retry, retry_line(1)))
    session.refresh(run_row)
    assert (run_row.pct, run_row.stage) == before
