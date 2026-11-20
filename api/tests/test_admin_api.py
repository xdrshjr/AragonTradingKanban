"""管理后台 API（spec §24.5.3、§24.3.14；AC-176/177、AC-194–199、AC-196/197、AC-182 的 admin 部分）。

世界：一个 `enforced` 应用（root 由 lifespan 引导）。`root_client` / `admin_client` / `user_client`
是三个各有 Cookie 罐的客户端；`make_user` 直接落库造人（省一次注册往返）。除少数断言"计时/限速"的用例外，
时间都用 `frozen_now` 冻结，让"今天""最近 7 天"是确定的。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.base import iso
from app.models import AuditLog, InviteCode, QuotaLedger, Run, Setting, User, UserSession, WatchlistItem
from app.security import clock
from app.services import admin_overview, user_service

ADMIN = "/api/v1/admin"
AUTH = "/api/v1/auth"
ROOT_PASSWORD = "13519126390"


# ── 夹具 ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _client(auth_client: TestClient) -> TestClient:
    return TestClient(auth_client.app)


@pytest.fixture()
def root_client(auth_client, maker) -> TestClient:  # noqa: ANN001
    """root，已清强制改密标志并登录（默认口令是公开的，正常会被卡在改密页）。"""
    with maker() as db:
        root = user_service.get_root(db)
        root.must_change_password = 0
        db.commit()
    client = _client(auth_client)
    response = client.post(f"{AUTH}/login", json={"username": "jdqqjr", "password": ROOT_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture()
def admin_client(auth_client, make_user, login_as) -> TestClient:  # noqa: ANN001
    user = make_user("boss_admin", role="admin")
    client = _client(auth_client)
    login_as(client, user)
    return client


@pytest.fixture()
def user_client(auth_client, make_user, login_as) -> TestClient:  # noqa: ANN001
    user = make_user("plain_user")
    client = _client(auth_client)
    login_as(client, user)
    return client


def code_of(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def user_id(maker, username: str) -> str:  # noqa: ANN001
    with maker() as db:
        found = user_service.get_by_username(db, username)
        assert found is not None, username
        return found.id


def audit_actions(maker) -> list[str]:  # noqa: ANN001
    with maker() as db:
        return [row.action for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()]


def last_audit(maker, action: str) -> AuditLog:  # noqa: ANN001
    with maker() as db:
        row = db.execute(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc()).limit(1)
        ).scalar_one_or_none()
        assert row is not None, f"no audit row {action}"
        return row


# ── 概览（AC-194）────────────────────────────────────────────────────────────


def seed_run(db, instrument, status: str, started_at: str, created_by: str | None) -> None:  # noqa: ANN001
    db.add(
        Run(
            id=uuid.uuid4().hex,
            instrument_id=instrument.id,
            status=status,
            started_at=started_at,
            created_by=created_by,
        )
    )


def test_overview_aggregates_match_hand_written_sql(
    admin_client,
    make_user,
    instrument,
    maker,
    frozen_now,  # noqa: ANN001
) -> None:
    alice = make_user("alice")
    bob = make_user("bob", status="disabled")
    carol = make_user("carol", locked_until=iso(frozen_now.current + timedelta(hours=1)))
    now = frozen_now.current
    today = iso(now)
    yesterday = iso(now - timedelta(days=1))
    with maker() as db:
        for status, who in (
            ("succeeded", alice.id),
            ("succeeded", alice.id),
            ("succeeded", bob.id),
            ("failed", alice.id),
            ("cancelled", carol.id),
        ):
            seed_run(db, instrument, status, today, who)
        seed_run(db, instrument, "succeeded", yesterday, alice.id)
        seed_run(db, instrument, "succeeded", iso(now - timedelta(days=30)), alice.id)  # 窗口外
        day = clock.local_day_key(now, get_settings().quota_tz)
        for reason, delta in (("run", -1), ("run", -1), ("run_refund", 1), ("admin_grant", 5)):
            db.add(
                QuotaLedger(
                    user_id=alice.id,
                    resource="report",
                    delta=delta,
                    reason=reason,
                    source="daily" if reason != "admin_grant" else "bonus",
                    day_key=day,
                    created_at=today,
                )
            )
        # 昨天的消耗不计入今天
        db.add(
            QuotaLedger(
                user_id=alice.id,
                resource="report",
                delta=-1,
                reason="run",
                source="daily",
                day_key="2000-01-01",
                created_at=yesterday,
            )
        )
        db.commit()

    body = admin_client.get(f"{ADMIN}/overview").json()
    assert body["users"]["total"] == 5  # root + boss_admin + alice + bob + carol
    assert body["users"]["byRole"] == {"root": 1, "admin": 1, "user": 3}
    assert body["users"]["disabled"] == 1
    assert body["users"]["locked"] == 1
    assert body["runs"] == {"today": 5, "succeeded": 3, "failed": 1, "successRate": 0.75}
    assert body["quota"] == {"consumedToday": 2, "refundedToday": 1}
    assert len(body["signups"]) == len(body["runsDaily"]) == 14
    assert body["signups"][-1]["count"] == 5 and body["runsDaily"][-1]["count"] == 5
    assert body["runsDaily"][-2]["count"] == 1
    assert [row["day"] for row in body["runsDaily"]] == sorted(row["day"] for row in body["runsDaily"])
    assert body["topUsers7d"][0] == {"userId": alice.id, "username": "alice", "runs": 4}
    assert len(body["topUsers7d"]) <= 5
    assert body["pendingQuotaRequests"] == 0
    assert 1 <= len(body["recentAudit"]) <= 8
    assert set(body["recentAudit"][0]) >= {"id", "at", "action", "actorName", "detail"}

    # 与手写 SQL 逐项对账
    with maker() as db:
        assert db.execute(text("select count(*) from users where status != 'deleted'")).scalar() == 5
        assert db.execute(text("select count(*) from runs where started_at >= :t"), {"t": today}).scalar() == 5


def test_overview_success_rate_is_null_without_finished_runs(admin_client) -> None:  # noqa: ANN001
    body = admin_client.get(f"{ADMIN}/overview").json()
    assert body["runs"] == {"today": 0, "succeeded": 0, "failed": 0, "successRate": None}
    assert body["topUsers7d"] == []


def test_overview_day_buckets_follow_the_quota_timezone(frozen_now, monkeypatch) -> None:  # noqa: ANN001
    """23:30 UTC 已经是上海的次日 07:30：窗口的最后一天是上海日历的"今天"。"""
    from datetime import UTC, datetime

    frozen_now.set(datetime(2026, 3, 1, 23, 30, tzinfo=UTC))
    windows = admin_overview.day_windows(frozen_now.current)
    assert windows[-1][0] == "2026-03-02"
    assert windows[-1][1] == "2026-03-01T16:00:00Z"  # 上海 00:00 = 前一日 16:00 UTC
    assert windows[-1][2] == "2026-03-02T16:00:00Z"
    assert len(windows) == 14 and windows[0][0] == "2026-02-17"


def test_overview_is_fast_on_20k_users(admin_client, engine, frozen_now) -> None:  # noqa: ANN001
    """AC-194：CI 以 2 万用户 < 40 ms 为门（取多次中的最好一次，免受负载抖动）。"""
    now = frozen_now.current
    rows = [
        {
            "id": uuid.uuid4().hex,
            "username": f"bulk{n}",
            "username_lc": f"bulk{n}",
            "password_hash": "!",
            "role": "user",
            "status": "active",
            "created_at": iso(now - timedelta(minutes=n % 20000)),
            "updated_at": iso(now),
            "last_login_at": iso(now - timedelta(hours=n % 400)),
        }
        for n in range(20000)
    ]
    with engine.begin() as connection:
        connection.execute(User.__table__.insert(), rows)
    session = sessionmaker(bind=engine)()
    try:
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            admin_overview.overview(session, now)
            timings.append((time.perf_counter() - started) * 1000)
    finally:
        session.close()
    assert min(timings) < 40, f"overview took {min(timings):.1f} ms (all: {timings})"


# ── 用户列表（AC-195）────────────────────────────────────────────────────────


def seed_bulk_users(engine, count: int, created_at: str, prefix: str = "bulk") -> list[str]:  # noqa: ANN001
    ids = []
    rows = []
    for n in range(count):
        uid = uuid.uuid4().hex
        ids.append(uid)
        rows.append(
            {
                "id": uid,
                "username": f"{prefix}{n:03d}",
                "username_lc": f"{prefix}{n:03d}",
                "password_hash": "!",
                "role": "user",
                "status": "active",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
    with engine.begin() as connection:
        connection.execute(User.__table__.insert(), rows)
    return ids


def test_user_list_cursor_has_no_gaps_or_duplicates_even_for_identical_created_at(
    admin_client,
    engine,  # noqa: ANN001
) -> None:
    seed_bulk_users(engine, 120, "2026-01-01T00:00:00Z")  # 120 个同一秒创建的用户
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 50, "q": "bulk"}
        if cursor:
            params["before"] = cursor
        body = admin_client.get(f"{ADMIN}/users", params=params).json()
        pages += 1
        assert body["total"] == 120
        seen += [item["id"] for item in body["items"]]
        cursor = body["nextCursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 120


def test_user_list_filters_and_default_excludes_tombstones(
    admin_client,
    make_user,
    root_client,
    maker,  # noqa: ANN001
) -> None:
    make_user("zed_one")
    make_user("zed_two", status="disabled")
    make_user("zebra", role="admin")
    gone = make_user("zed_gone")
    assert root_client.delete(f"{ADMIN}/users/{gone.id}", params={"confirm": "zed_gone"}).status_code == 204

    names = lambda body: sorted(item["username"] for item in body["items"])  # noqa: E731
    assert names(admin_client.get(f"{ADMIN}/users", params={"q": "ZED"}).json()) == ["zed_one", "zed_two"]
    assert names(admin_client.get(f"{ADMIN}/users", params={"q": "ze", "role": "admin"}).json()) == ["zebra"]
    assert names(admin_client.get(f"{ADMIN}/users", params={"q": "zed", "status": "disabled"}).json()) == ["zed_two"]
    tomb = admin_client.get(f"{ADMIN}/users", params={"status": "deleted"}).json()
    assert tomb["total"] == 1 and tomb["items"][0]["status"] == "deleted"
    # `q` 只配对用户名前缀，且按小写不敏感
    assert admin_client.get(f"{ADMIN}/users", params={"q": "one"}).json()["total"] == 0


@pytest.mark.parametrize("q", ["%", "_", "z%", "a_b", "zed one", "ze'd", "ze;--", "x" * 21])
def test_wildcards_and_odd_characters_in_q_match_nothing_instead_of_everything(
    admin_client,
    make_user,
    q: str,  # noqa: ANN001
) -> None:
    make_user("zed_one")
    body = admin_client.get(f"{ADMIN}/users", params={"q": q})
    assert body.status_code == 200
    assert body.json() == {"items": [], "total": 0, "nextCursor": None}


def test_the_prefix_query_uses_the_username_index_and_is_not_a_like(admin_client, engine) -> None:  # noqa: ANN001
    """RV-8 / AC-195：`EXPLAIN QUERY PLAN` 含 `ux_users_username_lc`，且语句里没有 LIKE。"""
    seed_bulk_users(engine, 300, "2026-01-01T00:00:00Z", prefix="plan")
    captured: list[tuple[str, tuple]] = []

    def grab(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        if "username_lc" in statement and "FROM users" in statement and "count(" not in statement.lower():
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", grab)
    try:
        assert admin_client.get(f"{ADMIN}/users", params={"q": "plan1", "limit": 20}).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    assert captured, "the page query was not captured"
    statement, parameters = captured[-1]
    assert " LIKE " not in statement.upper()
    with engine.connect() as connection:
        plan = " | ".join(
            str(row[-1]) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
        )
    assert "ux_users_username_lc" in plan, plan


def test_a_user_list_request_costs_a_constant_number_of_statements(
    admin_client,
    engine,
    make_user,  # noqa: ANN001
) -> None:
    """AC-195：一次列表请求的 SQL 条数 ≤ 6，且与页大小无关（无 N+1）。"""
    seed_bulk_users(engine, 60, "2026-01-01T00:00:00Z", prefix="n1")
    make_user("someone")
    counts: list[int] = []

    def measure(limit: int) -> int:
        statements: list[str] = []

        def grab(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", grab)
        try:
            assert admin_client.get(f"{ADMIN}/users", params={"limit": limit}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", grab)
        return len([s for s in statements if not s.startswith(("PRAGMA", "BEGIN", "COMMIT", "ROLLBACK"))])

    for limit in (5, 50):
        counts.append(measure(limit))
    assert counts[0] <= 6 and counts[1] <= 6, counts
    assert counts[0] == counts[1], counts


def test_list_rows_carry_quota_and_lock_state(admin_client, make_user, maker, frozen_now) -> None:  # noqa: ANN001
    spender = make_user("spender", quota_daily_limit=5)
    make_user("freeloader", quota_daily_limit=-1)
    locked = make_user("lockedout", locked_until=iso(frozen_now.current + timedelta(minutes=5)))
    day = clock.local_day_key(frozen_now.current, get_settings().quota_tz)
    with maker() as db:
        for delta, source in ((-1, "daily"), (-1, "daily"), (4, "bonus")):
            db.add(
                QuotaLedger(
                    user_id=spender.id,
                    resource="report",
                    delta=delta,
                    reason="run" if delta < 0 else "admin_grant",
                    source=source,
                    day_key=day,
                    created_at=iso(frozen_now.current),
                )
            )
        db.commit()
    items = {row["username"]: row for row in admin_client.get(f"{ADMIN}/users", params={"limit": 200}).json()["items"]}
    assert items["spender"]["quota"]["report"] == {"limit": 5, "usedToday": 2, "bonus": 4, "remaining": 7}
    assert items["freeloader"]["quota"]["report"]["limit"] is None
    assert items["freeloader"]["quota"]["report"]["remaining"] is None
    assert items["lockedout"]["locked"] is True and items["spender"]["locked"] is False
    assert items["boss_admin"]["quota"]["report"]["limit"] is None  # admin 默认不限
    assert locked.username in items


def test_list_validates_its_query(admin_client) -> None:  # noqa: ANN001
    assert admin_client.get(f"{ADMIN}/users", params={"limit": 0}).status_code == 422
    assert admin_client.get(f"{ADMIN}/users", params={"limit": 999}).status_code == 422
    assert admin_client.get(f"{ADMIN}/users", params={"role": "god"}).status_code == 422
    bad = admin_client.get(f"{ADMIN}/users", params={"before": "garbage"})
    assert bad.status_code == 422 and code_of(bad) == "invalid_cursor"


# ── 创建用户与临时密码（AC-196）──────────────────────────────────────────────


def test_creating_a_user_returns_the_temporary_password_exactly_once(
    admin_client,
    auth_client,
    maker,  # noqa: ANN001
) -> None:
    response = admin_client.post(f"{ADMIN}/users", json={"username": "newbie", "displayName": "新人"})
    assert response.status_code == 201, response.text
    body = response.json()
    temporary = body["temporaryPassword"]
    assert len(temporary) == 12 and body["user"]["username"] == "newbie" and body["user"]["role"] == "user"
    assert body["user"]["displayName"] == "新人"

    # 之后无处可再读到它
    uid = body["user"]["id"]
    for path in (f"{ADMIN}/users/{uid}", f"{ADMIN}/users"):
        assert temporary not in admin_client.get(path).text
    with maker() as db:
        row = user_service.get_by_username(db, "newbie")
        assert row.must_change_password == 1 and row.created_by is not None
        assert temporary not in row.password_hash
        blob = json.dumps(
            [[r.detail_json, r.target_name, r.actor_name] for r in db.execute(select(AuditLog)).scalars()]
        )
        assert temporary not in blob

    # 临时密码能登录，但被卡在改密页
    fresh = _client(auth_client)
    login = fresh.post(f"{AUTH}/login", json={"username": "newbie", "password": temporary})
    assert login.status_code == 200 and login.json()["user"]["mustChangePassword"] is True
    blocked = fresh.get("/api/v1/watchlist")
    assert blocked.status_code == 403 and code_of(blocked) == "password_change_required"
    assert "user.create" in audit_actions(maker)


def test_a_supplied_password_is_checked_against_the_policy_and_not_echoed(admin_client) -> None:  # noqa: ANN001
    weak = admin_client.post(f"{ADMIN}/users", json={"username": "weakling", "password": "12345678"})
    assert weak.status_code == 422 and code_of(weak) == "weak_password"
    ok = admin_client.post(f"{ADMIN}/users", json={"username": "solid", "password": "Correct-Horse-4x"})
    assert ok.status_code == 201
    assert ok.json()["temporaryPassword"] is None
    assert "Correct-Horse-4x" not in ok.text


@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("ab", "invalid_username"),
        ("has space", "invalid_username"),
        ("admin", "invalid_username"),
        ("boss_admin", "username_taken"),
    ],
)
def test_username_rules_apply_to_admin_creation_too(admin_client, username: str, expected: str) -> None:  # noqa: ANN001
    response = admin_client.post(f"{ADMIN}/users", json={"username": username})
    assert response.status_code in (409, 422) and code_of(response) == expected


def test_only_root_can_create_an_admin_and_nobody_a_second_root(
    admin_client,
    root_client,  # noqa: ANN001
) -> None:
    refused = admin_client.post(f"{ADMIN}/users", json={"username": "wannabe", "role": "admin"})
    assert refused.status_code == 403 and code_of(refused) == "admin_protected"
    made = root_client.post(f"{ADMIN}/users", json={"username": "deputy", "role": "admin"})
    assert made.status_code == 201 and made.json()["user"]["role"] == "admin"
    assert root_client.post(f"{ADMIN}/users", json={"username": "pretender", "role": "root"}).status_code == 422


def test_bonus_credits_at_creation_land_in_the_ledger_with_the_actor(
    admin_client,
    maker,  # noqa: ANN001
) -> None:
    uid = admin_client.post(f"{ADMIN}/users", json={"username": "gifted", "bonusCredits": 4}).json()["user"]["id"]
    with maker() as db:
        rows = db.execute(select(QuotaLedger).where(QuotaLedger.user_id == uid)).scalars().all()
    assert [(row.delta, row.reason, row.source) for row in rows] == [(4, "admin_grant", "bonus")]
    assert rows[0].actor_id is not None
    assert "quota.grant" in audit_actions(maker)


# ── 矩阵（AC-176 / AC-177）──────────────────────────────────────────────────


def test_a_second_root_is_refused_by_the_database(engine, maker) -> None:  # noqa: ANN001, ARG001
    from sqlalchemy.exc import IntegrityError

    with maker() as db:
        user_service.create_user(db, username="root_one", password_hash="!", role="root", allow_reserved=True)
        db.commit()
        with pytest.raises(IntegrityError):
            user_service.create_user(db, username="root_two", password_hash="!", role="root", allow_reserved=True)
            db.commit()


WRITES_ON_A_USER = (
    ("PATCH", "", {"status": "disabled"}),
    ("PATCH", "", {"displayName": "x"}),
    ("POST", "/reset-password", None),
    ("POST", "/revoke-sessions", None),
    ("POST", "/unlock", None),
    ("POST", "/quota", {"resource": "report", "grant": 1}),
    ("DELETE", "", None),
)


def call(client: TestClient, method: str, uid: str, suffix: str, body, username: str):  # noqa: ANN001, ANN201
    url = f"{ADMIN}/users/{uid}{suffix}"
    if method == "DELETE":
        return client.delete(url, params={"confirm": username})
    return client.request(method, url, json=body)


@pytest.mark.parametrize(("method", "suffix", "body"), WRITES_ON_A_USER)
def test_an_admin_cannot_write_to_another_admin_or_to_root(
    method: str,
    suffix: str,
    body,
    admin_client,
    make_user,
    maker,  # noqa: ANN001
) -> None:
    peer = make_user("peer_admin", role="admin")
    refused = call(admin_client, method, peer.id, suffix, body, "peer_admin")
    assert refused.status_code == 403 and code_of(refused) == "admin_protected", refused.text
    root_id = user_id(maker, "jdqqjr")
    refused = call(admin_client, method, root_id, suffix, body, "jdqqjr")
    assert refused.status_code == 403 and code_of(refused) == "root_protected", refused.text


def test_an_admin_can_do_every_action_on_a_plain_user(admin_client, make_user, maker) -> None:  # noqa: ANN001
    victim = make_user("victim")
    assert admin_client.patch(f"{ADMIN}/users/{victim.id}", json={"displayName": "Vic"}).status_code == 200
    assert admin_client.patch(f"{ADMIN}/users/{victim.id}", json={"status": "disabled"}).status_code == 200
    assert admin_client.patch(f"{ADMIN}/users/{victim.id}", json={"status": "active"}).status_code == 200
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/reset-password").status_code == 200
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/revoke-sessions").status_code == 200
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/unlock").status_code == 204
    assert (
        admin_client.post(f"{ADMIN}/users/{victim.id}/quota", json={"resource": "report", "grant": 2}).status_code
        == 200
    )
    assert admin_client.delete(f"{ADMIN}/users/{victim.id}", params={"confirm": "victim"}).status_code == 204


def test_everyone_can_read_everyone(admin_client, make_user, maker) -> None:  # noqa: ANN001
    peer = make_user("peer_admin", role="admin")
    assert admin_client.get(f"{ADMIN}/users/{peer.id}").status_code == 200
    assert admin_client.get(f"{ADMIN}/users/{user_id(maker, 'jdqqjr')}").status_code == 200


def test_only_root_can_appoint_or_demote_admins(admin_client, root_client, make_user) -> None:  # noqa: ANN001
    target = make_user("candidate")
    refused = admin_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "admin"})
    assert refused.status_code == 403 and code_of(refused) == "admin_protected"

    promoted = root_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"
    # 任命立即生效：下一次请求就有管理员权限（角色每次读库，AC-157）
    # 降级同样
    demoted = root_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "user"})
    assert demoted.status_code == 200 and demoted.json()["role"] == "user"
    # root 不能被任何人"降级"：role 的取值里没有 root，且 root 作为目标一律受保护
    assert root_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "root"}).status_code == 422


def test_a_role_change_takes_effect_on_the_targets_next_request(
    root_client,
    auth_client,
    make_user,
    login_as,  # noqa: ANN001
) -> None:
    target = make_user("riser")
    session = _client(auth_client)
    login_as(session, target)
    assert session.get(f"{ADMIN}/overview").status_code == 403
    assert root_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "admin"}).status_code == 200
    assert session.get(f"{ADMIN}/overview").status_code == 200
    assert root_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "user"}).status_code == 200
    assert session.get(f"{ADMIN}/overview").status_code == 403


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("PATCH", "", {"status": "disabled"}),
        ("PATCH", "", {"role": "user"}),
        ("DELETE", "", None),
    ],
)
def test_nobody_may_disable_delete_or_demote_themselves(
    method: str,
    suffix: str,
    body,
    root_client,
    admin_client,
    maker,  # noqa: ANN001
) -> None:
    root_id = user_id(maker, "jdqqjr")
    refused = call(root_client, method, root_id, suffix, body, "jdqqjr")
    assert refused.status_code == 422 and code_of(refused) == "self_action_forbidden", refused.text
    admin_id = user_id(maker, "boss_admin")
    refused = call(admin_client, method, admin_id, suffix, body, "boss_admin")
    assert refused.status_code == 422 and code_of(refused) == "self_action_forbidden", refused.text


def test_root_may_manage_admins(root_client, make_user, maker) -> None:  # noqa: ANN001
    deputy = make_user("deputy", role="admin")
    assert root_client.post(f"{ADMIN}/users/{deputy.id}/reset-password").status_code == 200
    assert root_client.patch(f"{ADMIN}/users/{deputy.id}", json={"status": "disabled"}).status_code == 200
    assert root_client.delete(f"{ADMIN}/users/{deputy.id}", params={"confirm": "deputy"}).status_code == 204


def test_root_is_protected_from_an_admin_even_with_a_forged_role_in_the_body(
    admin_client,
    maker,  # noqa: ANN001
) -> None:
    root_id = user_id(maker, "jdqqjr")
    refused = admin_client.patch(f"{ADMIN}/users/{root_id}", json={"role": "user", "status": "disabled"})
    assert refused.status_code == 403 and code_of(refused) == "root_protected"
    with maker() as db:
        root = user_service.get_root(db)
        assert root.role == "root" and root.status == "active"


def test_a_half_allowed_patch_applies_nothing(admin_client, make_user, maker) -> None:  # noqa: ANN001
    """displayName 允许、role 不允许 → 整个请求 403，displayName 也不生效。"""
    target = make_user("halfway")
    refused = admin_client.patch(f"{ADMIN}/users/{target.id}", json={"displayName": "Changed", "role": "admin"})
    assert refused.status_code == 403
    with maker() as db:
        assert user_service.get_by_id(db, target.id).display_name is None


def test_a_user_gets_403_and_a_guest_401_on_the_admin_api(user_client, auth_client) -> None:  # noqa: ANN001
    assert user_client.get(f"{ADMIN}/users").status_code == 403
    assert user_client.post(f"{ADMIN}/users", json={"username": "x_x_x"}).status_code == 403
    assert _client(auth_client).get(f"{ADMIN}/users").status_code == 401


def test_unknown_user_ids_are_404(admin_client) -> None:  # noqa: ANN001
    ghost = "0" * 32
    assert admin_client.get(f"{ADMIN}/users/{ghost}").status_code == 404
    assert admin_client.patch(f"{ADMIN}/users/{ghost}", json={"displayName": "x"}).status_code == 404
    assert admin_client.post(f"{ADMIN}/users/{ghost}/unlock").status_code == 404
    assert admin_client.delete(f"{ADMIN}/users/{ghost}", params={"confirm": "x"}).status_code == 404


# ── 服务主体（AC-182 的 admin 部分）──────────────────────────────────────────


def test_the_api_token_principal_is_narrowed_on_the_admin_api(
    auth_client,
    monkeypatch,
    make_user,  # noqa: ANN001
) -> None:
    monkeypatch.setattr(get_settings(), "api_token", "service-token", raising=False)
    service = _client(auth_client)
    service.headers["Authorization"] = "Bearer service-token"
    target = make_user("somebody")

    for method, path, body in (
        ("POST", "/users", {"username": "by_service"}),
        ("PATCH", f"/users/{target.id}", {"status": "disabled"}),
        ("DELETE", f"/users/{target.id}?confirm=somebody", None),
        ("POST", f"/users/{target.id}/reset-password", None),
        ("GET", "/settings", None),
        ("PATCH", "/settings", {"signupBonus": 1}),
        ("GET", "/invites", None),
        ("POST", "/invites", {}),
        ("GET", "/quota-requests", None),
        ("POST", f"/users/{target.id}/quota", {"resource": "report", "grant": 1}),
    ):
        response = service.request(method, f"{ADMIN}{path}", json=body)
        assert response.status_code == 403 and code_of(response) == "forbidden", (method, path, response.text)
    for path in ("/overview", "/audit", "/featured-watchlist", "/users", f"/users/{target.id}"):
        assert service.get(f"{ADMIN}{path}").status_code == 200, path


# ── 重置密码 / 会话 / 解锁 / 停用（AC-157、AC-196）───────────────────────────


def test_reset_password_returns_a_new_password_once_and_logs_the_user_out_everywhere(
    admin_client,
    auth_client,
    make_user,
    login_as,
    maker,  # noqa: ANN001
) -> None:
    victim = make_user("forgetful")
    device_a, device_b = _client(auth_client), _client(auth_client)
    login_as(device_a, victim)
    login_as(device_b, victim)
    assert device_a.get(f"{AUTH}/me").json()["authenticated"] is True

    reset = admin_client.post(f"{ADMIN}/users/{victim.id}/reset-password")
    assert reset.status_code == 200
    temporary = reset.json()["temporaryPassword"]
    assert len(temporary) == 12

    assert device_a.get(f"{AUTH}/me").json()["authenticated"] is False
    assert device_b.get(f"{AUTH}/me").json()["authenticated"] is False
    old = _client(auth_client).post(f"{AUTH}/login", json={"username": "forgetful", "password": victim.password})
    assert old.status_code == 401
    fresh = _client(auth_client)
    login = fresh.post(f"{AUTH}/login", json={"username": "forgetful", "password": temporary})
    assert login.status_code == 200 and login.json()["user"]["mustChangePassword"] is True

    # 不可再读
    assert temporary not in admin_client.get(f"{ADMIN}/users/{victim.id}").text
    row = last_audit(maker, "auth.password.reset")
    assert row.target_name == "forgetful" and temporary not in (row.detail_json or "")
    with maker() as db:
        reasons = {
            s.revoked_reason
            for s in db.execute(select(UserSession).where(UserSession.user_id == victim.id)).scalars()
            if s.revoked_at
        }
    assert "admin" in reasons


def test_reset_password_also_clears_a_lockout(admin_client, make_user, maker) -> None:  # noqa: ANN001
    victim = make_user("lockedforgetful", failed_logins=5, locked_until=iso(clock.now() + timedelta(minutes=10)))
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/reset-password").status_code == 200
    with maker() as db:
        row = user_service.get_by_id(db, victim.id)
        assert row.failed_logins == 0 and row.locked_until is None


def test_revoke_sessions_kicks_every_device(admin_client, auth_client, make_user, login_as) -> None:  # noqa: ANN001
    victim = make_user("kickme")
    devices = [_client(auth_client) for _ in range(3)]
    for device in devices:
        login_as(device, victim)
    revoked = admin_client.post(f"{ADMIN}/users/{victim.id}/revoke-sessions")
    assert revoked.status_code == 200 and revoked.json() == {"revoked": 3}
    assert all(d.get(f"{AUTH}/me").json()["authenticated"] is False for d in devices)
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/revoke-sessions").json() == {"revoked": 0}


def test_a_single_session_can_be_revoked_and_only_if_it_belongs_to_the_user(
    admin_client,
    auth_client,
    make_user,
    login_as,  # noqa: ANN001
) -> None:
    """B-19：用户抽屉里逐条吊销。"""
    victim = make_user("twodevices")
    other = make_user("bystander")
    first, second, theirs = _client(auth_client), _client(auth_client), _client(auth_client)
    login_as(first, victim)
    login_as(second, victim)
    login_as(theirs, other)
    detail = admin_client.get(f"{ADMIN}/users/{victim.id}").json()
    assert len(detail["sessions"]) == 2
    their_session = admin_client.get(f"{ADMIN}/users/{other.id}").json()["sessions"][0]["id"]

    # 别人的会话 id → 404，且不会被吊销
    assert admin_client.delete(f"{ADMIN}/users/{victim.id}/sessions/{their_session}").status_code == 404
    assert theirs.get(f"{AUTH}/me").json()["authenticated"] is True

    target_session = detail["sessions"][0]["id"]
    assert admin_client.delete(f"{ADMIN}/users/{victim.id}/sessions/{target_session}").status_code == 204
    assert len(admin_client.get(f"{ADMIN}/users/{victim.id}").json()["sessions"]) == 1
    assert admin_client.delete(f"{ADMIN}/users/{victim.id}/sessions/{target_session}").status_code == 404
    alive = [c.get(f"{AUTH}/me").json()["authenticated"] for c in (first, second)]
    assert sorted(alive) == [False, True]


def test_unlock_lets_a_locked_user_back_in(admin_client, auth_client, make_user, maker) -> None:  # noqa: ANN001
    victim = make_user("lockedout", failed_logins=5, locked_until=iso(clock.now() + timedelta(minutes=15)))
    locked = _client(auth_client).post(f"{AUTH}/login", json={"username": "lockedout", "password": victim.password})
    assert locked.status_code == 423
    detail = admin_client.get(f"{ADMIN}/users/{victim.id}").json()
    assert detail["locked"] is True and detail["failedLogins"] >= 5 and detail["lockedUntil"]
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/unlock").status_code == 204
    ok = _client(auth_client).post(f"{AUTH}/login", json={"username": "lockedout", "password": victim.password})
    assert ok.status_code == 200
    assert last_audit(maker, "user.unlock").target_name == "lockedout"


def test_disabling_takes_effect_on_the_very_next_request_and_enabling_restores(
    admin_client,
    auth_client,
    make_user,
    login_as,
    maker,  # noqa: ANN001
) -> None:
    victim = make_user("suspect")
    session = _client(auth_client)
    login_as(session, victim)
    assert session.get(f"{AUTH}/me").json()["authenticated"] is True

    patched = admin_client.patch(f"{ADMIN}/users/{victim.id}", json={"status": "disabled", "disabledReason": "刷量"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "disabled" and patched.json()["disabledReason"] == "刷量"
    assert session.get(f"{AUTH}/me").json()["authenticated"] is False
    blocked = _client(auth_client).post(f"{AUTH}/login", json={"username": "suspect", "password": victim.password})
    assert blocked.status_code == 403 and code_of(blocked) == "account_disabled"

    assert admin_client.patch(f"{ADMIN}/users/{victim.id}", json={"status": "active"}).status_code == 200
    assert (
        _client(auth_client)
        .post(f"{AUTH}/login", json={"username": "suspect", "password": victim.password})
        .status_code
        == 200
    )
    actions = audit_actions(maker)
    assert "user.disable" in actions and "user.enable" in actions
    assert json.loads(last_audit(maker, "user.disable").detail_json)["reason"] == "刷量"


def test_changing_to_the_current_value_is_a_quiet_no_op(admin_client, make_user, maker) -> None:  # noqa: ANN001
    target = make_user("steady")
    before = len(audit_actions(maker))
    assert (
        admin_client.patch(f"{ADMIN}/users/{target.id}", json={"status": "active", "role": "user"}).status_code == 200
    )
    assert len(audit_actions(maker)) == before


def test_role_change_is_audited_with_from_and_to(root_client, make_user, maker) -> None:  # noqa: ANN001
    target = make_user("audited")
    root_client.patch(f"{ADMIN}/users/{target.id}", json={"role": "admin"})
    row = last_audit(maker, "user.role.change")
    assert json.loads(row.detail_json) == {"from": "user", "to": "admin"} and row.actor_role == "root"


# ── 删除 = 匿名化（AC-197）──────────────────────────────────────────────────


def test_delete_requires_the_exact_username_and_applies_nothing_otherwise(
    admin_client,
    make_user,
    maker,  # noqa: ANN001
) -> None:
    victim = make_user("typo_target")
    for bad in (None, "", "typo_targe", "TYPO_TARGET", "someone_else"):
        response = admin_client.delete(
            f"{ADMIN}/users/{victim.id}", params={"confirm": bad} if bad is not None else None
        )
        assert response.status_code == 422 and response.json()["error"]["detail"] == {"field": "confirm"}, bad
    with maker() as db:
        assert user_service.get_by_id(db, victim.id).status == "active"
    # body 形式也行
    ok = admin_client.request("DELETE", f"{ADMIN}/users/{victim.id}", json={"confirm": "typo_target"})
    assert ok.status_code == 204


def test_delete_anonymises_but_keeps_ledger_and_audit(
    admin_client,
    auth_client,
    make_user,
    login_as,
    maker,  # noqa: ANN001
) -> None:
    victim = make_user("leaver")
    session = _client(auth_client)
    login_as(session, victim)
    with maker() as db:
        db.add(
            QuotaLedger(
                user_id=victim.id,
                resource="report",
                delta=3,
                reason="admin_grant",
                source="bonus",
                day_key="2026-01-01",
                created_at="2026-01-01T00:00:00Z",
            )
        )
        db.commit()
    assert admin_client.delete(f"{ADMIN}/users/{victim.id}", params={"confirm": "leaver"}).status_code == 204

    with maker() as db:
        row = user_service.get_by_id(db, victim.id)
        assert row.status == "deleted" and row.password_hash == "!"
        assert row.username.startswith("deleted_") and row.username_lc == f"deleted:{victim.id}"
        assert db.execute(select(QuotaLedger).where(QuotaLedger.user_id == victim.id)).scalars().all()
        assert (
            db.execute(
                select(UserSession).where(UserSession.user_id == victim.id, UserSession.revoked_at.is_(None))
            ).first()
            is None
        )
    assert last_audit(maker, "user.delete").target_name == "leaver"
    assert session.get(f"{AUTH}/me").json()["authenticated"] is False
    assert (
        _client(auth_client).post(f"{AUTH}/login", json={"username": "leaver", "password": victim.password}).status_code
        == 401
    )

    # 用户名可被再次注册（这里用管理员创建）
    again = admin_client.post(f"{ADMIN}/users", json={"username": "leaver"})
    assert again.status_code == 201 and again.json()["user"]["id"] != victim.id
    # 已注销的账号不再可改
    assert admin_client.patch(f"{ADMIN}/users/{victim.id}", json={"displayName": "x"}).status_code == 404
    assert admin_client.post(f"{ADMIN}/users/{victim.id}/reset-password").status_code == 404


def test_delete_rejects_the_pending_quota_request_of_the_leaver(admin_client, make_user, maker) -> None:  # noqa: ANN001
    from app.models import QuotaRequest

    victim = make_user("requester")
    with maker() as db:
        db.add(
            QuotaRequest(
                id=uuid.uuid4().hex,
                user_id=victim.id,
                resource="report",
                amount=3,
                reason="please",
                status="pending",
                created_at=iso(clock.now()),
            )
        )
        db.commit()
    assert admin_client.get(f"{ADMIN}/quota-requests").json()["total"] == 1
    admin_client.delete(f"{ADMIN}/users/{victim.id}", params={"confirm": "requester"})
    assert admin_client.get(f"{ADMIN}/quota-requests").json()["total"] == 0


# ── 额度调整 ────────────────────────────────────────────────────────────────


def test_quota_grant_limit_and_take_back(admin_client, make_user, maker) -> None:  # noqa: ANN001
    target = make_user("budgeted")
    url = f"{ADMIN}/users/{target.id}/quota"
    granted = admin_client.post(url, json={"resource": "report", "grant": 7, "note": "活动"})
    assert granted.status_code == 200
    assert granted.json()["report"]["bonus"] == 7

    limited = admin_client.post(url, json={"resource": "report", "dailyLimit": 10})
    assert limited.json()["report"]["limit"] == 10
    unlimited = admin_client.post(url, json={"resource": "report", "dailyLimit": -1})
    assert unlimited.json()["report"]["limit"] is None
    followed = admin_client.post(url, json={"resource": "report", "dailyLimit": None})  # 跟随默认
    assert followed.json()["report"]["limit"] == 3
    both = admin_client.post(url, json={"resource": "deep_ask", "grant": 2, "dailyLimit": 4})
    assert both.json()["deepAsk"]["limit"] == 4 and both.json()["deepAsk"]["bonus"] == 2

    # 扣回不会把余额扣成负数
    taken = admin_client.post(url, json={"resource": "report", "grant": -50})
    assert taken.status_code == 200 and taken.json()["report"]["bonus"] == 0
    # 已为 0 的扣回：什么都没发生，也不记审计
    n = audit_actions(maker).count("quota.grant")
    assert admin_client.post(url, json={"resource": "report", "grant": -5}).status_code == 200
    assert audit_actions(maker).count("quota.grant") == n

    with maker() as db:
        rows = db.execute(select(QuotaLedger).where(QuotaLedger.user_id == target.id)).scalars().all()
    assert {row.reason for row in rows} == {"admin_grant", "admin_revoke"}
    assert all(row.actor_id for row in rows)
    actions = audit_actions(maker)
    assert "quota.grant" in actions and "quota.limit.set" in actions


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"resource": "report"},
        {"resource": "report", "grant": 0},
        {"resource": "report", "grant": 51},
        {"resource": "report", "grant": -51},
        {"resource": "bogus", "grant": 1},
        {"resource": "report", "dailyLimit": -2},
    ],
)
def test_quota_body_validation(admin_client, make_user, body: dict) -> None:  # noqa: ANN001
    target = make_user("validated")
    assert admin_client.post(f"{ADMIN}/users/{target.id}/quota", json=body).status_code == 422


# ── 邀请码（AC-199 的一部分）────────────────────────────────────────────────


def test_invite_lifecycle(admin_client, auth_client, solve_captcha, maker, frozen_now) -> None:  # noqa: ANN001
    created = admin_client.post(
        f"{ADMIN}/invites", json={"maxUses": 2, "expiresInDays": 7, "bonusCredits": 3, "note": "春季"}
    )
    assert created.status_code == 201, created.text
    invite = created.json()
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", invite["code"])
    assert invite["status"] == "active" and invite["usedCount"] == 0 and invite["createdBy"] == "boss_admin"
    assert invite["expiresAt"] and invite["bonusCredits"] == 3

    listed = admin_client.get(f"{ADMIN}/invites").json()["items"]
    assert [row["id"] for row in listed] == [invite["id"]]

    # 审计里只有打码的邀请码
    audited = last_audit(maker, "invite.create")
    assert invite["code"] not in json.dumps([audited.target_name, audited.detail_json])
    assert audited.target_name.endswith("****")

    # 撤销后注册失败；已注册的人不受影响
    signup = _client(auth_client)
    ok = signup.post(
        f"{AUTH}/register", json={"username": "early_bird", "password": "Aragon-Test-9x", "inviteCode": invite["code"]}
    )
    assert ok.status_code == 201
    assert admin_client.get(f"{ADMIN}/invites").json()["items"][0]["usedCount"] == 1

    assert admin_client.delete(f"{ADMIN}/invites/{invite['id']}").status_code == 204
    assert admin_client.get(f"{ADMIN}/invites").json()["items"][0]["status"] == "revoked"
    late = _client(auth_client).post(
        f"{AUTH}/register", json={"username": "latecomer", "password": "Aragon-Test-9x", "inviteCode": invite["code"]}
    )
    assert late.status_code == 422 and code_of(late) == "invite_invalid"
    assert signup.get(f"{AUTH}/me").json()["authenticated"] is True
    # 幂等：再撤销一次仍是 204，且只记一条审计
    assert admin_client.delete(f"{ADMIN}/invites/{invite['id']}").status_code == 204
    assert audit_actions(maker).count("invite.revoke") == 1


def test_invite_statuses_exhausted_and_expired(admin_client, maker, frozen_now) -> None:  # noqa: ANN001
    spent = admin_client.post(f"{ADMIN}/invites", json={"maxUses": 1}).json()
    old = admin_client.post(f"{ADMIN}/invites", json={"maxUses": 5, "expiresInDays": 1}).json()
    with maker() as db:
        db.get(InviteCode, spent["id"]).used_count = 1
        db.get(InviteCode, old["id"]).expires_at = iso(frozen_now.current - timedelta(minutes=1))
        db.commit()
    states = {row["id"]: row["status"] for row in admin_client.get(f"{ADMIN}/invites").json()["items"]}
    assert states[spent["id"]] == "exhausted" and states[old["id"]] == "expired"


@pytest.mark.parametrize(
    "body",
    [
        {"maxUses": 0},
        {"maxUses": 101},
        {"expiresInDays": 0},
        {"expiresInDays": 91},
        {"bonusCredits": 21},
        {"bonusCredits": -1},
    ],
)
def test_invite_bounds(admin_client, body: dict) -> None:  # noqa: ANN001
    assert admin_client.post(f"{ADMIN}/invites", json=body).status_code == 422


def test_revoking_an_unknown_invite_is_404(admin_client) -> None:  # noqa: ANN001
    assert admin_client.delete(f"{ADMIN}/invites/{'0' * 32}").status_code == 404


def test_invites_never_grant_a_role(admin_client, auth_client) -> None:  # noqa: ANN001
    """AC-177：邀请码注册永远只产生 user。"""
    invite = admin_client.post(f"{ADMIN}/invites", json={}).json()
    response = _client(auth_client).post(
        f"{AUTH}/register",
        json={"username": "role_hopeful", "password": "Aragon-Test-9x", "inviteCode": invite["code"], "role": "admin"},
    )
    assert response.status_code == 201 and response.json()["user"]["role"] == "user"


# ── 设置（AC-199）────────────────────────────────────────────────────────────


def test_settings_defaults_and_roundtrip(admin_client, maker) -> None:  # noqa: ANN001
    assert admin_client.get(f"{ADMIN}/settings").json() == {
        "registrationMode": "open",
        "defaultDailyReport": 3,
        "defaultDailyDeepAsk": 10,
        "signupBonus": 0,
        "siteDailyCap": 60,
        "authRecoveryEnabled": True,
    }
    patched = admin_client.patch(
        f"{ADMIN}/settings",
        json={"registrationMode": "invite", "defaultDailyReport": 5, "signupBonus": 2, "siteDailyCap": 0},
    )
    assert patched.status_code == 200
    assert patched.json() == {
        "registrationMode": "invite",
        "defaultDailyReport": 5,
        "defaultDailyDeepAsk": 10,
        "signupBonus": 2,
        "siteDailyCap": 0,
        "authRecoveryEnabled": True,
    }
    row = last_audit(maker, "settings.update")
    changes = json.loads(row.detail_json)["changes"]
    assert changes["registrationMode"] == {"from": "open", "to": "invite"}
    assert changes["defaultDailyReport"] == {"from": 3, "to": 5}
    assert "defaultDailyDeepAsk" not in changes
    # 没有真正变化的 PATCH 不记审计
    n = audit_actions(maker).count("settings.update")
    admin_client.patch(f"{ADMIN}/settings", json={"signupBonus": 2})
    assert audit_actions(maker).count("settings.update") == n


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("registrationMode", "everyone"),
        ("registrationMode", 1),
        ("registrationMode", None),
        ("defaultDailyReport", -1),
        ("defaultDailyReport", 101),
        ("defaultDailyReport", "3"),
        ("defaultDailyReport", 3.5),
        ("defaultDailyReport", True),
        ("defaultDailyReport", None),
        ("defaultDailyDeepAsk", 201),
        ("defaultDailyDeepAsk", -1),
        ("signupBonus", 21),
        ("signupBonus", -1),
        ("siteDailyCap", 10001),
        ("siteDailyCap", -1),
        ("notASetting", 1),
    ],
)
def test_each_setting_is_validated_on_its_own(admin_client, field: str, value) -> None:  # noqa: ANN001
    response = admin_client.patch(f"{ADMIN}/settings", json={field: value})
    assert response.status_code == 422, response.text
    assert code_of(response) == "invalid_setting"
    assert response.json()["error"]["detail"] == {"field": field}


def test_a_bad_field_rejects_the_whole_patch(admin_client, maker) -> None:  # noqa: ANN001
    response = admin_client.patch(f"{ADMIN}/settings", json={"signupBonus": 5, "siteDailyCap": 99999})
    assert response.status_code == 422 and response.json()["error"]["detail"] == {"field": "siteDailyCap"}
    assert admin_client.get(f"{ADMIN}/settings").json()["signupBonus"] == 0
    assert admin_client.patch(f"{ADMIN}/settings", json={}).status_code == 422
    assert admin_client.patch(f"{ADMIN}/settings", json=[1]).status_code == 422


@pytest.mark.parametrize(
    "boundary",
    [
        ("defaultDailyReport", 0),
        ("defaultDailyReport", 100),
        ("defaultDailyDeepAsk", 200),
        ("signupBonus", 20),
        ("siteDailyCap", 10000),
        ("siteDailyCap", 0),
    ],
)
def test_setting_boundaries_are_inclusive(admin_client, boundary: tuple[str, int]) -> None:  # noqa: ANN001
    assert admin_client.patch(f"{ADMIN}/settings", json={boundary[0]: boundary[1]}).status_code == 200


def test_registration_modes_take_effect_without_a_restart(
    admin_client,
    auth_client,
    solve_captcha,
    maker,
    frozen_now,  # noqa: ANN001
) -> None:
    """AC-199：open / invite / closed 各一条端到端；改设置后下一次请求即生效。"""

    def try_register(name: str, **extra):  # noqa: ANN202
        client = _client(auth_client)
        challenge_id, answer = solve_captcha(client, "register")
        return client.post(
            f"{AUTH}/register",
            json={
                "username": name,
                "password": "Aragon-Test-9x",
                "captchaId": challenge_id,
                "captchaAnswer": answer,
                **extra,
            },
        )

    assert try_register("mode_open").status_code == 201

    admin_client.patch(f"{ADMIN}/settings", json={"registrationMode": "invite"})
    refused = try_register("mode_invite_noinvite")
    assert refused.status_code == 422 and code_of(refused) == "invite_required"
    code = admin_client.post(f"{ADMIN}/invites", json={}).json()["code"]
    assert try_register("mode_invite_ok", inviteCode=code).status_code == 201

    admin_client.patch(f"{ADMIN}/settings", json={"registrationMode": "closed"})
    closed = try_register("mode_closed")
    assert closed.status_code == 403 and code_of(closed) == "registration_closed"
    assert _client(auth_client).get(f"{AUTH}/config").json()["registrationMode"] == "closed"

    admin_client.patch(f"{ADMIN}/settings", json={"registrationMode": "open"})
    assert try_register("mode_open_again").status_code == 201


def test_the_signup_bonus_setting_is_what_new_users_get(
    admin_client,
    auth_client,
    solve_captcha,
    maker,
    frozen_now,  # noqa: ANN001
) -> None:  # noqa: ANN001
    admin_client.patch(f"{ADMIN}/settings", json={"signupBonus": 4})
    client = _client(auth_client)
    challenge_id, answer = solve_captcha(client, "register")
    created = client.post(
        f"{AUTH}/register",
        json={
            "username": "bonus_baby",
            "password": "Aragon-Test-9x",
            "captchaId": challenge_id,
            "captchaAnswer": answer,
        },
    )
    assert created.status_code == 201
    uid = created.json()["user"]["id"]
    with maker() as db:
        total = db.execute(select(QuotaLedger.delta).where(QuotaLedger.user_id == uid)).scalars().all()
    assert sum(total) == 4


def test_the_default_daily_limit_setting_reaches_the_list(admin_client, make_user) -> None:  # noqa: ANN001
    make_user("plain_follower")
    admin_client.patch(f"{ADMIN}/settings", json={"defaultDailyReport": 9})
    items = {r["username"]: r for r in admin_client.get(f"{ADMIN}/users", params={"limit": 200}).json()["items"]}
    assert items["plain_follower"]["quota"]["report"]["limit"] == 9


def test_settings_are_stored_under_the_documented_keys(admin_client, maker) -> None:  # noqa: ANN001
    admin_client.patch(
        f"{ADMIN}/settings",
        json={
            "registrationMode": "closed",
            "defaultDailyReport": 8,
            "defaultDailyDeepAsk": 11,
            "signupBonus": 1,
            "siteDailyCap": 77,
        },
    )
    with maker() as db:
        stored = {row.key: row.value for row in db.execute(select(Setting)).scalars()}
    assert stored["auth.registration_mode"] == "closed"
    assert stored["quota.default_daily.report"] == "8"
    assert stored["quota.default_daily.deep_ask"] == "11"
    assert stored["quota.signup_bonus"] == "1"
    assert stored["quota.site_daily_cap"] == "77"


# ── 审计（只读，AC-198）─────────────────────────────────────────────────────


def test_audit_listing_filters_and_pages(admin_client, make_user, maker, frozen_now) -> None:  # noqa: ANN001
    victim = make_user("audited_one")
    for _ in range(3):
        frozen_now.advance(seconds=5)
        admin_client.post(f"{ADMIN}/users/{victim.id}/unlock")
    body = admin_client.get(f"{ADMIN}/audit", params={"action": "user.unlock"}).json()
    assert len(body["items"]) == 3 and body["nextCursor"] is None
    first = body["items"][0]
    assert first["actorName"] == "boss_admin" and first["targetName"] == "audited_one"
    assert first["actorRole"] == "admin" and first["targetType"] == "user"
    assert [r["at"] for r in body["items"]] == sorted((r["at"] for r in body["items"]), reverse=True)

    page_one = admin_client.get(f"{ADMIN}/audit", params={"action": "user.unlock", "limit": 2}).json()
    assert len(page_one["items"]) == 2 and page_one["nextCursor"]
    page_two = admin_client.get(
        f"{ADMIN}/audit", params={"action": "user.unlock", "limit": 2, "before": page_one["nextCursor"]}
    ).json()
    assert len(page_two["items"]) == 1 and page_two["nextCursor"] is None
    assert {r["id"] for r in page_one["items"]}.isdisjoint(r["id"] for r in page_two["items"])

    assert admin_client.get(f"{ADMIN}/audit", params={"actor": "boss_admin", "target": "audited_one"}).json()["items"]
    assert admin_client.get(f"{ADMIN}/audit", params={"actor": "nobody"}).json()["items"] == []
    cutoff = iso(frozen_now.current - timedelta(seconds=7))
    recent = admin_client.get(f"{ADMIN}/audit", params={"action": "user.unlock", "from": cutoff}).json()["items"]
    assert len(recent) == 2
    old = admin_client.get(f"{ADMIN}/audit", params={"action": "user.unlock", "to": cutoff}).json()["items"]
    assert len(old) == 1


def test_audit_rejects_unknown_actions_and_bad_bounds(admin_client) -> None:  # noqa: ANN001
    assert code_of(admin_client.get(f"{ADMIN}/audit", params={"action": "user.explode"})) == "invalid_query"
    assert code_of(admin_client.get(f"{ADMIN}/audit", params={"from": "yesterday"})) == "invalid_query"
    assert code_of(admin_client.get(f"{ADMIN}/audit", params={"before": "nope"})) == "invalid_cursor"


def test_audit_detail_is_parsed_json_and_never_carries_secrets(admin_client, make_user, maker) -> None:  # noqa: ANN001
    victim = make_user("sensitive")
    temporary = admin_client.post(f"{ADMIN}/users/{victim.id}/reset-password").json()["temporaryPassword"]
    body = admin_client.get(f"{ADMIN}/audit", params={"action": "auth.password.reset"}).json()
    assert isinstance(body["items"][0]["detail"], dict)
    dump = json.dumps(admin_client.get(f"{ADMIN}/audit", params={"limit": 200}).json())
    for secret in (temporary, victim.password, "scrypt$"):
        assert secret not in dump


def test_the_audit_api_is_read_only(auth_client) -> None:  # noqa: ANN001
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = _client(auth_client).request(method, f"{ADMIN}/audit")
        assert response.status_code in (401, 404, 405), method
    from app.main import app

    audit_routes = [r for r in app.routes if "audit" in getattr(r, "path", "") and r.path.startswith("/api/v1/admin")]
    assert audit_routes and all(set(r.methods) <= {"GET", "HEAD"} for r in audit_routes)


# ── 精选自选 ────────────────────────────────────────────────────────────────


def test_featured_watchlist_add_list_remove_and_audit(admin_client, instrument, maker, user_client) -> None:  # noqa: ANN001
    assert admin_client.get(f"{ADMIN}/featured-watchlist").json() == {"items": []}
    added = admin_client.post(f"{ADMIN}/featured-watchlist", json={"instrumentId": instrument.id})
    assert added.status_code == 201
    items = added.json()["items"]
    assert [row["instrument"]["id"] for row in items] == [instrument.id]
    assert items[0]["instrument"]["symbol"] == "600519.SH"
    # 再加一次：安静地不重复，也不重复审计
    again = admin_client.post(f"{ADMIN}/featured-watchlist", json={"instrumentId": instrument.symbol})
    assert again.status_code == 201 and len(again.json()["items"]) == 1
    assert audit_actions(maker).count("featured.add") == 1

    assert user_client.get(f"{ADMIN}/featured-watchlist").status_code == 403
    assert admin_client.delete(f"{ADMIN}/featured-watchlist/{instrument.id}").status_code == 204
    assert admin_client.get(f"{ADMIN}/featured-watchlist").json() == {"items": []}
    assert last_audit(maker, "featured.remove").target_name == "600519.SH"
    assert admin_client.delete(f"{ADMIN}/featured-watchlist/{instrument.id}").status_code == 404
    assert admin_client.post(f"{ADMIN}/featured-watchlist", json={"instrumentId": "NOPE:0"}).status_code == 404
    with maker() as db:
        assert db.execute(select(WatchlistItem)).first() is None


# ── 额度申请队列 ────────────────────────────────────────────────────────────


def seed_request(maker, user, amount: int = 5, created: str | None = None) -> str:  # noqa: ANN001
    from app.models import QuotaRequest

    rid = uuid.uuid4().hex
    with maker() as db:
        db.add(
            QuotaRequest(
                id=rid,
                user_id=user.id,
                resource="report",
                amount=amount,
                reason="需要更多额度做研究",
                status="pending",
                created_at=created or iso(clock.now()),
            )
        )
        db.commit()
    return rid


def test_quota_request_queue_and_resolution(admin_client, make_user, instrument, maker, frozen_now) -> None:  # noqa: ANN001
    asker = make_user("asker")
    other = make_user("other_asker")
    with maker() as db:
        seed_run(db, instrument, "succeeded", iso(frozen_now.current), asker.id)
        seed_run(db, instrument, "failed", iso(frozen_now.current), asker.id)
        db.commit()
    first = seed_request(maker, asker, 5, iso(frozen_now.current))
    frozen_now.advance(seconds=1)
    second = seed_request(maker, other, 2, iso(frozen_now.current))

    queue = admin_client.get(f"{ADMIN}/quota-requests").json()
    assert queue["total"] == 2 and [row["id"] for row in queue["items"]] == [first, second]
    row = queue["items"][0]
    assert row["user"] == {"id": asker.id, "username": "asker", "displayName": None}
    assert row["userRuns7d"] == 2 and row["resource"] == "report" and row["amount"] == 5

    approved = admin_client.post(f"{ADMIN}/quota-requests/{first}/resolve", json={"decision": "approve", "grant": 3})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["granted"] == 3
    with maker() as db:
        rows = db.execute(select(QuotaLedger).where(QuotaLedger.user_id == asker.id)).scalars().all()
    assert [(r.delta, r.reason, r.source) for r in rows] == [(3, "request_approved", "bonus")]

    rejected = admin_client.post(
        f"{ADMIN}/quota-requests/{second}/resolve", json={"decision": "reject", "note": "本周已有额度"}
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["resolutionNote"] == "本周已有额度"
    assert admin_client.get(f"{ADMIN}/quota-requests").json()["total"] == 0
    approved_list = admin_client.get(f"{ADMIN}/quota-requests", params={"status": "approved"}).json()
    assert [r["id"] for r in approved_list["items"]] == [first]

    actions = audit_actions(maker)
    assert "quota.request.approve" in actions and "quota.request.reject" in actions
    detail = json.loads(last_audit(maker, "quota.request.approve").detail_json)
    assert detail["granted"] == 3 and detail["requested"] == 5


def test_quota_request_resolution_rules(admin_client, make_user, maker) -> None:  # noqa: ANN001
    asker = make_user("rules_asker")
    rid = seed_request(maker, asker)
    url = f"{ADMIN}/quota-requests/{rid}/resolve"
    assert admin_client.post(url, json={"decision": "reject"}).status_code == 422  # 拒绝必须附理由
    assert admin_client.post(url, json={"decision": "reject", "note": "  "}).status_code == 422
    assert admin_client.post(url, json={"decision": "approve", "grant": 21}).status_code == 422
    assert admin_client.post(url, json={"decision": "maybe"}).status_code == 422
    assert (
        admin_client.post(f"{ADMIN}/quota-requests/{'0' * 32}/resolve", json={"decision": "approve"}).status_code == 404
    )
    assert admin_client.post(url, json={"decision": "approve"}).status_code == 200
    assert admin_client.post(url, json={"decision": "approve"}).status_code == 409  # 不能重复处理


def test_the_overview_counts_pending_quota_requests(admin_client, make_user, maker) -> None:  # noqa: ANN001
    seed_request(maker, make_user("pending_one"))
    assert admin_client.get(f"{ADMIN}/overview").json()["pendingQuotaRequests"] == 1


# ── 详情 ────────────────────────────────────────────────────────────────────


def test_user_detail_shape(admin_client, auth_client, make_user, login_as, instrument, maker, frozen_now) -> None:  # noqa: ANN001
    target = make_user("detailed", display_name="详情")
    session = _client(auth_client)
    login_as(session, target)
    with maker() as db:
        for n in range(25):
            seed_run(db, instrument, "succeeded", iso(frozen_now.current - timedelta(minutes=n)), target.id)
        db.commit()
    detail = admin_client.get(f"{ADMIN}/users/{target.id}").json()
    assert detail["username"] == "detailed" and detail["displayName"] == "详情"
    assert len(detail["recentRuns"]) == 20  # 近 20 次
    assert detail["recentRuns"][0]["symbol"] == "600519.SH"
    assert detail["recentRuns"][0]["startedAt"] >= detail["recentRuns"][-1]["startedAt"]
    assert (
        len(detail["sessions"]) == 1
        and "tokenHash" not in json.dumps(detail)
        and "token_hash" not in json.dumps(detail)
    )
    assert set(detail["quotaStatus"]) >= {"report", "deepAsk", "tz"}
    assert detail["failedLogins"] == 0 and detail["lockedUntil"] is None
    assert detail["mustChangePassword"] is False and detail["passwordIsDefault"] is False
    assert "passwordHash" not in detail and "password" not in json.dumps(detail).lower().replace(
        "mustchangepassword", ""
    ).replace("passwordisdefault", "")


# ── AUTH_MODE=open：服务主体拥有全部权限，不被矩阵二次拦截 ───────────────────


def test_in_open_mode_the_service_principal_can_run_the_whole_admin_api(client) -> None:  # noqa: ANN001
    made = client.post(f"{ADMIN}/users", json={"username": "open_made", "role": "admin"})
    assert made.status_code == 201 and made.json()["user"]["role"] == "admin"
    uid = made.json()["user"]["id"]
    assert client.get(f"{ADMIN}/users").json()["total"] == 1
    assert client.patch(f"{ADMIN}/users/{uid}", json={"role": "user"}).status_code == 200
    assert client.post(f"{ADMIN}/users/{uid}/reset-password").status_code == 200
    assert client.patch(f"{ADMIN}/settings", json={"signupBonus": 1}).status_code == 200
    assert client.get(f"{ADMIN}/overview").status_code == 200
    assert client.delete(f"{ADMIN}/users/{uid}", params={"confirm": "open_made"}).status_code == 204
    # 审计里的 actor 是 service（没有用户 id）
    rows = client.get(f"{ADMIN}/audit", params={"action": "user.create"}).json()["items"]
    assert rows[0]["actorName"] == "service" and rows[0]["actorId"] is None
