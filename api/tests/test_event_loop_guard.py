"""S1 · 事件循环闸门（AC-111 / AC-132）。

AC-111 是这一轮唯一一条**必须在进门前失败**的判据，而它的三个断言恰好是三个不同的
位置（V11-P0-2 的教训）：

1. `POST /instruments/{id}/reports` 返回 `event_loop_unsupported`（**同步**错误，503）；
2. `runs` 表里**没有**这一行（一次注定不可能的生成不该在账本上留痕）；
3. `settings.runs_dir/<id>` 目录**未创建**（`RunManager.start()` 在写行之前就 mkdir，
   `_pipeline()` 第一句又 mkdir —— 所以闸门只能是 start() 的第 0 步）。

AC-132 是同一个位置的**异常映射**判据：`NotImplementedError` → `event_loop_unsupported`，
`OSError` → `aragon_unavailable`。两条不许互换——把后者吞成前者，用户会去关 `--reload`，
而真正的问题是 `aragon` 找不到。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent import aragon_runner, preflight
from app.agent.runner_base import AgentError, AgentRequest
from app.config import get_settings


def _request(tmp_path: Path) -> AgentRequest:
    """按 `AgentRequest` 的**真实**签名造一份（★v12：字段是 instrument_symbol/name）。"""
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("x", encoding="utf-8")
    system_prompt = tmp_path / "system.md"
    system_prompt.write_text("你是研究员。", encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    return AgentRequest(
        run_id="abcdef0123456789abcdef0123456789",
        workspace=tmp_path,
        prompt_path=prompt,
        system_prompt_path=system_prompt,
        schema_path=schema,
        schema_source_path=schema,
        instrument_symbol="600519.SH",
        instrument_name="贵州茅台",
    )


def test_generation_is_rejected_before_any_side_effect(client, instrument, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-111：Selector 环境下发起一次生成——同步 503、无 run 行、无工作区目录。

    ★实施发现（I-119）：第三条断言必须是**前后对比**，不能写成"目录里一个子目录都没有"。
    `WORKSPACE_ROOT` 是 **session 级**的临时目录（`conftest.py:22-24`），同一进程里其它
    用例早已在里面建过工作区——于是"空目录"这条断言断的是**别人的副作用**，与本用例
    要证明的事（这次请求没有建目录）无关。改成拍快照：请求前后 `runs_dir` 的条目集合
    必须**完全一致**。这既保住了 AC-111 的原意（一次注定不可能的生成不留任何痕迹），
    也不会随用例执行顺序漂移。

    ★评审修正（评审节点 I-127）：整套 conftest 跑在 `AGENT_MODE=mock` 下，而闸门现在
    会为 mock 让路（mock 根本不 spawn 子进程）。所以这条用例必须显式把模式设成
    `real` —— 那才是 AC-111 描述的现场（真引擎 + 错误的事件循环）。
    """
    monkeypatch.setattr(get_settings(), "agent_mode", "real", raising=False)
    monkeypatch.setattr(preflight, "spawn_capability", lambda: (False, "event_loop_selector:_WindowsSelectorEventLoop"))

    runs_dir = get_settings().runs_dir
    before = {path.name for path in runs_dir.iterdir()} if runs_dir.exists() else set()

    response = client.post(f"/api/v1/instruments/{instrument.id}/reports", json={})
    assert response.status_code == 503, response.text
    body = response.json()
    # 本仓的错误响应形状是 {error: {code, message, detail, requestId}}（与 runErrors 对齐）。
    detail = body.get("error") or body.get("detail") or body
    assert detail.get("code") == "event_loop_unsupported", body
    # 用户能照做的那句话必须随响应下发（hint 的真源是 failure.FailureSpec，不是前端）。
    assert "reload" in (detail.get("message") or "").lower()

    # ② 账本上没有这一行
    runs = client.get(f"/api/v1/instruments/{instrument.id}/runs").json()
    assert runs == [] or runs.get("items") == []
    # ③ 工作区目录没有被创建——用前后快照比较（见 docstring 的 I-119）
    after = {path.name for path in runs_dir.iterdir()} if runs_dir.exists() else set()
    assert after == before, f"这次请求建了工作区：{sorted(after - before)}"


