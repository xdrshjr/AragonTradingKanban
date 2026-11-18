"""S2 · 墙钟兜底与活性清扫（AC-114 / AC-115 / AC-130 / C-41）。

这一组守的是 E-92 那条事实：**Python 侧没有任何截止时间**。`run_manager.py` 里唯一的
`asyncio.wait_for` 是 SSE 轮询，`runner.run()` 的 `while True` 只在 `waiter.done()` 或
队列空时退出——真正的时间上限完全依赖交给 CLI 的 `--max-duration`。CLI 卡死、shim
留下不关管道的孙子、或 `taskkill` 失败时，run 会永远停在 `running`，SSE 只发 ping
永不 return，前端永远转圈，还占掉一个并发名额。

两条判据，对应两层修法：

* **AC-114**：`asyncio.wait_for` 在 `deadline = max_duration + grace` 之后收敛成
  `agent_timeout`，且 `run.error.payload.deadlineSec` 存在；
* **AC-115 / AC-130**：`sweep_stale_runs()` 是**同步**函数、**不看** `SCHEDULER_ENABLED`，
  抢不动有活任务的 run（R-62）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.services import run_manager as run_manager_module


@pytest.fixture()
def bound_manager(session, monkeypatch: pytest.MonkeyPatch):
    """把 `RunManager` 的后台 session 工厂绑到**测试引擎**上。

    `sweep_stale_runs()` 用的是 `report_service_session()`（生产 = 真 SessionLocal），
    所以单测里要换掉那个工厂；否则它读 `api/data/app.db`，用例插的行看不见。
    """

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(run_manager_module, "report_service_session", factory)
    return factory


def _manager(monkeypatch: pytest.MonkeyPatch, *, grace: int, max_ms: int) -> run_manager_module.RunManager:
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_hard_deadline_grace_sec", grace, raising=False)
    monkeypatch.setattr(settings, "aragon_max_duration_ms", max_ms, raising=False)
    return run_manager_module.RunManager()


# ── AC-114：硬截止 ──────────────────────────────────────────────────────────


def test_hard_deadline_is_derived_from_the_cli_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """第 1 层的截止 = CLI 的 `--max-duration` + grace（**不是**第二份配置）。

    C-45 的原话：不新增 `AGENT_HARD_DEADLINE_SEC`。一个可设的绝对秒数会与
    `--max-duration` 形成第二个截止真源，而两个真源里更松的那个永远赢——于是
    "调大 max-duration" 变成唯一能解释的行为（与 V10-P1-6 的 keySource 是同一课）。
    """
    _manager(monkeypatch, grace=120, max_ms=900_000)
    assert run_manager_module.hard_deadline_seconds() == pytest.approx(1020.0)

    _manager(monkeypatch, grace=3, max_ms=1_000)
    assert run_manager_module.hard_deadline_seconds() == pytest.approx(4.0)

    # 负的 grace 不能把截止压到 1 秒以下（否则一次正常的快速生成都会被判超时）。
    _manager(monkeypatch, grace=-999, max_ms=0)
    assert run_manager_module.hard_deadline_seconds() == pytest.approx(1.0)


def test_there_is_no_second_deadline_configuration_key() -> None:
    """C-45：`Settings` 里**不许**出现 `agent_hard_deadline_sec` / `agent_session_save`。"""
    fields = set(get_settings().model_fields)
    assert "agent_hard_deadline_grace_sec" in fields, "grace 是允许的（它是余量，不是上限）"
    for forbidden in ("agent_hard_deadline_sec", "agent_session_save"):
        assert forbidden not in fields, f"{forbidden} 会造第二真源（C-45）"


def test_a_hanging_engine_becomes_agent_timeout(
    session, instrument, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bound_manager
) -> None:
    """AC-114 的主体：假引擎 hang 住 → run 变 `failed(agent_timeout)` 且带 `deadlineSec`。

    用 monkeypatch 把 grace 调成 0、`max_duration` 调成 1 秒，于是"等 1 秒"就够了 ——
    用例不该为了证明超时真的等两分钟。
    """
    from app.agent.runner_base import AgentRequest

    _manager(monkeypatch, grace=0, max_ms=1_000)
    manager = run_manager_module.get_manager()

    class _HangingRunner:
        name = "hanging"
        source = "aragon"

        def __init__(self) -> None:
            self.cancelled = False

        async def run(self, request: AgentRequest, on_event):
            await asyncio.sleep(30)
            return None

        async def cancel(self) -> None:
            self.cancelled = True

    from app.models.run import Run

    run = Run(
        id="h" * 32,
        instrument_id=instrument.id,
        status="running",
        stage="research",
        pct=40,
        started_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        cancel_requested=0,
        retry_count=0,
        cost_known=0,
    )
    session.add(run)
    session.commit()

    runner = _HangingRunner()
    request = AgentRequest(
        run_id=run.id,
        workspace=tmp_path,
        prompt_path=tmp_path / "p.txt",
        system_prompt_path=tmp_path / "s.md",
        schema_path=tmp_path / "schema.json",
        instrument_symbol="600519.SH",
        instrument_name="贵州茅台",
    )

    async def _go():
        return await manager._drive_agent(session, run, runner, request)  # noqa: SLF001

    result = asyncio.run(_go())
    assert result is None
    session.refresh(run)
    assert run.status == "failed"
    assert run.error_code == "agent_timeout"
    assert runner.cancelled is True, "超时必须先收进程树（留着它在烧 token 更糟）"

    # ★评审修正（E-114）：AC-114 的第二个判据**必须**被断言，而上一版只验了错误码。
    # `deadlineSec` 是"我们等了多久"这条事实的唯一落点（§22.5.2）：没有它，界面只能说
    # "引擎超时了"，用户无法知道该把 `ARAGON_MAX_DURATION_MS` 调成多少。它也只允许
    # 出现在 `agent_timeout` 这一条上——给每条失败都塞一个会让它变成噪音。
    from app.models.run import RunEvent

    error_events = [
        event
        for event in session.query(RunEvent).filter(RunEvent.run_id == run.id).all()
        if event.type == "run.error"
    ]
    assert len(error_events) == 1, [event.type for event in error_events]
    payload = error_events[0].payload
    assert payload["code"] == "agent_timeout"
    assert payload["deadlineSec"] == 1, payload


# ── AC-115：清扫 ────────────────────────────────────────────────────────────


def _other_instrument(session, suffix: str = "000858.SZ"):
    """再造一个标的。

    `ux_runs_active` 是"同一 `instrument_id` 只能有一行活跃 run"的部分唯一索引
    （§8.2 的并发闸门），所以"一条陈旧 + 一条新鲜"的夹具必须落在两个标的上。
    """
    from app.models.instrument import Instrument

    row = Instrument(
        id=f"CN:{suffix}",
        symbol=suffix,
        name=f"测试标的 {suffix}",
        market="CN",
        exchange="SSE",
        currency="CNY",
        sector="食品饮料",
        tz="Asia/Shanghai",
        created_at="2026-02-10T00:00:00Z",
        updated_at="2026-02-10T00:00:00Z",
    )
    session.merge(row)
    session.commit()
    return row


def _stale_run(session, instrument, *, minutes: int = 60, status: str = "running", target=None):
    from app.models.run import Run

    started = datetime.now(UTC) - timedelta(minutes=minutes)
    run = Run(
        id=f"{minutes:04d}".ljust(32, "0"),
        instrument_id=(target or instrument).id,
        status=status,
        stage="research",
        pct=40,
        started_at=started.isoformat().replace("+00:00", "Z"),
        cancel_requested=0,
        retry_count=0,
        cost_known=0,
    )
    session.add(run)
    session.commit()
    return run


def test_sweep_is_synchronous_and_ignores_the_scheduler_switch(
    session, instrument, monkeypatch: pytest.MonkeyPatch, bound_manager
) -> None:
    """C-41 / AC-130：`sweep_stale_runs()` 是同步函数，且**不看** `SCHEDULER_ENABLED`。

    过去它挂在 `scheduler_ticker` 上，而 `main.py` 在该开关为 0 时根本不启动 ticker
    ⇒ 最需要自愈的部署恰恰整层消失（V11-P1-4）。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "scheduler_enabled", False, raising=False)
    monkeypatch.setattr(settings, "agent_hard_deadline_grace_sec", 0, raising=False)
    monkeypatch.setattr(settings, "aragon_max_duration_ms", 60_000, raising=False)

    manager = run_manager_module.RunManager()
    assert not asyncio.iscoroutinefunction(manager.sweep_stale_runs), "清扫必须是同步函数"

    stale = _stale_run(session, instrument, minutes=60)
    fresh = _stale_run(session, instrument, minutes=0, target=_other_instrument(session))
    fresh.id = "f" * 32
    session.commit()

    swept = manager.sweep_stale_runs()
    assert swept == 1, "只该收掉过期的那个"

    session.expire_all()
    assert session.get(type(stale), stale.id).status == "failed"
    assert session.get(type(stale), stale.id).error_code == "agent_timeout"
    # 刚起的那次不许被抢（R-62 的另一半）。
    assert session.get(type(fresh), fresh.id).status == "running"


