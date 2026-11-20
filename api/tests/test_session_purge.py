"""会话表清理（spec §25.3.1b；AC-209、AC-251）。

`sessions` 过去只增不删：过期/吊销的行永远留在表里。`purge_dead()` 只删"死了超过保留期"的行——
活会话、刚吊销、刚过期的都保留（排查"我昨天怎么被踢下线"仍可用）。清扫在 `_sweep_loop` 里经
`asyncio.to_thread` 运行：最多一次删 10 000 行，不能卡住事件循环。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db.base import iso
from app.models import UserSession
from app.security import clock
from app.services import session_service

RETAIN = 30


@pytest.fixture()
def owner(make_user):  # noqa: ANN001, ANN201
    return make_user("session_owner")


def _row(user_id: str, *, now, revoked_days: float | None = None, idle_days: float, abs_days: float | None = None):  # noqa: ANN001, ANN202
    """`revoked_days` / `idle_days` / `abs_days` 是"距今多少天以前"（负数 = 将来）。"""
    absolute = now - timedelta(days=abs_days if abs_days is not None else idle_days - 1)
    return UserSession(
        id=uuid.uuid4().hex,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        user_id=user_id,
        remember=1,
        created_at=iso(now - timedelta(days=60)),
        last_seen_at=iso(now - timedelta(days=60)),
        idle_expires_at=iso(now - timedelta(days=idle_days)),
        expires_at=iso(absolute),
        revoked_at=iso(now - timedelta(days=revoked_days)) if revoked_days is not None else None,
        revoked_reason="logout" if revoked_days is not None else None,
    )


def test_purge_dead_keeps_live_and_recent_and_removes_only_the_long_dead(session, owner) -> None:  # noqa: ANN001
    """AC-209：6 类共 100 行 → 只剩前三类；重复调用返回 0（幂等）。"""
    now = clock.now()
    kinds = {
        "live": dict(idle_days=-5, abs_days=-20),  # 空闲/绝对期限都在将来
        "just_revoked": dict(revoked_days=1, idle_days=-5, abs_days=-20),
        "just_expired": dict(idle_days=1, abs_days=0.5),
        "revoked_31d": dict(revoked_days=31, idle_days=-5, abs_days=-20),
        "idle_expired_31d": dict(idle_days=31, abs_days=30),
        "abs_expired_31d": dict(idle_days=32, abs_days=31),
    }
    plan = {
        "live": 20,
        "just_revoked": 15,
        "just_expired": 15,
        "revoked_31d": 20,
        "idle_expired_31d": 15,
        "abs_expired_31d": 15,
    }
    survivors: dict[str, str] = {}
    for kind, count in plan.items():
        for _ in range(count):
            row = _row(owner.id, now=now, **kinds[kind])
            session.add(row)
            if kind in ("live", "just_revoked", "just_expired"):
                survivors[row.id] = kind
    session.commit()
    assert session.scalar(select(func.count()).select_from(UserSession)) == 100
    snapshot = {
        row.id: (row.token_hash, row.idle_expires_at, row.expires_at, row.revoked_at)
        for row in session.execute(select(UserSession)).scalars()
        if row.id in survivors
    }

    removed = session_service.purge_dead(session, now)
    assert removed == 50
    session.expire_all()
    left = {row.id: row for row in session.execute(select(UserSession)).scalars()}
    assert set(left) == set(survivors)
    # 幸存行的内容不变。
    for row_id, expected in snapshot.items():
        row = left[row_id]
        assert (row.token_hash, row.idle_expires_at, row.expires_at, row.revoked_at) == expected
    assert session_service.purge_dead(session, now) == 0


def test_purge_dead_is_bounded_per_call_and_eventually_clears(session, owner) -> None:  # noqa: ANN001
    """AC-209：12 000 行死会话，一次调用至多 batch×5 = 10 000 行；再调若干次后清零，活会话不动。"""
    now = clock.now()
    live = _row(owner.id, now=now, idle_days=-5, abs_days=-20)
    session.add(live)
    session.execute(
        UserSession.__table__.insert(),
        [
            {
                "id": uuid.uuid4().hex,
                "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
                "user_id": owner.id,
                "remember": 1,
                "created_at": iso(now - timedelta(days=90)),
                "last_seen_at": iso(now - timedelta(days=90)),
                "idle_expires_at": iso(now - timedelta(days=40)),
                "expires_at": iso(now - timedelta(days=39)),
                "revoked_at": None,
                "revoked_reason": None,
            }
            for _ in range(12_000)
        ],
    )
    session.commit()
    first = session_service.purge_dead(session, now)
    assert first == session_service.PURGE_BATCH * session_service.PURGE_MAX_BATCHES == 10_000
    assert session.scalar(select(func.count()).select_from(UserSession)) == 2_001
    assert session_service.purge_dead(session, now) == 2_000
    assert session_service.purge_dead(session, now) == 0
    session.expire_all()
    assert [row.id for row in session.execute(select(UserSession)).scalars()] == [live.id]


def test_the_retention_window_is_configurable(session, owner, monkeypatch) -> None:  # noqa: ANN001
    now = clock.now()
    session.add(_row(owner.id, now=now, revoked_days=10, idle_days=-5, abs_days=-20))
    session.commit()
    assert session_service.purge_dead(session, now) == 0
    monkeypatch.setattr(get_settings(), "auth_session_retain_days", 7, raising=False)
    assert session_service.purge_dead(session, now) == 1


def test_a_cutoff_of_zero_days_is_clamped_so_live_rows_are_never_touched(session, owner, monkeypatch) -> None:  # noqa: ANN001
    now = clock.now()
    session.add(_row(owner.id, now=now, idle_days=-5, abs_days=-20))
    session.commit()
    monkeypatch.setattr(get_settings(), "auth_session_retain_days", 0, raising=False)
    assert session_service.purge_dead(session, now) == 0


# ── 清扫循环（AC-251）────────────────────────────────────────────────────────


def test_the_sweep_loop_runs_purge_dead_off_the_event_loop_thread(client, monkeypatch) -> None:  # noqa: ANN001
    """AC-251（RV-Y14）：桩记录线程 id 并睡 0.2 s；同时一个 10 ms 心跳协程至少前进 5 次。"""
    import app.main as main_module

    monkeypatch.setattr(main_module, "STALE_SWEEP_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "_last_session_purge", 0.0)
    seen: dict[str, object] = {"calls": 0}

    def slow_purge(db, now=None) -> int:  # noqa: ANN001, ARG001
        seen["calls"] = int(seen["calls"]) + 1  # type: ignore[arg-type]
        seen["thread"] = threading.get_ident()
        seen["start"] = True
        time.sleep(0.2)
        seen["end"] = True
        return 0

    monkeypatch.setattr(main_module.session_service, "purge_dead", slow_purge)

    async def scenario() -> tuple[int, int]:
        loop_thread = threading.get_ident()
        task = asyncio.create_task(main_module._sweep_loop())
        beats = 0
        deadline = time.monotonic() + 10
        try:
            while not seen.get("end") and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
                if seen.get("start") and not seen.get("end"):
                    beats += 1
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return loop_thread, beats

    loop_thread, beats = asyncio.run(scenario())
    assert seen.get("end"), "purge_dead was never called"
    assert seen["thread"] != loop_thread
    assert beats >= 5, beats


def test_purge_sessions_if_due_opens_its_own_session_throttles_and_never_raises(client, monkeypatch) -> None:  # noqa: ANN001
    import app.main as main_module

    monkeypatch.setattr(main_module, "_last_session_purge", 0.0)
    received: list[object] = []

    def boom(db, now=None):  # noqa: ANN001, ANN202, ARG001
        received.append(db)
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(main_module.session_service, "purge_dead", boom)
    main_module._purge_sessions_if_due()  # 异常只记日志、不外抛
    assert len(received) == 1
    # 节流：6 小时内第二次调用什么都不做。
    main_module._purge_sessions_if_due()
    assert len(received) == 1
    # 到点后再来一次。
    monkeypatch.setattr(main_module, "_last_session_purge", time.monotonic() - main_module.SESSION_PURGE_SECONDS - 1)
    main_module._purge_sessions_if_due()
    assert len(received) == 2


def test_purge_leaves_users_and_their_live_sessions_alone(session, owner) -> None:  # noqa: ANN001
    """`sessions` 无子表：删除只动 `sessions`。"""
    now = clock.now()
    session.add(_row(owner.id, now=now, revoked_days=40, idle_days=-5, abs_days=-20))
    session.commit()
    session_service.purge_dead(session, now)
    from app.models import User

    assert session.scalar(select(func.count()).select_from(User).where(User.id == owner.id)) == 1
    assert session.execute(update(UserSession).values(revoked_reason="x")).rowcount == 0
