"""租约调度器：幂等、让位、死租约、连续失败禁用，以及触发线程模型。

覆盖 AC-29（并发 tick 只起一个 run）、AC-30（让位并顺延，不丢 schedule）、
AC-43（正向：一次到期 tick 恰好 +1 run 且 `reason="scheduled"`；负例：`to_thread`
内直接 `schedule()` 必抛 `RuntimeError`），外加 `/schedules` 路由契约（§18.5.5）。

测试纪律（三处必然会踩的坑）：

* **每个线程用自己的 session**。SQLAlchemy 的 `Session` 不是线程安全的，
  而 `tick_claim()` 被设计成"可以安全放进 `to_thread`"，所以这里刻意用
  `sessionmaker(bind=engine)` 现开两个 session，模拟真实的两条线程。
* **事件循环只开一次**。`RunManager` 的 `asyncio.Semaphore`/`asyncio.Lock` 会绑在
  创建它的 loop 上，跨 `asyncio.run()` 复用同一个 manager 会在 GC 时炸出
  "attached to a different loop"，所以每个测试在 `asyncio.run()` 里跑完整场景，
  结束后 `reset_manager()` + `reset_ticker()`。
* **不留后台任务**。`manager.schedule()` 的 run 任务会继续跑六个阶段，
  测试断言完立刻取消并等它落地，避免污染下一个测试。
"""

from __future__ import annotations

import asyncio
import gc
import warnings
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.models import Report, Run, Schedule
from app.services import report_service
from app.services import run_manager as run_manager_module
from app.services import scheduler as scheduler_module
from app.services import scheduler_ticker as ticker_module
from app.services.cron import next_fire
from app.services.run_manager import MAX_CONCURRENT_RUNS
from app.services.scheduler import Scheduler
from app.services.scheduler_ticker import SchedulerTicker

# ── helpers ─────────────────────────────────────────────────────────────────


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_schedule(
    session,
    instrument,
    *,
    cron: str = "*/15 * * * *",
    enabled: int = 1,
    min_age_hours: int = 12,
    next_run_at: str | None = None,
    schedule_id: str = "sch_test_1",
) -> Schedule:
    """直接写行（不经过 `create()`），这样才能把 `next_run_at` 放在过去。"""
    now = datetime.now(UTC)
    schedule = Schedule(
        id=schedule_id,
        instrument_id=instrument.id,
        cron=cron,
        enabled=enabled,
        min_age_hours=min_age_hours,
        next_run_at=next_run_at if next_run_at is not None else _iso(now - timedelta(minutes=30)),
        created_at=_iso(now),
        updated_at=_iso(now),
    )
    session.add(schedule)
    session.commit()
    return schedule


def _make_report(session, instrument, *, generated_at: datetime, report_id: str = "rep_test_1") -> Report:
    """只填非空列：本测试只关心 `generated_at` 与 `deleted_at`。"""
    report = Report(
        id=report_id,
        instrument_id=instrument.id,
        version=1,
        status="ready",
        title="测试报告",
        as_of=_iso(generated_at),
        generated_at=_iso(generated_at),
        doc_json="{}",
        doc_bytes=2,
        is_latest=1,
        cost_known=0,
    )
    session.add(report)
    session.commit()
    return report


def _make_run(session, instrument, *, run_id: str) -> Run:
    """`schedules.last_run_id` 是真外键，所以记账测试必须先有一条 run 行。"""
    run = Run(
        id=run_id,
        instrument_id=instrument.id,
        status="succeeded",
        pct=100,
        started_at=_iso(datetime.now(UTC) - timedelta(minutes=5)),
        finished_at=_iso(datetime.now(UTC)),
        cost_known=0,
    )
    session.add(run)
    session.commit()
    return run


def _claimed_by(session, schedule_id: str) -> str | None:
    """绕过 ORM 身份映射直接读库——租约是原生 SQL 写的，ORM 视图可能是陈旧的。"""
    return session.execute(select(Schedule.claimed_by).where(Schedule.id == schedule_id)).scalar_one()


@pytest.fixture()
def clean_services():
    """每个测试前后都把 manager / ticker 这两个模块级单例清掉。"""
    run_manager_module.reset_manager()
    ticker_module.reset_ticker()
    yield
    run_manager_module.reset_manager()
    ticker_module.reset_ticker()


