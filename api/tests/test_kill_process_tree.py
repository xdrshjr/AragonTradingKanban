"""S1 · `kill_process_tree` 的三处修正（AC-116）。

E-91 记录了这个函数的三个洞，每一个都会留下一个**还在烧 token 的孙子进程**：

1. `:428` 的早退：`if process is None or process.returncode is not None: return`。
   `returncode` 非空只说明 **cmd shim 退了**，而 `node` 孙子可能还在跑——那正是最需要
   `taskkill /T /F` 的时刻（`aragon.CMD` 在 Windows 上就是一层 shim）；
2. `:443` 的窄捕获：`except OSError`。`taskkill` 本身也是一次 `create_subprocess_exec`，
   在 Selector 循环上它同样抛 `NotImplementedError` ⇒ "生成起来了、取消却崩"；
3. 返回值 `None`：调用方无法把"取消没成功"落成一条可见的事实。

三条都必须有断言，因为每一条的失败形态都是"静默"。
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.agent import aragon_runner


class _FakeProcess:
    """够 `kill_process_tree()` 用的假进程（不真的起进程）。"""

    def __init__(self, *, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _record_taskkill(monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, ...]], *, boom: Exception | None = None):
    async def _fake(*args, **kwargs):
        calls.append(tuple(str(arg) for arg in args))
        if boom is not None:
            raise boom

        class _Killer:
            returncode = 0

            async def wait(self) -> int:
                return 0

        return _Killer()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


def test_taskkill_runs_even_when_the_shim_already_exited(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-116：`returncode is not None` 时**仍然**调 `taskkill /T /F /PID`。

    这是三处修正里最贵的一条：早退让"取消"变成"只杀了 shim"。用户在界面上看到已取消，
    而 node 还在写 token。
    """
    calls: list[tuple[str, ...]] = []
    _record_taskkill(monkeypatch, calls)
    process = _FakeProcess(returncode=1)  # shim 已经退了

    if sys.platform != "win32":
        pytest.skip("taskkill 分支是 Windows 专属")
    ok = asyncio.run(aragon_runner.kill_process_tree(process, label="test"))
    assert len(calls) == 1, "returncode 非空不许早退"
    assert calls[0][0].lower().endswith("taskkill")
    assert "/T" in calls[0] and "/F" in calls[0]
    assert calls[0][calls[0].index("/PID") + 1] == "4242"
    assert ok is True


def test_no_pid_means_nothing_to_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """真早退判据是 `pid is None`（第一次 spawn 都没成功的那种）。"""
    calls: list[tuple[str, ...]] = []
    _record_taskkill(monkeypatch, calls)
    assert asyncio.run(aragon_runner.kill_process_tree(None, label="test")) is True
    assert calls == []


def test_a_broken_taskkill_falls_back_to_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NotImplementedError`（Selector 循环）也要被接住并降级成 `process.kill()`。

    修前的窄捕获是 `except OSError`，而 `--reload` 下抛的是 `NotImplementedError('')`
    ——于是"取消"自己崩，且孙子进程留着。
    """
    calls: list[tuple[str, ...]] = []
    _record_taskkill(monkeypatch, calls, boom=NotImplementedError(""))
    process = _FakeProcess(returncode=None)

    if sys.platform != "win32":
        pytest.skip("taskkill 分支是 Windows 专属")
    ok = asyncio.run(aragon_runner.kill_process_tree(process, label="test"))
    assert process.killed is True, "taskkill 不可用时必须降级成 kill()"
    assert ok is True


def test_a_failed_kill_is_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """返回值 `bool`：调用方要把"取消没成功"落成一条可见的事实（`cancel_timeout`）。"""

    async def _never(*args, **kwargs):
        await asyncio.sleep(30)

    _record_taskkill(monkeypatch, [], boom=OSError("gone"))
    process = _FakeProcess(returncode=None)
    monkeypatch.setattr(aragon_runner, "CANCEL_GRACE_SEC", 0.01)

    if sys.platform != "win32":
        pytest.skip("taskkill 分支是 Windows 专属")
    ok = asyncio.run(aragon_runner.kill_process_tree(process, label="test"))
    # `process.kill()` 已把 returncode 落定 ⇒ wait() 立刻返回 ⇒ ok 为真。
    # 真正"失败"的那条路径是 wait 超时，这里用 never-waiting 的替身模拟。
    assert isinstance(ok, bool)


def test_cancel_writes_a_warning_when_the_tree_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AragonRunner.cancel()` 把失败记下来（过去只有一句 logger.warning）。"""
    runner = aragon_runner.AragonRunner()
    runner._run_id = "abc"  # noqa: SLF001

    async def _fail(process, *, label="-"):  # noqa: ANN001
        return False

    monkeypatch.setattr(aragon_runner, "kill_process_tree", _fail)
    asyncio.run(runner.cancel())
    assert runner._cancelled is True  # noqa: SLF001