def test_the_gate_is_the_first_step_of_start(session, instrument, monkeypatch: pytest.MonkeyPatch) -> None:
    """C-38：闸门在 `RunManager.start()` 第 0 步，**不是** `_pipeline()`。

    直接对 manager 调用一次，断言抛出的错误类型与"没有任何落库/落盘副作用"。
    ★评审修正（I-127）：同上，必须显式 `real` —— 闸门对 mock 会让路（mock 不 spawn）。
    """
    from app.errors import EventLoopUnsupported
    from app.services import run_manager as run_manager_module

    monkeypatch.setattr(get_settings(), "agent_mode", "real", raising=False)
    monkeypatch.setattr(preflight, "spawn_capability", lambda: (False, "event_loop_policy:X"))
    manager = run_manager_module.RunManager()
    with pytest.raises(EventLoopUnsupported) as caught:
        manager.start(session, instrument, force=False)
    assert caught.value.code == "event_loop_unsupported"
    assert caught.value.status_code == 503
    assert session.query(run_manager_module.Run).count() == 0


def test_mock_mode_is_not_blocked_by_the_event_loop(session, instrument, monkeypatch: pytest.MonkeyPatch) -> None:
    """★评审修正（评审节点 I-127）：mock 引擎**不 spawn 子进程**，所以那道闸门与它无关。

    上一版无条件拒绝，于是"没有 aragon 也能完整演示"这条产品承诺在最常见的演示开关
    组合（`start-all.ps1 -Mock -Reload` / `AGENT_MODE=mock` + `DEV_RELOAD=1`）上失效：
    用户看到"这台机器起不来"，而演示链路其实一个子进程都不需要。

    本用例反过来断两件事：① mock 下 `start()` **不抛**（可正常建 run）；
    ② 闸门本身没被删掉——同一个 manager 在 `real` 下仍然拒绝（上一条用例守着）。
    """
    from app.services import run_manager as run_manager_module

    monkeypatch.setattr(get_settings(), "agent_mode", "mock", raising=False)
    monkeypatch.setattr(preflight, "spawn_capability", lambda: (False, "event_loop_selector:_WindowsSelectorEventLoop"))
    manager = run_manager_module.RunManager()
    run = manager.start(session, instrument, force=False)
    assert run.id
    assert session.query(run_manager_module.Run).count() == 1
    manager.request_cancel(session, run.id)


def _stub_capability(**overrides):
    """一份"引擎完全正常"的能力快照——这两条用例只测**异常映射**，不测预检。"""
    payload = {
        "available": True,
        "versionOk": True,
        "canStructured": True,
        "hasSubmitResult": True,
        "hasApiKey": True,
        "resolvedBinary": "fake-aragon",
        "extraAllowTools": [],
        "tools": [],
    }
    payload.update(overrides)
    return preflight.AragonCapability(**payload)


def test_spawn_not_implemented_is_the_loop_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-132 第一条：`NotImplementedError` → `event_loop_unsupported`。"""

    async def _boom(*args, **kwargs):
        raise NotImplementedError("")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    runner = aragon_runner.AragonRunner()
    # 绕过能力预检：这条用例只测异常映射（`_stream_once` 的第一道门是能力，不是执行）。
    monkeypatch.setattr(preflight, "check_aragon", lambda *a, **k: _stub_capability())
    monkeypatch.setattr(preflight, "effective_key_source", lambda has_key: "env")

    async def _run() -> None:
        with pytest.raises(AgentError) as caught:
            async for _event in runner._stream_once(_request(tmp_path), lambda event: None):
                pass
        assert caught.value.code == "event_loop_unsupported"

    asyncio.run(_run())


def test_spawn_os_error_stays_aragon_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-132 第二条：`OSError` → `aragon_unavailable`（两条码**不互换**）。

    V11-P1-11 的反例很具体：把三种异常统一映射成 `event_loop_unsupported`，会把
    "找不到 aragon.CMD"「cwd 不存在」「argv 非法」全部报成"事件循环不支持"，
    而 `aragon_unavailable` 的 hint（检查 `ARAGON_BIN` 与 PATH）才是唯一能救人的那句。
    """

    async def _boom(*args, **kwargs):
        raise FileNotFoundError("aragon.CMD not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    monkeypatch.setattr(preflight, "check_aragon", lambda *a, **k: _stub_capability())
    monkeypatch.setattr(preflight, "effective_key_source", lambda has_key: "env")

    runner = aragon_runner.AragonRunner()

    async def _run() -> None:
        with pytest.raises(AgentError) as caught:
            async for _event in runner._stream_once(_request(tmp_path), lambda event: None):
                pass
        assert caught.value.code == "aragon_unavailable"

    asyncio.run(_run())
