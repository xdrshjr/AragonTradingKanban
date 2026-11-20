"""额度引擎（spec §24.3.8；AC-185 – AC-193 的引擎半边、AC-207）。

这里直接对 `services/quota_service.py` 与 `RunManager.start()` 说话（没有 HTTP）：账本是真相，
所以每条用例都落到 `quota_ledger` 的行上断言，而不是断言某个计数器。

RV-9 的并发用例**用事件同步，不用 sleep**：线程 A 在 `with reservation()` 里停住（账本行已 flush、
尚未 commit），主线程用 `lock.acquire(blocking=False)` 证明锁此刻**确实被持有**，再放行 A；B 随后读到的
余额必须已经包含 A 的扣减。
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.errors import QuotaExceeded
from app.models import Instrument, QuotaLedger, ReportAsk, Run
from app.security.principal import SYSTEM, service_principal, user_principal
from app.services import quota_service, settings_service
from app.services.run_manager import RunManager


def principal_of(record, role: str | None = None):  # noqa: ANN001, ANN201
    return user_principal(
        user_id=record.id,
        username=record.username,
        role=role or record.role,  # type: ignore[arg-type]
        session_id=None,
        must_change_password=False,
    )


def make_instruments(session, count: int) -> list[Instrument]:  # noqa: ANN001
    rows = [
        Instrument(
            id=f"CN:9000{index:02d}.SH",
            symbol=f"9000{index:02d}.SH",
            name=f"测试{index}",
            market="CN",
            exchange="SSE",
            currency="CNY",
            tz="Asia/Shanghai",
            created_at="2026-02-10T00:00:00Z",
            updated_at="2026-02-10T00:00:00Z",
        )
        for index in range(count)
    ]
    session.add_all(rows)
    session.commit()
    return rows


def spend(db, principal, instrument_id: str, *, status: str = "queued") -> str:  # noqa: ANN001
    """What `RunManager.start()` does around the reservation, minus the gate chain."""
    run_id = uuid.uuid4().hex
    with quota_service.reservation(db, principal, "report", run_id):
        db.add(
            Run(
                id=run_id,
                instrument_id=instrument_id,
                status=status,
                pct=0,
                started_at="2026-02-10T00:00:00Z",
                cost_known=0,
                workspace_path=f"workspace/runs/{run_id}",
                created_by=principal.user_id,
            )
        )
        db.commit()
    return run_id


def ledger(session, user_id: str, **filters):  # noqa: ANN001, ANN201
    session.expire_all()
    query = select(QuotaLedger).where(QuotaLedger.user_id == user_id).order_by(QuotaLedger.id)
    for column, value in filters.items():
        query = query.where(getattr(QuotaLedger, column) == value)
    return list(session.execute(query).scalars())


@pytest.fixture()
def world(session, make_user):  # noqa: ANN001, ANN201
    instruments = make_instruments(session, 24)
    alice = make_user("alice")
    return session, instruments, alice, principal_of(alice)


# ── AC-185 / AC-187：默认额度、扣除顺序 ─────────────────────────────────────


def test_default_user_gets_three_and_the_fourth_is_refused_with_the_full_detail(world) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    for index in range(3):
        spend(session, who, instruments[index].id)
    with pytest.raises(QuotaExceeded) as caught:
        spend(session, who, instruments[3].id)
    error = caught.value
    assert error.status_code == 429 and error.code == "quota_exceeded"
    assert set(error.detail) == {"resource", "scope", "limit", "usedToday", "bonus", "resetAt", "canRequest"}
    assert (error.detail["limit"], error.detail["usedToday"], error.detail["bonus"]) == (3, 3, 0)
    assert error.detail["scope"] == "user" and error.detail["canRequest"] is True
    assert error.detail["resetAt"].endswith("Z")
    assert len(ledger(session, alice.id, reason="run")) == 3, "被拒的预留不留账本行"


def test_daily_credits_are_spent_before_bonus_credits(world) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    from app.models import User

    quota_service.grant(session, actor=SYSTEM, target_user=session.get(User, alice.id), resource="report", amount=5)
    session.commit()
    for index in range(8):
        spend(session, who, instruments[index].id)
    with pytest.raises(QuotaExceeded):
        spend(session, who, instruments[8].id)
    sources = [row.source for row in ledger(session, alice.id, reason="run")]
    assert sources == ["daily"] * 3 + ["bonus"] * 5


def test_status_reports_the_limit_used_bonus_remaining_and_reset(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    quota_service.grant(session, actor=SYSTEM, target_user=session.get(User, alice.id), resource="report", amount=2)
    spend(session, who, instruments[0].id)
    session.commit()
    status = quota_service.status(session, session.get(User, alice.id))
    report = status["report"]
    assert (report["limit"], report["usedToday"], report["bonus"], report["remaining"]) == (3, 1, 2, 4)
    assert report["pendingRequest"] is None and report["resetAt"].endswith("Z")
    assert status["deepAsk"]["limit"] == 10 and status["deepAsk"]["remaining"] == 10
    assert status["tz"] == "Asia/Shanghai"
    assert set(report) == {"limit", "usedToday", "bonus", "remaining", "resetAt", "pendingRequest"}


# ── AC-186：并发恰好 3 成功（+ RV-9 的锁覆盖到 commit） ─────────────────────


def test_twenty_threads_with_limit_three_yield_exactly_three_successes(world, engine) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(20)
    outcomes: list[str] = []
    guard = threading.Lock()

    def attempt(instrument_id: str) -> None:
        db = factory()
        try:
            barrier.wait()
            try:
                spend(db, who, instrument_id)
                result = "ok"
            except QuotaExceeded:
                db.rollback()
                result = "429"
            with guard:
                outcomes.append(result)
        finally:
            db.close()

    threads = [threading.Thread(target=attempt, args=(instruments[index].id,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(outcomes) == ["429"] * 17 + ["ok"] * 3
    assert len(ledger(session, alice.id, reason="run")) == 3
    assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 3, "失败的预留不留 run 行"


def test_the_lock_is_held_until_the_callers_commit_not_until_the_insert(world, engine) -> None:  # noqa: ANN001
    """RV-9：锁早于提交释放 ⇒ 后一线程读不到未提交的扣减 ⇒ 超发。事件同步，不 sleep。"""
    session, instruments, alice, who = world
    from app.models import User

    user = session.get(User, alice.id)
    user.quota_daily_limit = 1
    session.commit()

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    inside = threading.Event()
    release = threading.Event()
    result: dict[str, object] = {}

    def holder() -> None:
        db = factory()
        try:
            run_id = uuid.uuid4().hex
            with quota_service.reservation(db, who, "report", run_id):
                inside.set()  # 账本行已 flush，**尚未 commit**
                assert release.wait(timeout=30)
                db.add(
                    Run(
                        id=run_id,
                        instrument_id=instruments[0].id,
                        status="queued",
                        pct=0,
                        started_at="2026-02-10T00:00:00Z",
                        cost_known=0,
                        workspace_path="w",
                        created_by=who.user_id,
                    )
                )
                db.commit()
            result["holder"] = "ok"
        finally:
            db.close()

    def rival() -> None:
        db = factory()
        try:
            try:
                spend(db, who, instruments[1].id)
                result["rival"] = "ok"
            except QuotaExceeded:
                result["rival"] = "429"
        finally:
            db.close()

    first = threading.Thread(target=holder)
    first.start()
    assert inside.wait(timeout=30)

    stripe = quota_service._stripe(alice.id)  # noqa: SLF001 - the property under test is the lock
    assert stripe.acquire(blocking=False) is False, "A 还没 commit，用户锁必须仍被持有"

    second = threading.Thread(target=rival)
    second.start()
    release.set()
    first.join(timeout=30)
    second.join(timeout=30)

    assert result == {"holder": "ok", "rival": "429"}, "B 必须读到 A 已提交的扣减，而不是超发"
    assert len(ledger(session, alice.id, reason="run")) == 1
    assert stripe.acquire(blocking=False) is True, "退出上下文后锁已释放"
    stripe.release()


def test_an_exception_inside_the_block_still_releases_the_lock(world) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    with pytest.raises(RuntimeError), quota_service.reservation(session, who, "report", uuid.uuid4().hex):
        raise RuntimeError("boom")
    session.rollback()
    stripe = quota_service._stripe(alice.id)  # noqa: SLF001
    assert stripe.acquire(blocking=False) is True
    stripe.release()
    assert ledger(session, alice.id, reason="run") == [], "回滚把账本行一并带走"


# ── AC-188：退还 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "timeout", "budget_exceeded"])
def test_a_non_succeeded_run_is_refunded_exactly_once_to_the_original_pool(world, terminal: str) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    run_id = spend(session, who, instruments[0].id)
    session.get(Run, run_id).status = terminal
    session.commit()

    assert quota_service.reconcile_refunds(session) == 1
    assert quota_service.reconcile_refunds(session) == 0, "幂等：第二次对账不产生第二条"
    assert quota_service.reconcile_refunds(session, user_id=alice.id) == 0

    charge, refund = ledger(session, alice.id)
    assert (refund.reason, refund.delta, refund.ref_id) == ("run_refund", 1, run_id)
    assert (refund.source, refund.day_key) == (charge.source, charge.day_key), "退回当初扣的池子与那一天"


def test_succeeded_runs_including_deduped_ones_are_never_refunded(world) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    run_id = spend(session, who, instruments[0].id)
    session.get(Run, run_id).status = "succeeded"
    session.commit()
    assert quota_service.reconcile_refunds(session) == 0
    assert [row.reason for row in ledger(session, alice.id)] == ["run"]


def test_still_active_runs_are_not_refunded(world) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    spend(session, who, instruments[0].id, status="running")
    assert quota_service.reconcile_refunds(session) == 0


def test_a_refund_can_be_booked_concurrently_without_a_duplicate(world, engine) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    run_id = spend(session, who, instruments[0].id)
    session.get(Run, run_id).status = "failed"
    session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(6)

    def sweep() -> None:
        db = factory()
        try:
            barrier.wait()
            quota_service.reconcile_refunds(db)
        finally:
            db.close()

    threads = [threading.Thread(target=sweep) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(ledger(session, alice.id, reason="run_refund")) == 1


def test_a_failed_deep_ask_is_refunded_to_the_deep_pool(world) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    from app.models import Report

    report_row = Report(
        id="r" * 32,
        instrument_id=instruments[0].id,
        version=1,
        status="ready",
        title="t",
        as_of="2026-02-10T00:00:00Z",
        generated_at="2026-02-10T00:00:00Z",
        doc_json="{}",
        doc_bytes=2,
        is_latest=True,
        pinned=False,
        archived=False,
    )
    session.add(report_row)
    session.commit()
    ask_id = "ask_" + uuid.uuid4().hex
    with quota_service.reservation(session, who, "deep_ask", ask_id):
        session.add(
            ReportAsk(
                id=ask_id,
                report_id=report_row.id,
                question="why",
                mode="deep",
                status="queued",
                created_at="2026-02-10T00:00:00Z",
                user_id=alice.id,
            )
        )
        session.commit()
    assert quota_service.reconcile_refunds(session) == 0, "进行中的追问不退"
    session.get(ReportAsk, ask_id).status = "failed"
    session.commit()
    assert quota_service.reconcile_refunds(session) == 1
    charge, refund = ledger(session, alice.id)
    assert (charge.resource, charge.reason, refund.reason, refund.delta) == ("deep_ask", "ask", "ask_refund", 1)
    assert refund.ref_id == ask_id and len(ask_id) == 36, "追问 id 是 36 字符，账本 ref_id 必须装得下"


# ── AC-191：跨零点 ──────────────────────────────────────────────────────────


def test_usage_resets_at_local_midnight_but_a_refund_goes_back_to_yesterday(world, frozen_now) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    session.get(User, alice.id).quota_daily_limit = 1
    session.commit()

    frozen_now.set(datetime(2026, 11, 20, 15, 59, 59, tzinfo=UTC))  # Asia/Shanghai 23:59:59
    run_id = spend(session, who, instruments[0].id)
    before = quota_service.status(session, session.get(User, alice.id))["report"]
    assert (before["usedToday"], before["remaining"]) == (1, 0)
    assert before["resetAt"] == "2026-11-20T16:00:00Z"

    frozen_now.set(datetime(2026, 11, 20, 16, 0, 0, tzinfo=UTC))  # 00:00:00 次日
    after = quota_service.status(session, session.get(User, alice.id))["report"]
    assert (after["usedToday"], after["remaining"]) == (0, 1)
    assert after["resetAt"] == "2026-11-21T16:00:00Z"

    session.get(Run, run_id).status = "failed"
    session.commit()
    quota_service.reconcile_refunds(session)
    charge, refund = ledger(session, alice.id)
    assert refund.day_key == charge.day_key == "2026-11-20", "昨日的退款退回昨日的 day_key"
    today = quota_service.status(session, session.get(User, alice.id))["report"]
    assert today["usedToday"] == 0 and today["remaining"] == 1, "不会因为退款给今天多出一次"


# ── AC-192：不限 / 禁用 / 豁免主体 ──────────────────────────────────────────


def test_unlimited_writes_no_ledger_and_zero_always_refuses(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    session.get(User, alice.id).quota_daily_limit = -1
    session.commit()
    for index in range(6):
        spend(session, who, instruments[index].id)
    assert ledger(session, alice.id) == [], "不限额度不写账本"
    assert quota_service.status(session, session.get(User, alice.id))["report"]["remaining"] is None

    session.get(User, alice.id).quota_daily_limit = 0
    session.commit()
    with pytest.raises(QuotaExceeded) as caught:
        spend(session, who, instruments[10].id)
    assert caught.value.detail["limit"] == 0


def test_admins_default_to_unlimited_and_an_explicit_limit_binds_them(make_user, session) -> None:  # noqa: ANN001
    from app.models import User

    instruments = make_instruments(session, 4)
    boss = make_user("boss", role="admin")
    who = principal_of(boss)
    for index in range(4):
        spend(session, who, instruments[index].id, status="succeeded")
    assert ledger(session, boss.id) == []
    session.get(User, boss.id).quota_daily_limit = 1
    session.commit()
    run_id = spend(session, who, instruments[0].id, status="running")
    assert run_id and len(ledger(session, boss.id)) == 1
    with pytest.raises(QuotaExceeded):
        spend(session, who, instruments[1].id)


def test_system_service_and_guest_are_never_charged(session) -> None:  # noqa: ANN001
    instruments = make_instruments(session, 2)
    from app.security.principal import GUEST

    for who in (SYSTEM, service_principal(), service_principal(open_mode=True), GUEST):
        with quota_service.reservation(session, who, "report", uuid.uuid4().hex) as row:
            assert row is None
    assert session.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0
    quota_service.precheck(session, SYSTEM)
    assert quota_service.snapshot(session, SYSTEM) is None
    assert instruments


def test_deep_ask_quota_is_independent_of_report_quota(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    session.get(User, alice.id).quota_daily_limit = 0
    session.commit()
    with pytest.raises(QuotaExceeded):
        quota_service.precheck(session, who, "report")
    quota_service.precheck(session, who, "deep_ask")  # 深度追问的额度没动
    with quota_service.reservation(session, who, "deep_ask", "ask_" + uuid.uuid4().hex):
        session.commit()
    assert [(row.resource, row.reason) for row in ledger(session, alice.id)] == [("deep_ask", "ask")]


# ── 管理员调整 ──────────────────────────────────────────────────────────────


def test_grant_and_revoke_are_clamped_to_the_bonus_balance(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    user = session.get(User, alice.id)
    admin = principal_of(alice, "admin")
    row = quota_service.grant(session, actor=admin, target_user=user, resource="report", amount=3, note="奖励")
    assert (row.delta, row.reason, row.source, row.actor_id, row.note) == (3, "admin_grant", "bonus", alice.id, "奖励")
    taken = quota_service.grant(session, actor=admin, target_user=user, resource="report", amount=-50)
    assert (taken.delta, taken.reason) == (-3, "admin_revoke"), "扣回量夹到当前赠送余额"
    assert quota_service.grant(session, actor=admin, target_user=user, resource="report", amount=-5) is None
    assert quota_service.bonus_balance(session, alice.id, "report") == 0
    for bad in (0, 51, -51):
        with pytest.raises(ValueError):
            quota_service.grant(session, actor=admin, target_user=user, resource="report", amount=bad)


def test_set_limit_writes_the_user_column_per_resource(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    user = session.get(User, alice.id)
    quota_service.set_limit(session, actor=SYSTEM, target_user=user, resource="report", limit=7)
    quota_service.set_limit(session, actor=SYSTEM, target_user=user, resource="deep_ask", limit=-1)
    session.commit()
    status = quota_service.status(session, user)
    assert status["report"]["limit"] == 7 and status["deepAsk"]["limit"] is None
    quota_service.set_limit(session, actor=SYSTEM, target_user=user, resource="report", limit=None)
    assert quota_service.status(session, user)["report"]["limit"] == 3, "None = 跟随默认"
    with pytest.raises(ValueError):
        quota_service.set_limit(session, actor=SYSTEM, target_user=user, resource="report", limit=-2)


def test_the_default_comes_from_settings_and_applies_immediately(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    settings_service.set_value(session, settings_service.KEY_DEFAULT_DAILY_REPORT, 1)
    session.commit()
    assert quota_service.status(session, session.get(User, alice.id))["report"]["limit"] == 1
    spend(session, who, instruments[0].id)
    with pytest.raises(QuotaExceeded):
        spend(session, who, instruments[1].id)


def test_ledger_listing_is_newest_first_with_a_cursor(world) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    user = session.get(User, alice.id)
    for _ in range(5):
        quota_service.grant(session, actor=SYSTEM, target_user=user, resource="report", amount=1)
    session.commit()
    first, cursor = quota_service.list_ledger(session, alice.id, limit=2)
    assert len(first) == 2 and cursor is not None and first[0]["id"] > first[1]["id"]
    second, cursor2 = quota_service.list_ledger(session, alice.id, limit=10, before=cursor)
    assert len(second) == 3 and cursor2 is None
    assert {item["id"] for item in first}.isdisjoint(item["id"] for item in second)
    assert set(first[0]) == {"id", "at", "resource", "delta", "reason", "source", "refId", "note"}


# ── AC-207：站点日总量 ──────────────────────────────────────────────────────


def test_site_cap_refuses_the_next_name_and_refunds_give_names_back(make_user, session) -> None:  # noqa: ANN001
    instruments = make_instruments(session, 8)
    settings_service.set_value(session, settings_service.KEY_SITE_DAILY_CAP, 2)
    session.commit()
    first, second, third = (make_user(name) for name in ("pat_one", "pat_two", "pat_three"))
    spent = [spend(session, principal_of(first), instruments[0].id, status="running")]
    spent.append(spend(session, principal_of(second), instruments[1].id, status="running"))

    with pytest.raises(QuotaExceeded) as caught:
        spend(session, principal_of(third), instruments[2].id)
    assert caught.value.detail["scope"] == "site" and caught.value.detail["canRequest"] is False
    with pytest.raises(QuotaExceeded) as precheck:
        quota_service.precheck(session, principal_of(third), "report")
    assert precheck.value.detail["scope"] == "site"

    session.get(Run, spent[0]).status = "failed"  # 失败 run 退款后名额回补
    session.commit()
    run_id = spend(session, principal_of(third), instruments[2].id, status="running")
    assert run_id


def test_exempt_principals_do_not_take_site_slots_and_zero_means_no_cap(make_user, session) -> None:  # noqa: ANN001
    instruments = make_instruments(session, 8)
    settings_service.set_value(session, settings_service.KEY_SITE_DAILY_CAP, 1)
    session.commit()
    admin = make_user("root_like", role="admin")
    for index in range(3):
        spend(session, principal_of(admin), instruments[index].id, status="running")
        with quota_service.reservation(session, SYSTEM, "report", uuid.uuid4().hex):
            pass
    ordinary = make_user("ordinary")
    spend(session, principal_of(ordinary), instruments[3].id, status="running")  # 第一个普通名额照常可用
    other = make_user("other")
    with pytest.raises(QuotaExceeded):
        spend(session, principal_of(other), instruments[4].id)

    settings_service.set_value(session, settings_service.KEY_SITE_DAILY_CAP, 0)  # 0 = 不限，立即生效
    session.commit()
    assert spend(session, principal_of(other), instruments[4].id, status="running")


def test_the_site_cap_is_per_local_day(make_user, session, frozen_now) -> None:  # noqa: ANN001
    instruments = make_instruments(session, 4)
    settings_service.set_value(session, settings_service.KEY_SITE_DAILY_CAP, 1)
    session.commit()
    user = make_user("daily")
    frozen_now.set(datetime(2026, 11, 20, 15, 59, 59, tzinfo=UTC))
    spend(session, principal_of(user), instruments[0].id, status="running")
    other = make_user("daily2")
    with pytest.raises(QuotaExceeded):
        spend(session, principal_of(other), instruments[1].id, status="running")
    frozen_now.set(datetime(2026, 11, 20, 16, 0, 1, tzinfo=UTC))
    assert spend(session, principal_of(other), instruments[1].id, status="running")


# ── RunManager.start 接线（§24.3.9(a)） ─────────────────────────────────────


@pytest.fixture()
def manager() -> RunManager:
    from app.services import run_manager as run_manager_module

    run_manager_module.reset_manager()
    return run_manager_module.get_manager()


def test_start_without_an_actor_is_the_system_and_costs_no_quota(world, manager) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    run = manager.start(session, instruments[0], reason="scheduled", created_by=alice.id)
    assert run.created_by == alice.id, "定时任务的 run 记创建者"
    assert session.execute(select(func.count()).select_from(QuotaLedger)).scalar_one() == 0


def test_start_for_a_user_deducts_in_the_same_transaction_and_stamps_created_by(world, manager) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    run = manager.start(session, instruments[0], actor=who)
    assert run.created_by == alice.id
    (row,) = ledger(session, alice.id)
    assert (row.reason, row.ref_id, row.delta, row.source) == ("run", run.id, -1, "daily")


def test_rejected_starts_do_not_charge_create_a_run_or_a_workspace(world, manager) -> None:  # noqa: ANN001
    from app.errors import BaseReportMismatch, RunConflict

    session, instruments, alice, who = world
    runs_dir = Path(get_settings().runs_dir)

    def workspaces() -> int:
        return len(list(runs_dir.iterdir())) if runs_dir.exists() else 0

    manager.start(session, instruments[0], actor=who)
    baseline = (workspaces(), session.execute(select(func.count()).select_from(Run)).scalar_one())

    with pytest.raises(RunConflict):  # 409：同标已有运行中
        manager.start(session, instruments[0], actor=who)
    with pytest.raises(BaseReportMismatch):  # 422：base 不属于该标的
        manager.start(session, instruments[1], actor=who, base_report_id="nope")

    assert len(ledger(session, alice.id)) == 1, "409 / 422 不扣额度"
    assert (workspaces(), session.execute(select(func.count()).select_from(Run)).scalar_one()) == baseline


def test_start_refuses_when_the_quota_is_spent_before_touching_the_disk(world, manager) -> None:  # noqa: ANN001
    from app.models import User

    session, instruments, alice, who = world
    session.get(User, alice.id).quota_daily_limit = 0
    session.commit()
    runs_dir = Path(get_settings().runs_dir)
    before = len(list(runs_dir.iterdir())) if runs_dir.exists() else 0
    with pytest.raises(QuotaExceeded):
        manager.start(session, instruments[0], actor=who)
    after = len(list(runs_dir.iterdir())) if runs_dir.exists() else 0
    assert before == after
    assert session.execute(select(func.count()).select_from(Run)).scalar_one() == 0


def test_a_failed_run_is_refunded_promptly_by_finalize_failure(world, manager) -> None:  # noqa: ANN001
    session, instruments, alice, who = world
    run = manager.start(session, instruments[0], actor=who)
    manager._finalize_failure(session, run, "agent_failed", "boom", None)  # noqa: SLF001
    reasons = [row.reason for row in ledger(session, alice.id)]
    assert reasons == ["run", "run_refund"], "_finalize_failure 尾部的定向对账（纯加速）"


def test_the_report_inherits_the_runs_author_and_the_cost_row_the_user(world, manager) -> None:  # noqa: ANN001
    from app.models import CostLedger
    from app.services import report_service

    session, instruments, alice, who = world
    run = manager.start(session, instruments[0], actor=who)
    doc = {
        "schema": "aragon.research.report/v1",
        "asOf": "2026-02-10T00:00:00Z",
        "instrument": {"name": "测试0"},
        "sections": [],
    }
    report = report_service.create_version(
        session, instruments[0], doc, status="ready", run_id=run.id, cost_usd=0.1, cost_known=True
    )
    assert report.created_by == alice.id
    assert session.execute(select(CostLedger.user_id).where(CostLedger.report_id == report.id)).scalar_one() == alice.id