# ── AC-29：租约幂等 ──────────────────────────────────────────────────────────


def test_two_concurrent_tick_claims_claim_the_schedule_exactly_once(session, engine, instrument) -> None:
    """AC-29：同一到期 schedule 被两个并发 tick 命中时，**只**产生一个 claim。

    两个 tick 各在自己的线程里、各开自己的 session（`tick_claim` 被设计成可以
    安全 `to_thread`）。判据是数据库级互斥：`UPDATE … WHERE claimed_by IS NULL`
    的 `rowcount` 只有一个能拿到 1。
    """
    _make_schedule(session, instrument, next_run_at=_iso(datetime.now(UTC) - timedelta(minutes=30)))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _tick() -> list[str]:
        thread_session = factory()
        try:
            return [item["id"] for item in Scheduler().tick_claim(thread_session)]
        finally:
            thread_session.close()

    async def scenario() -> tuple[list[str], list[str]]:
        first, second = await asyncio.gather(asyncio.to_thread(_tick), asyncio.to_thread(_tick))
        return first, second

    first, second = asyncio.run(scenario())

    assert first + second == ["sch_test_1"], f"两次 tick 的 claim 结果应当是唯一的那个 id：{first} + {second}"
    # 断言之后没有人还攥着租约（`_should_fire` 通过时不释放，这是刻意的：
    # 触发完由 `mark_triggered` 清；这里模拟"已经被 ticker 接手"的中间态，
    # 只需要验证再次 tick 不会再抢到同一个 schedule）。
    with factory() as third_session:
        assert Scheduler().tick_claim(third_session) == []


def test_recover_dead_leases_releases_stale_claims(session, instrument) -> None:
    """AC-29 配套：宿主挂掉留下的租约必须被回收，否则那条 schedule 永久饿死。"""
    schedule = _make_schedule(session, instrument)
    scheduler = Scheduler(lease_seconds=120)
    stale = _iso(datetime.now(UTC) - timedelta(seconds=600))
    session.execute(
        update(Schedule)
        .where(Schedule.id == schedule.id)
        .values(claimed_by="host:1:dead", claimed_at=stale)
    )
    session.commit()

    assert scheduler.recover_dead_leases(session) == 1
    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.claimed_by is None
    assert row.claimed_at is None
    # 租约已释放，于是它重新可被 claim。
    claimed = scheduler.tick_claim(session)
    assert [item["id"] for item in claimed] == [schedule.id]


def test_fresh_lease_is_not_recovered(session, instrument) -> None:
    """AC-29 反面：租约还没过期就不许抢——否则并发幂等就白做了。"""
    schedule = _make_schedule(session, instrument)
    scheduler = Scheduler(lease_seconds=120)
    session.execute(
        update(Schedule)
        .where(Schedule.id == schedule.id)
        .values(claimed_by="host:1:alive", claimed_at=_iso(datetime.now(UTC)))
    )
    session.commit()

    assert scheduler.recover_dead_leases(session) == 0
    assert scheduler.tick_claim(session) == []


def test_tick_claim_returns_plain_dicts_not_orm_rows(session, instrument) -> None:
    """AC-43：跨线程边界只传数据，不传活着的 ORM 实例（否则会在错的 session 上惰性加载）。"""
    _make_schedule(session, instrument)
    claimed = Scheduler().tick_claim(session)
    assert claimed
    item = claimed[0]
    assert isinstance(item, dict)
    assert set(item) == {"id", "instrument_id", "cron", "min_age_hours"}


# ── AC-30：让位（容量耗尽 / 报告太新） ──────────────────────────────────────


