"""S2 第 2 层 · 活性清扫：`sweep_stale_runs()` 的判据（AC-115 / AC-130 / R-62 / C-41）。

`test_hard_deadline.py` 守的是第 1 层（`runner.run()` 外面那层 `asyncio.wait_for`）；
这个文件只守第 2 层：**已经没有任何协程在管**的那种 `running`。

为什么还需要第 2 层：`persist` 阶段里 `create_version()` 抛 `IntegrityError` 之后，
如果 `_finalize_failure` 自己的 commit 再撞上 `SQLITE_BUSY`（`db/session.py` 只给
5000ms），行就停在 `running` —— 这条路径上没有任何协程还在等，第 1 层的 `wait_for`
也早就走完了。于是 SSE 永远只发 ping、前端永远转圈、还白占一个并发名额。
对账是唯一能收它的东西。

三条判据：

* **AC-115**：手工插一行 `status=running`、`started_at` 早于硬截止的 run →
  `sweep_stale_runs()` 返回 1，该行变 `failed(agent_timeout)`，并留下一条
  `run.error`（带 `deadlineSec`），因为前端"我们等了多久"的文案只从事件读；
* **AC-115 / R-62**：`self._tasks` 里有活任务的 run **不被抢** —— 抢了就是两条路径
  同时对同一行 `_finalize_failure`，前端会收到互相矛盾的终态；
* **AC-130**：清扫**不看** `SCHEDULER_ENABLED` —— 60 秒周期任务在 `lifespan` 里
  无条件创建，关掉定时刷新的部署同样能自愈。

关于 AC-130 的那"60 秒"：这里**不真的等 60 秒**。等待只能证明"这台机器在一分钟内
跑完了一个本来就能确定性断言的同步函数"，代价是整套用例多付 60 秒墙钟；而且一旦
有人把 `STALE_SWEEP_SECONDS` 改成 5 分钟，用例会以"超时"而不是"周期被改了"失败 ——
报告指向错误的文件。所以把它拆成两条更强的判据：①源码级 —— 周期常量是 60，
`create_task(_sweep_loop())` 在 `lifespan` 的 `if settings.scheduler_enabled:` 分支
**之外**（缩进 4 个空格 = 函数体层级，"字符串出现过"不算数）；②行为级 —— 在
`SCHEDULER_ENABLED=0` 下真的把那个常驻任务跑起来，只把**周期**调小（机制、函数、
DB 写入一概不换），看那一行确实变 `failed(agent_timeout)`。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import textwrap
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Instrument, Run, RunEvent
from app.services import run_manager as run_manager_module


@pytest.fixture()
def background_sessions(session, monkeypatch: pytest.MonkeyPatch):
    """把清扫用的后台 session 工厂绑到**测试引擎**上。

    `sweep_stale_runs()` 里的 session 来自 `report_service_session()`（生产 = 真
    `SessionLocal` → `api/data/app.db`，即"从不复用请求的 session"）。不换掉它，
    用例插的行清扫根本看不见、清扫写进去的行用例也看不见 —— 两边各自成功，
    断言变成空转。返回工厂本身，让需要独立连接的用例（周期任务）能自己开一个。
    """

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(run_manager_module, "report_service_session", factory)
    return factory


def _patch_deadline(monkeypatch: pytest.MonkeyPatch, *, grace: int = 0, max_ms: int = 60_000) -> float:
    """把硬截止压到 60 秒（grace=0 + `--max-duration` 60s），并返回它。

    `get_settings()` 是 `lru_cache(maxsize=1)` 的单例：改环境变量没用，已经 import
    过的模块手里攥着同一个 `Settings` 实例。所以改的是**那个实例的属性**
    （与 `test_hard_deadline.py::_manager` 同一手法）；`hard_deadline_seconds()`
    每次调用都重新 `get_settings()`，因此立刻生效。压小的理由是 `run_manager` 的
    默认截止是 900s + 120s = 1020s，用例不该为了造一行"过期"数据去等 17 分钟。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_hard_deadline_grace_sec", grace, raising=False)
    monkeypatch.setattr(settings, "aragon_max_duration_ms", max_ms, raising=False)
    return run_manager_module.hard_deadline_seconds()


def _extra_instrument(session, suffix: str) -> Instrument:
    """再造一个标的。

    `ux_runs_active` 是"同一 `instrument_id` 只能有一行活跃 run"的部分唯一索引
    （§8.2 的并发闸门），所以"一行陈旧 + 一行新鲜"这种夹具必须落在两个标的上 ——
    这不是测试的权宜，而是产品行为本身。
    """
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


