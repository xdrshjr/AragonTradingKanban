"""S1 · 执行环境自证（AC-110 / AC-112 / AC-132）。

这一组守的是本轮最贵的一个 bug：**默认启动参数下每一次生成都在 research 阶段以
`internal` 结束**（E-88 / E-89：`runs.7aa5aeaa`，`error_message='NotImplementedError: '`）。
原因是 `uvicorn --reload` 把事件循环换成 `SelectorEventLoop`，而
`asyncio.create_subprocess_exec` 在它上面直接抛 `NotImplementedError`。

所以这里断言的是"**判据本身**"，而不是某个 UI 效果：

1. Proactor → `(True, None)`；Selector → `(False, "event_loop_selector:...")`；
   没有运行中的 loop → 看策略，前缀是 `event_loop_policy:`（两个前缀故意不同，
   因为可操作方向不同：一个去关 `--reload`，一个去查 asyncio 策略）；
2. 非 win32 恒 `(True, None)`（R-55：`isinstance` 在 Linux / uvloop 下恒为假，
   不做平台短路会把一台好好的 Linux 机器判成"不能 spawn"）；
3. `lifespan` 在 `loopOk=False` 时**不 raise**（S-2：一个起不来的服务比一个
   "能看不能生成"的服务更糟）。
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.agent import preflight


def _loop_named(name: str) -> object:
    """造一个**类名就是 `name`** 的假循环。

    为什么要动态建类：`spawn_capability()` 的 reason 里报的是 `type(loop).__name__`
    （诊断要能直接看出是 `_WindowsSelectorEventLoop` 还是 `ProactorEventLoop`），
    所以假对象必须让 `type(obj).__name__` 真的等于那个名字，否则用例在断言自己。
    """
    return type(name, (), {})()


def test_non_windows_is_always_capable(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-55：非 win32 不做平台判断，直接 `(True, None)`。"""
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _loop_named("_UnixSelectorEventLoop"))
    assert preflight.spawn_capability() == (True, None)


def test_no_running_loop_reads_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """同步路径（`RunManager.start()` 第 0 步）没有运行中的 loop ⇒ 只能看策略。

    这条用例存在的理由就是 C-38：闸门必须在**同步**方法里可用，所以判据也必须是同步的。

    ★评审修正（E-114）：先 `reset_cache()` —— 本用例断言的是**从未观测到运行中的 loop**
    那一档（`event_loop_policy:` 前缀）。进程里只要有一个 `TestClient` 生命周期跑过
    （`lifespan` 会记下它的 loop），这一档就不可达，用例会随执行顺序红绿——与 I-119
    同一个教训，所以把前置条件写成显式的一行。
    """
    preflight.reset_cache()

    def _no_loop() -> asyncio.AbstractEventLoop:
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "get_running_loop", _no_loop)

    class _SelectorPolicy:
        pass

    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: _SelectorPolicy())
    capable, reason = preflight.spawn_capability()
    assert capable is False
    assert reason == "event_loop_policy:_SelectorPolicy"

    class WindowsProactorEventLoopPolicy:
        pass

    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: WindowsProactorEventLoopPolicy())
    assert preflight.spawn_capability() == (True, None)


def test_selector_loop_is_reported_with_its_own_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-110 的前半句：Selector 上返回 `(False, "event_loop_selector:<类名>")`。"""
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _loop_named("_WindowsSelectorEventLoop"))
    # 真实 Windows 上 `windows_events.ProactorEventLoop` 存在，假 loop 不是它的实例；
    # 这里显式让 `is_proactor_loop` 走"类名"分支，避免依赖测试机平台。
    monkeypatch.setattr(preflight, "is_proactor_loop", lambda loop: type(loop).__name__ == "ProactorEventLoop")
    assert preflight.spawn_capability() == (False, "event_loop_selector:_WindowsSelectorEventLoop")


def test_proactor_loop_is_capable(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-110 的后半句：Proactor 上返回 `(True, None)`。"""
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _loop_named("ProactorEventLoop"))
    monkeypatch.setattr(preflight, "is_proactor_loop", lambda loop: type(loop).__name__ == "ProactorEventLoop")
    assert preflight.spawn_capability() == (True, None)