def test_capacity_exhaustion_defers_and_keeps_the_schedule(session, instrument, monkeypatch) -> None:
    """AC-30：全局 run 已达 `MAX_CONCURRENT_RUNS` → 跳过并顺延 5 分钟，不报错、不丢 schedule。

    判据：`enabled` 不变、`next_run_at` 顺延 5 分钟、不计失败，且 `tick_claim()`
    返回空列表而不是抛异常——"不报错"同样是 AC-30 的硬要求。
    """
    schedule = _make_schedule(session, instrument)
    monkeypatch.setattr(report_service, "count_active_runs", lambda _session: 99)

    Scheduler().tick_claim(session)

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.enabled == 1, "让位不能把 schedule 关掉"
    assert row.failure_count == 0
    next_run = datetime.fromisoformat(row.next_run_at.replace("Z", "+00:00"))
    assert next_run > datetime.now(UTC)
    # 顺延窗口是 DEFER_MINUTES=5（含一点执行抖动）。
    assert next_run <= datetime.now(UTC) + timedelta(minutes=6)


def test_tick_claim_returns_empty_when_capacity_is_exhausted(session, instrument, monkeypatch) -> None:
    """AC-30（"不报错"那一半）：容量耗尽时 `tick_claim()` 必须返回空列表而不是抛异常。"""
    _make_schedule(session, instrument)
    monkeypatch.setattr(report_service, "count_active_runs", lambda _session: 99)
    assert Scheduler().tick_claim(session) == []


def test_report_younger_than_min_age_hours_is_skipped_and_advanced(session, instrument) -> None:
    """AC-30：报告龄 < `minAgeHours` → 跳过并推进到下一个触发点，`enabled` 不变。"""
    schedule = _make_schedule(session, instrument, min_age_hours=12)
    _make_report(session, instrument, generated_at=datetime.now(UTC) - timedelta(hours=1))

    Scheduler().tick_claim(session)

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.enabled == 1
    assert row.failure_count == 0
    # 推进到 cron 的下一个触发点，而不是"现在 + 5 分钟"。
    expected = next_fire(row.cron, datetime.now(UTC) - timedelta(seconds=2))
    actual = datetime.fromisoformat(row.next_run_at.replace("Z", "+00:00"))
    assert actual == expected
    assert actual > datetime.now(UTC)


def test_tick_claim_returns_empty_when_report_is_too_young(session, instrument) -> None:
    """AC-30（"不报错"那一半）：报告太新时 `tick_claim()` 必须返回空列表。"""
    _make_schedule(session, instrument, min_age_hours=12)
    _make_report(session, instrument, generated_at=datetime.now(UTC) - timedelta(hours=1))
    assert Scheduler().tick_claim(session) == []


def test_report_older_than_min_age_hours_is_claimed(session, instrument) -> None:
    """AC-30 反面：报告足够老时不得无谓跳过——否则定时刷新永远不触发。"""
    _make_schedule(session, instrument, min_age_hours=12)
    _make_report(session, instrument, generated_at=datetime.now(UTC) - timedelta(hours=30))

    claimed = Scheduler().tick_claim(session)
    assert [item["id"] for item in claimed] == ["sch_test_1"]


def test_no_report_at_all_is_claimed(session, instrument) -> None:
    """AC-30：从没生成过报告时没有"报告龄"，应当照常触发第一次。"""
    _make_schedule(session, instrument, min_age_hours=720)
    assert [item["id"] for item in Scheduler().tick_claim(session)] == ["sch_test_1"]


# ── AC-43 正例：一次到期 tick 恰好 +1 run，且 reason=scheduled ───────────────


def test_tick_once_starts_exactly_one_scheduled_run(session, engine, instrument, monkeypatch, clean_services) -> None:
    """AC-43 正例：`ticker.tick_once()` 起且只起一个 run，`reason="scheduled"`，并回写 `last_run_id`。

    确定性来自三处：① schedule 的 `next_run_at` 直接写成过去；② 没有旧报告
    （不存在"报告太新"这条分支）；③ 全局槽位空着。不 monkeypatch 任何生产代码。
    """
    from app.db import session as session_module

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)

    schedule = _make_schedule(session, instrument, next_run_at=_iso(datetime.now(UTC) - timedelta(minutes=30)))

    async def scenario() -> int:
        ticker = SchedulerTicker(tick_seconds=3600)
        started = await ticker.tick_once()
        manager = run_manager_module.get_manager()
        # 后台任务已经排上；等它至少把 run 行推进到 running（或直接结束），
        # 然后收干净，绝不留一个活着的 task 给下一个测试。
        for _ in range(200):
            if not manager._tasks:  # noqa: SLF001 - 测试需要观察后台任务是否已落地
                break
            await asyncio.sleep(0.01)
        for task in list(manager._tasks.values()):  # noqa: SLF001
            task.cancel()
        if manager._tasks:  # noqa: SLF001
            await asyncio.gather(*manager._tasks.values(), return_exceptions=True)
        return started

    started = asyncio.run(scenario())
    run_manager_module.reset_manager()

    assert started == 1

    rows = session.execute(select(Run).where(Run.instrument_id == instrument.id)).scalars().all()
    assert len(rows) == 1, f"一次到期 tick 应当恰好 +1 个 run，实际 {len(rows)}"
    run = rows[0]
    assert run.reason == "scheduled"

    session.expire_all()
    refreshed = session.get(Schedule, schedule.id)
    assert refreshed.last_run_id == run.id
    assert refreshed.failure_count == 0
    assert refreshed.claimed_by is None, "触发成功后必须释放租约"