def _run_row(
    session,
    instrument: Instrument,
    *,
    run_id: str,
    started_at: datetime,
    status: str = "running",
) -> Run:
    """插一行 run。`started_at` 走与生产同一个 `_iso` 形态（`...Z`，秒级/微秒级皆可）。

    `sweep_stale_runs()` 的过期判断是**字符串比较**（`Run.started_at < cutoff`），
    所以夹具必须用 `_iso()` 那种 `+00:00 → Z` 的写法，用 `str(datetime)` 会写出
    `+00:00` 而排到 `Z` 之后，让"过期"数据看起来是新的。
    """
    run = Run(
        id=run_id,
        instrument_id=instrument.id,
        status=status,
        stage="research",
        pct=40,
        started_at=started_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        cancel_requested=0,
        retry_count=0,
        cost_known=0,
    )
    session.add(run)
    session.commit()
    return run


def _ago(minutes: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _error_events(session, run_id: str) -> list[RunEvent]:
    return list(
        session.execute(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == "run.error")
        )
        .scalars()
        .all()
    )


def _code_body(func) -> str:
    """函数**可执行部分**（去掉 docstring）的源码，用来做"有没有这个调用"的断言。

    直接对 `inspect.getsource()` 断言会被 docstring 骗：`_sweep_loop` 的注释里正好
    写着"不再用 `asyncio.create_task` 包一层"，于是 `"create_task" not in source`
    会失败在一个**注释**上，而真正的回归（代码里真的多了一层 Task）反而没人测。
    所以先用 AST 把 docstring 摘掉再 unparse 回去。
    """
    node = ast.parse(textwrap.dedent(inspect.getsource(func))).body[0]
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        node.body = node.body[1:]
    return ast.unparse(node)


# ── AC-115：过期就收 ────────────────────────────────────────────────────────


def test_an_overdue_running_row_becomes_failed_agent_timeout(
    session, instrument, monkeypatch: pytest.MonkeyPatch, background_sessions
) -> None:
    """AC-115 主体：`running` + `started_at` 早于硬截止 → `failed(agent_timeout)`。

    断言的不只是 `status`：

    * **返回值** —— `_sweep_loop` 用 `if swept:` 决定要不要 warn；一个永远返回 0 的
      清扫等于没有清扫，而日志里也就永远不会有"收掉了几条"；
    * `error_code` —— 必须是 `agent_timeout`，不是 `interrupted`。两者语义不同：
      `interrupted` 是"重启前那个进程死了"，`agent_timeout` 是"进程活着但没人管了"；
    * `finished_at` —— 没有结束时间的 `failed` 行让"这次跑了多久"永远算不出来；
    * `run.error` 事件的 `code` 与 `deadlineSec` —— 前端"我们等了多久"的文案（§22.5.2）
      只从这条事件读，写在 run 行上没有任何消费者。

    60 分钟 vs 60 秒是刻意的：差距足够大，CI 上慢几百毫秒也不会变红。
    """
    deadline = _patch_deadline(monkeypatch)
    assert deadline == pytest.approx(60.0), "夹具假设的截止 = 60s"

    manager = run_manager_module.RunManager()
    run = _run_row(session, instrument, run_id="sweep-overdue", started_at=_ago(60))

    assert manager.sweep_stale_runs() == 1

    session.expire_all()
    swept = session.get(Run, run.id)
    assert swept is not None
    assert swept.status == "failed"
    assert swept.error_code == "agent_timeout"
    assert swept.finished_at is not None
    assert "硬截止" in (swept.error_message or "")

    events = _error_events(session, run.id)
    assert len(events) == 1, "终态必须留一条 run.error（前端只读事件）"
    payload = events[0].payload
    assert payload["code"] == "agent_timeout"
    assert payload["deadlineSec"] == 60, "agent_timeout 要告诉用户我们等了多久"
    assert payload["retryable"] is True