def test_loop_gate_is_the_third_gate_on_fully_capable(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-112：`degradedReason == "event_loop_unsupported"` 且它**排在**其它原因之前。

    `fullyCapable` 不在 wire 上（V11-P1-1②），所以这里断言的是 dataclass 的 property ——
    它是 `/capabilities.runtime.loopOk` 与 `aragon.degradedReason` 的**同一个**输入。
    """
    monkeypatch.setattr(preflight, "effective_key_source", lambda has_key: "env")
    capability = preflight.AragonCapability(
        available=True,
        versionOk=True,
        canStructured=True,
        hasSubmitResult=True,
        hasApiKey=True,
        loopOk=True,
    )
    assert capability.fullyCapable is True
    assert capability.degradedReason is None

    capability.loopOk = False
    capability.loopReason = "event_loop_selector:_WindowsSelectorEventLoop"
    assert capability.fullyCapable is False
    assert capability.degradedReason == "event_loop_unsupported"

    # 排在最前：即使引擎根本没装，只要循环不对，说出来的也必须先是"这台机器起不来"。
    capability.available = False
    assert capability.degradedReason == "event_loop_unsupported"


def test_capability_payload_carries_the_loop_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """`as_payload()` 必须把 `loopOk` / `loopReason` 发出去（第 4 次同型的坑）。"""
    capability = preflight.AragonCapability(loopOk=False, loopReason="event_loop_policy:X")
    payload = capability.as_payload()
    assert payload["loopOk"] is False
    assert payload["loopReason"] == "event_loop_policy:X"


def test_running_loop_name_reads_the_same_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """`capabilities.runtime.eventLoop` 与 `loopOk` 读的是**同一个** loop 对象（C-33）。"""
    preflight.reset_cache()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _loop_named("ProactorEventLoop"))
    assert preflight.running_loop_name() == "ProactorEventLoop"

    def _no_loop() -> asyncio.AbstractEventLoop:
        raise RuntimeError("no running event loop")

    # 没有运行中的 loop 时保留**上一次**观测到的类名，而不是编一个 `None`：
    # 诊断面板要回答的是"这个进程用的什么循环"，不是"此刻有没有在跑"。
    monkeypatch.setattr(asyncio, "get_running_loop", _no_loop)
    assert preflight.running_loop_name() == "ProactorEventLoop"
    preflight.reset_cache()
    assert preflight.running_loop_name() is None


def test_startup_does_not_raise_on_a_bad_loop(engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """S-2：`lifespan` 只 `logger.error`，**不** `raise`。

    一个起不来的服务比一个"能看不能生成"的服务更糟：既有报告仍然可读可检索。
    正确的表现是 `fullyCapable` 变假、生成入口立刻拒绝（那是 `RunManager.start()` 的事，
    `test_event_loop_guard.py` 守它）。
    """
    import app.main as main_module

    called: list[str] = []

    class _Manager:
        _tasks: dict[str, object] = {}

        def recover_interrupted(self, session) -> int:
            return 0

        def sweep_stale_runs(self) -> int:
            return 0

        def cleanup_workspaces(self, session) -> int:
            return 0

    monkeypatch.setattr(main_module, "spawn_capability", lambda: (False, "event_loop_selector:X"), raising=False)
    monkeypatch.setattr(main_module, "assert_single_worker", lambda: called.append("worker"))
    monkeypatch.setattr(main_module, "run_migrations", lambda: None)
    monkeypatch.setattr(main_module, "check_aragon", lambda *a, **k: _StubCapability(), raising=False)
    monkeypatch.setattr(main_module, "get_manager", lambda: _Manager(), raising=False)
    monkeypatch.setattr(main_module, "get_ticker", lambda: _StubTicker(), raising=False)
    # `decision` 的那几行会读设置；这里让它走 mock，避免真的 spawn CLI。
    monkeypatch.setattr(main_module.get_settings(), "agent_mode", "mock", raising=False)
    monkeypatch.setattr(main_module.get_settings(), "scheduler_enabled", False, raising=False)

    async def _run_lifespan_startup() -> None:
        async with main_module.lifespan(main_module.app):
            return

    # 不 raise 就是这条用例的全部判据；顺序断言保证自证发生在单 worker 断言之后。
    asyncio.run(_run_lifespan_startup())
    assert called == ["worker"], "启动自证必须发生在 assert_single_worker 之后"


class _StubTicker:
    async def stop(self) -> None:
        return None

    def start(self) -> None:
        return None


class _StubCapability:
    """`AragonCapability` 的最小替身（`fullyCapable` 是 property，`loopReason` 可读）。"""

    cli = "fake"
    fullyCapable = False
    loopOk = False
    loopReason = "event_loop_selector:X"
    mode = "mock"
    errors = ["子进程不可用"]
    verified = False

    def as_payload(self) -> dict[str, object]:
        return {}


def test_preflight_takes_over_the_module_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """`preflight` 顶层真的 import 了 `asyncio` / `sys`（V11-P1-1③ 的落地检查）。"""
    assert isinstance(preflight.asyncio, types.ModuleType)
    assert isinstance(preflight.sys, types.ModuleType)
    if sys.platform == "win32":  # pragma: no cover - 只在 Windows 上有意义
        # `resolvedBinary` 之类不影响这条；只确认我们问的是"当前 loop"。
        assert callable(preflight.spawn_capability)