def test_tick_once_with_nothing_due_starts_no_run(session, engine, instrument, monkeypatch, clean_services) -> None:
    """AC-43 正例的对照：没有到期 schedule 时一次 tick 起 0 个 run（不是"永远 +1"）。"""
    from app.db import session as session_module

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)

    _make_schedule(session, instrument, next_run_at=_iso(datetime.now(UTC) + timedelta(hours=1)))

    async def scenario() -> int:
        return await SchedulerTicker(tick_seconds=3600).tick_once()

    assert asyncio.run(scenario()) == 0
    assert session.execute(select(Run)).scalars().all() == []


# ── AC-43 负例：`to_thread` 里直接 `schedule()` 必抛 RuntimeError ────────────


def test_schedule_inside_to_thread_raises_runtime_error(session, instrument, clean_services) -> None:
    """AC-43 负例：`manager.schedule()` 是 `create_task`，在工作线程里必抛 `RuntimeError`。

    这条负例就是 V4-P0-2 的把关点：v3 的伪码把 `start()` + `schedule()` 一起塞进
    `asyncio.to_thread`，于是每次到期都炸成 `failure_count`，3 次后 schedule 被
    自动禁用——界面上看起来像"cron 配错了"。
    """
    manager = run_manager_module.get_manager()

    async def scenario() -> None:
        run = manager.start(session, instrument, reason="scheduled")
        try:
            await asyncio.to_thread(lambda: manager.schedule(run.id))
        finally:
            for task in list(manager._tasks.values()):  # noqa: SLF001
                task.cancel()
            if manager._tasks:  # noqa: SLF001
                await asyncio.gather(*manager._tasks.values(), return_exceptions=True)

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
    # `schedule()` 先建 coroutine 再 `create_task`；create_task 抛错后那个 coroutine
    # 永远不会被 await，回收时必然发一条 RuntimeWarning。它是本缺陷的副产品，
    # 显式在这里收起，免得噪音淹掉真正的告警。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        gc.collect()
    run_manager_module.reset_manager()


def test_trigger_inside_the_loop_does_not_raise(session, engine, instrument, monkeypatch, clean_services) -> None:
    """AC-43 正例补充：同一个 `schedule()` 在事件循环线程里调用是合法的。

    与负例成对存在才说明"位置错了"而不是"这个 API 坏了"。
    """
    from app.db import session as session_module

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    manager = run_manager_module.get_manager()

    async def scenario() -> None:
        run = manager.start(session, instrument, reason="scheduled")
        manager.schedule(run.id)  # 不抛
        for task in list(manager._tasks.values()):  # noqa: SLF001
            task.cancel()
        if manager._tasks:  # noqa: SLF001
            await asyncio.gather(*manager._tasks.values(), return_exceptions=True)

    asyncio.run(scenario())
    run_manager_module.reset_manager()


# ── 连续失败禁用 / 记账 ─────────────────────────────────────────────────────


def test_repeated_failures_disable_the_schedule(session, instrument) -> None:
    """§18.3.3：连续 `MAX_FAILURES` 次失败 → `enabled=0`，不再无限重试。"""
    schedule = _make_schedule(session, instrument)
    scheduler = Scheduler()
    for _ in range(scheduler_module.MAX_FAILURES):
        scheduler.record_failure(session, schedule.id)

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.failure_count == scheduler_module.MAX_FAILURES
    assert row.enabled == 0
    assert Scheduler().tick_claim(session) == []