def test_sweep_never_steals_a_run_with_a_live_task(
    session, instrument, monkeypatch: pytest.MonkeyPatch, bound_manager
) -> None:
    """R-62：`if run.id in self._tasks: continue`。

    有活任务的 run 自己的 `wait_for` 会收；清扫抢它会造成两条路径同时 `_finalize_failure`。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_hard_deadline_grace_sec", 0, raising=False)
    monkeypatch.setattr(settings, "aragon_max_duration_ms", 60_000, raising=False)

    manager = run_manager_module.RunManager()
    stale = _stale_run(session, instrument, minutes=60)
    manager._tasks[stale.id] = object()  # noqa: SLF001 - 模拟"有协程还在管"

    assert manager.sweep_stale_runs() == 0
    session.expire_all()
    assert session.get(type(stale), stale.id).status == "running"


def test_sweep_survives_a_broken_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """一次对账失败不许杀死清扫（`_sweep_loop` 是常驻任务，抛一次就再也不会跑）。"""
    manager = run_manager_module.RunManager()

    def _boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(run_manager_module, "report_service_session", _boom)
    assert manager.sweep_stale_runs() == 0


def test_the_startup_path_runs_a_sweep_without_the_ticker() -> None:
    """`lifespan` 里的清扫与 ticker 解耦：两个调用点都不在 `scheduler_enabled` 分支里。"""
    from pathlib import Path

    import app.main as main_module

    source = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "sweep_stale_runs()" in source
    assert "_sweep_loop" in source
    # 清扫任务必须在 `if settings.scheduler_enabled:` **之外**创建。
    scheduler_block = source.index("if settings.scheduler_enabled:")
    sweeper = source.index('asyncio.create_task(_sweep_loop()')
    ticker_start = source.index("get_ticker().start()")
    assert scheduler_block < ticker_start < sweeper, "清扫任务应在 ticker 分支之后、无条件下创建"


def test_tick_once_does_not_fire_and_forget(monkeypatch: pytest.MonkeyPatch) -> None:
    """C-41：`tick_once()` 里不许出现未 await 的 `create_task`（它会吞异常）。"""

    import inspect

    from app.services import scheduler_ticker

    body = inspect.getsource(scheduler_ticker.SchedulerTicker.tick_once)
    assert "create_task" not in body, "tick_once 是单测入口，fire-and-forget 会让断言变成时序赌局"