def test_a_run_with_a_live_task_is_never_stolen(
    session, instrument, monkeypatch: pytest.MonkeyPatch, background_sessions
) -> None:
    """R-62：`if run.id in self._tasks: continue` —— 有活任务的 run 清扫不许抢。

    这条防线的价值在于"只有一个收尸人"：活任务的 `wait_for` 自己会收（第 1 层）。
    如果清扫也去收，两条路径会同时对同一行 `_finalize_failure` —— 一条写
    `agent_timeout`，另一条可能刚写完成功报告，前端于是收到互相矛盾的终态，
    而 `run.error` 也会重复。

    所以这里断言三件事：返回 0、行还是 `running`、**一条 `run.error` 都没有**
    （只看状态的话，一个"改了状态又回滚"的实现也能通过）。
    """
    _patch_deadline(monkeypatch)

    manager = run_manager_module.RunManager()
    run = _run_row(session, instrument, run_id="sweep-live-task", started_at=_ago(60))
    manager._tasks[run.id] = object()  # noqa: SLF001 - 模拟"有协程还在管这次生成"

    assert manager.sweep_stale_runs() == 0

    session.expire_all()
    assert session.get(Run, run.id).status == "running"
    assert _error_events(session, run.id) == []


def test_fresh_and_under_deadline_rows_are_left_alone(
    session, instrument, monkeypatch: pytest.MonkeyPatch, background_sessions
) -> None:
    """截止是 `hard_deadline_seconds()`，不是"看起来有点旧"。

    两条**不该**被收的行放进同一次清扫：

    * `started_at` = 现在（刚起的那次，另一路协程正在跑）；
    * `started_at` = 30 秒前 —— 比 60 秒的截止新、比新鲜的更旧，正是灰色地带里
      最容易写错的那一格（一个 `started_at < now - 10s` 的实现会在这里露馅）。

    它们必须都活着。一个"任何 running 到点就杀"的实现会把每一次正常生成都杀掉，
    而用户看到的会是"什么都没做就 agent_timeout"。两条 `running` 行落在两个标的上：
    `ux_runs_active` 是"同一标的最多一行活跃 run"。
    """
    _patch_deadline(monkeypatch)

    manager = run_manager_module.RunManager()
    fresh = _run_row(session, instrument, run_id="sweep-fresh", started_at=datetime.now(UTC))
    under = _run_row(
        session,
        _extra_instrument(session, "000858.SZ"),
        run_id="sweep-under-deadline",
        started_at=_ago(0.5),
    )

    assert manager.sweep_stale_runs() == 0

    session.expire_all()
    for row in (fresh, under):
        assert session.get(Run, row.id).status == "running", f"{row.id} 还在截止内，不许收"
        assert _error_events(session, row.id) == []


def test_queued_and_cancelling_rows_are_swept_too(
    session, instrument, monkeypatch: pytest.MonkeyPatch, background_sessions
) -> None:
    """`queued` / `cancelling` 同样是"死协程的样子"，清扫必须盖住三种状态。

    只收 `running` 会留两个漏洞：①进程在 `queued` 阶段死掉时，那一行既不占 CPU
    也不再前进，但 `ux_runs_active` 仍然拦着新生成（用户点了"生成"，永远 conflict）；
    ②`cancelling` 卡住时取消按钮看着有效，实际永远等不到终态 —— 用户只能重启服务。

    三个状态各占一个标的（`ux_runs_active` 的并发闸门），一次清扫收三行。
    """
    _patch_deadline(monkeypatch)

    manager = run_manager_module.RunManager()
    rows = [
        _run_row(session, instrument, run_id="sweep-queued", started_at=_ago(60), status="queued"),
        _run_row(
            session,
            _extra_instrument(session, "000858.SZ"),
            run_id="sweep-running",
            started_at=_ago(60),
            status="running",
        ),
        _run_row(
            session,
            _extra_instrument(session, "600036.SH"),
            run_id="sweep-cancelling",
            started_at=_ago(60),
            status="cancelling",
        ),
    ]

    assert manager.sweep_stale_runs() == 3

    session.expire_all()
    for row in rows:
        reloaded = session.get(Run, row.id)
        assert reloaded.status == "failed", f"{row.id} 卡在 {row.status}，清扫没收"
        assert reloaded.error_code == "agent_timeout"
    assert sum(len(_error_events(session, row.id)) for row in rows) == 3


# ── AC-130：清扫不看 SCHEDULER_ENABLED ──────────────────────────────────────