def test_mark_triggered_resets_failures_and_advances_next_run(session, instrument) -> None:
    """§18.3.3：成功触发要清 `failure_count`、把 `next_run_at` 推到下一个触发点。"""
    schedule = _make_schedule(session, instrument, next_run_at=_iso(datetime.now(UTC) - timedelta(minutes=30)))
    run = _make_run(session, instrument, run_id="run-abc")
    Scheduler().tick_claim(session)  # 先抢到租约
    Scheduler().mark_triggered(session, schedule.id, run.id)

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.last_run_id == run.id
    assert row.failure_count == 0
    assert datetime.fromisoformat(row.next_run_at.replace("Z", "+00:00")) > datetime.now(UTC)


def test_mark_triggered_releases_the_lease(session, instrument) -> None:
    """§18.3.3：触发成功后租约必须真的被放掉（否则同一条 schedule 白占 2 分钟）。"""
    schedule = _make_schedule(session, instrument, next_run_at=_iso(datetime.now(UTC) - timedelta(minutes=30)))
    run = _make_run(session, instrument, run_id="run-def")
    Scheduler().tick_claim(session)
    Scheduler().mark_triggered(session, schedule.id, run.id)
    assert _claimed_by(session, schedule.id) is None


def test_defer_moves_next_run_without_disabling(session, instrument) -> None:
    """AC-30：`defer()` 只挪时间，不关 schedule、不计失败。"""
    schedule = _make_schedule(session, instrument, min_age_hours=1)
    Scheduler().tick_claim(session)
    Scheduler().defer(session, schedule.id, minutes=5)

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.enabled == 1
    assert row.failure_count == 0
    delta = datetime.fromisoformat(row.next_run_at.replace("Z", "+00:00")) - datetime.now(UTC)
    assert timedelta(minutes=4) < delta <= timedelta(minutes=6)


def test_advance_next_run_keeps_enabled(session, instrument) -> None:
    """AC-30：`advance_next_run()` 是"跳过但别关掉"的那条路径。"""
    schedule = _make_schedule(session, instrument)
    Scheduler().tick_claim(session)
    Scheduler().advance_next_run(session, session.get(Schedule, schedule.id))

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.enabled == 1
    assert row.failure_count == 0
    assert datetime.fromisoformat(row.next_run_at.replace("Z", "+00:00")) > datetime.now(UTC)


def test_disabled_schedule_is_never_claimed(session, instrument) -> None:
    """§18.5.5：`enabled=0` 的 schedule 即使到期也不得被 claim。"""
    _make_schedule(session, instrument, enabled=0)
    assert Scheduler().tick_claim(session) == []


def test_max_concurrent_runs_is_two() -> None:
    """§7.1 / AC-30：上限就是 2，测试里对它的断言也钉住，避免被"顺手调大"。"""
    assert MAX_CONCURRENT_RUNS == 2


# ── 路由：/schedules（§18.5.5） ─────────────────────────────────────────────