def test_the_sweeper_is_created_outside_the_scheduler_switch() -> None:
    """AC-130 的结构判据：清扫任务在 `lifespan` 里**无条件**创建。

    要防的正是 V11-P1-4 那次回归：清扫曾经挂在 `scheduler_ticker` 上，而
    `SCHEDULER_ENABLED=0` 时 ticker 根本不启动 —— 于是"关掉定时刷新"（最需要自愈的
    部署）恰好把整层兜底删掉了，而且没有任何提示。

    所以断言顺序 **和缩进**：`if settings.scheduler_enabled:` → `create_task(...,
    name="stale-sweeper")`，且那一行缩进 4 个空格（`lifespan` 函数体层级）。
    只断言"源码里出现过 `_sweep_loop`"是不够的 —— 把它搬进 `if` 分支里同样能通过，
    而这正是回归的形态。顺带钉住 60 秒周期与关停顺序。
    """
    import app.main as main_module

    assert main_module.STALE_SWEEP_SECONDS == 60, "对账周期 60 秒（与 ticker 的 30 秒是两件事）"
    assert asyncio.iscoroutinefunction(main_module._sweep_loop)  # noqa: SLF001 - 常驻任务本身

    loop_body = _code_body(main_module._sweep_loop)  # noqa: SLF001
    assert "asyncio.sleep(STALE_SWEEP_SECONDS)" in loop_body, "周期必须读常量，不许写死一个数"
    assert "sweep_stale_runs()" in loop_body
    # C-41：`sweep_stale_runs()` 是同步函数，直接调；再包一层没人 await 的 Task
    # 会把异常丢进虚空，也让"清扫真的跑了"变成时序赌局。
    assert "create_task" not in loop_body

    source = inspect.getsource(main_module.lifespan)
    branch = source.index("if settings.scheduler_enabled:")
    created = source.index('asyncio.create_task(_sweep_loop(), name="stale-sweeper")')
    assert branch < created, "清扫任务必须在 ticker 分支之后创建"
    line = next(row for row in source.splitlines() if "create_task(_sweep_loop()" in row)
    assert len(line) - len(line.lstrip()) == 4, "缩进 4 空格 = 函数体层级，不在 if 分支（8 空格）里"
    assert "sweeper.cancel()" in source, "关停顺序：先停清扫任务，再杀子进程（§18.9 M10）"


def test_the_sweep_loop_heals_an_overdue_row_with_the_scheduler_off(
    session, instrument, monkeypatch: pytest.MonkeyPatch, background_sessions
) -> None:
    """AC-130 的行为判据：`SCHEDULER_ENABLED=0` 时那一行照样会变成
    `failed(agent_timeout)`。

    跑的是真的 `main._sweep_loop`、真的 `get_manager().sweep_stale_runs()`、真的
    DB 写入；只把**周期**调小（`STALE_SWEEP_SECONDS`），不放宽任何判据 —— 换掉周期
    不等于换掉机制，这也是"不真的等 60 秒"能够诚实地替代 AC-130 的原因（见模块
    docstring：等待证明不了语义，只会多付一分钟并把"周期被改成 5 分钟"报成超时）。

    `conftest.py` 把 `SCHEDULER_ENABLED` 设成 0 是整套用例的前提，这里先把它断言
    出来：否则哪天默认值变了，这条用例会悄悄退化成"开着 scheduler 也能自愈"，
    而它存在的全部意义就是那个 0。
    """
    import app.main as main_module

    assert get_settings().scheduler_enabled is False, "前提：这套测试跑在 SCHEDULER_ENABLED=0 下"
    _patch_deadline(monkeypatch)
    run = _run_row(session, instrument, run_id="sweep-loop", started_at=_ago(60))
    monkeypatch.setattr(main_module, "STALE_SWEEP_SECONDS", 0.01, raising=False)

    probe_factory = background_sessions

    async def _drive_until_healed() -> None:
        """把常驻任务真的跑起来，等它自己收掉那一行；最多 3 秒。

        轮询用自己的短命 session（`probe_factory`），不读测试 session 的缓存实例 ——
        否则这条用例会变成"断言 SQLAlchemy 的 identity map"，而不是清扫。
        """
        task = asyncio.create_task(main_module._sweep_loop())  # noqa: SLF001
        probe = probe_factory()
        try:
            for _ in range(300):
                await asyncio.sleep(0.01)
                probe.rollback()  # 每轮开一个新事务，才看得见清扫刚提交的行
                row = probe.get(Run, run.id)
                if row is not None and row.status == "failed":
                    return
        finally:
            probe.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(_drive_until_healed())

    session.expire_all()
    healed = session.get(Run, run.id)
    assert healed.status == "failed", "SCHEDULER_ENABLED=0 时清扫层消失了（V11-P1-4 的形态）"
    assert healed.error_code == "agent_timeout"