def test_post_schedule_rejects_bad_cron_with_422(client) -> None:
    """§18.5.5：非法 cron → 422 `invalid_cron`（不是 500，也不是 400）。"""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    response = client.post(
        "/api/v1/schedules",
        json={"instrumentId": "CN:600519.SH", "cron": "60 * * * *", "enabled": True, "minAgeHours": 12},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_cron"


def test_post_schedule_twice_conflicts_with_409(client) -> None:
    """§18.5.5：同一 instrument 只能有一条 `enabled=1` → 409 `duplicate_schedule`。"""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    payload = {"instrumentId": "CN:600519.SH", "cron": "0 9 * * 1-5", "enabled": True, "minAgeHours": 12}
    first = client.post("/api/v1/schedules", json=payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["id"].startswith("sch_")
    assert body["enabled"] is True
    assert body["nextRunAt"].endswith("Z"), "next_run_at 必须存成 UTC 的 ISO-8601"

    second = client.post("/api/v1/schedules", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_schedule"
    assert second.json()["error"]["detail"]["scheduleId"] == body["id"]


def test_patch_and_delete_schedule(client) -> None:
    """§18.5.5：PATCH 能改 cron/开关，DELETE 之后 GET 里不再出现。"""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    created = client.post(
        "/api/v1/schedules",
        json={"instrumentId": "CN:600519.SH", "cron": "0 9 * * 1-5", "enabled": True, "minAgeHours": 12},
    ).json()

    patched = client.patch(f"/api/v1/schedules/{created['id']}", json={"cron": "*/30 * * * *", "minAgeHours": 6})
    assert patched.status_code == 200, patched.text
    assert patched.json()["cron"] == "*/30 * * * *"
    assert patched.json()["minAgeHours"] == 6
    assert patched.json()["nextRunAt"] != created["nextRunAt"]

    disabled = client.patch(f"/api/v1/schedules/{created['id']}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/schedules/{created['id']}")
    assert deleted.status_code == 204

    listed = client.get("/api/v1/schedules")
    assert listed.status_code == 200
    assert listed.json()["total"] == 0


def test_get_schedules_lists_items_with_symbol(client) -> None:
    """§18.5.5：列表带 `symbol`，前端不必再发一次 resolve。"""
    client.post("/api/v1/symbols/resolve", json={"query": "600519"})
    client.post(
        "/api/v1/schedules",
        json={"instrumentId": "CN:600519.SH", "cron": "0 9 * * 1-5", "enabled": True, "minAgeHours": 12},
    )

    listed = client.get("/api/v1/schedules")
    body = listed.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["symbol"] == "600519.SH"
    assert item["cron"] == "0 9 * * 1-5"
    assert item["failureCount"] == 0
    assert item["lastRunId"] is None


def test_post_schedule_for_unknown_instrument_is_404(client) -> None:
    """§18.5.5：标的不存在 → 404，不是 500。"""
    response = client.post(
        "/api/v1/schedules",
        json={"instrumentId": "CN:000000.XX", "cron": "0 9 * * 1-5", "enabled": True, "minAgeHours": 12},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "instrument_not_found"


def test_patch_unknown_schedule_is_404(client) -> None:
    """§18.5.5：改不存在的 schedule → 404。"""
    response = client.patch("/api/v1/schedules/sch_missing", json={"enabled": False})
    assert response.status_code == 404


# ── ★v6 G3：预算闸门对定时刷新是暂停，不是失败 ─────────────


def test_budget_gate_pauses_a_scheduled_run_without_counting_a_failure(
    session, engine, instrument, monkeypatch, clean_services
) -> None:
    """★I-52：`MonthlyBudgetExceeded` 不能走 `record_failure`。

    闸门被插进 `RunManager.start()` 之后，402 会从定时触发路径上冒出来。如果它落进
    下面那个「万能的」`except Exception`，`record_failure` 会把 3 次超限累成
    `enabled = 0` —— 用户只是这个月花超了，却把一年的自动刷新永久关掉了。
    语义同 409/429：现在开不了，晚点再说，所以只 `defer`、不计失败。
    """
    from app.db import session as session_module
    from app.errors import MonthlyBudgetExceeded
    from app.services.run_manager import RunManager

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)

    schedule = _make_schedule(
        session, instrument, next_run_at=_iso(datetime.now(UTC) - timedelta(minutes=30))
    )

    def _blocked(self, *_args, **_kwargs):
        raise MonthlyBudgetExceeded(detail={"spentUsd": 25.0, "budgetUsd": 25.0, "override": True})

    monkeypatch.setattr(RunManager, "start", _blocked)

    async def scenario() -> int:
        return await SchedulerTicker(tick_seconds=3600).tick_once()

    assert asyncio.run(scenario()) == 0

    session.expire_all()
    row = session.get(Schedule, schedule.id)
    assert row.enabled == 1, "预算超限不能把定时刷新永久关掉"
    assert row.failure_count == 0, "暂停不是失败：3 次就 enabled=0"
    assert row.claimed_by is None, "defer 必须释放租约"
    assert session.execute(select(Run)).scalars().all() == [], "被拦下的 tick 不能留 run 行"
