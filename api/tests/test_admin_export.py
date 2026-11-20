"""管理效率（spec §25.3.5；AC-229 – AC-234 的后端半边、AC-245 – AC-247）。

三件事：CSV 导出（防注入、限量、留痕、取数机制）、批量赠送额度（两遍制、全成全败）、概览的
`security24h`（失败类按 `Σ detail.count`，不是数行）。世界与 `test_admin_api.py` 同构：一个 `enforced`
应用，root / admin / user 各有自己的 Cookie 罐。
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.base import iso
from app.models import AuditLog, QuotaLedger, User
from app.security import clock
from app.security.principal import user_principal
from app.services import admin_overview, admin_user_service, audit_service, export_service, user_service

ADMIN = "/api/v1/admin"
AUTH = "/api/v1/auth"
ROOT_PASSWORD = "13519126390"
BOM = "﻿"


# ── 夹具 ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _client(auth_client: TestClient) -> TestClient:
    return TestClient(auth_client.app)


@pytest.fixture()
def root_client(auth_client, maker) -> TestClient:  # noqa: ANN001
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


def parse_csv(response) -> list[list[str]]:  # noqa: ANN001
    text = response.content.decode("utf-8")
    assert text.startswith(BOM)
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def audit_rows(maker, action: str) -> list[AuditLog]:  # noqa: ANN001
    with maker() as db:
        return list(db.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).scalars())


def bulk_insert_users(maker, count: int, *, prefix: str = "bulk") -> list[str]:  # noqa: ANN001
    """`count` active users in one statement (a loop of `make_user` would take minutes)."""
    now = iso(clock.now())
    ids = [uuid.uuid4().hex for _ in range(count)]
    rows = [
        {
            "id": user_id,
            "username": f"{prefix}{i:05d}",
            "username_lc": f"{prefix}{i:05d}",
            "password_hash": "x",
            "role": "user",
            "status": "active",
            "must_change_password": 0,
            "password_is_default": 0,
            "failed_logins": 0,
            "created_at": iso(clock.now() - timedelta(seconds=count - i)),
            "updated_at": now,
        }
        for i, user_id in enumerate(ids)
    ]
    with maker() as db:
        db.execute(User.__table__.insert(), rows)
        db.commit()
    return ids


# ── csv_safe（AC-230）─────────────────────────────────────────────────────────


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_safe_defuses_every_formula_prefix(prefix: str) -> None:
    value = f'{prefix}HYPERLINK("http://evil","x")'
    assert export_service.csv_safe(value) == "'" + value
    assert export_service.csv_safe("ordinary") == "ordinary"
    assert export_service.csv_safe(None) == ""
    assert export_service.csv_safe(True) == "true"
    assert export_service.csv_safe(-1) == -1  # numbers are not text cells


def test_injection_and_rfc4180_escaping_survive_a_csv_round_trip(root_client, make_user) -> None:  # noqa: ANN001
    """AC-230：以 `= + - @ TAB CR` 开头的单元格导出后以 `'` 开头；逗号/引号/换行按 RFC 4180 转义。"""
    names = {
        "inj_eq": "=1+1",
        "inj_plus": "+SUM(A1)",
        "inj_minus": "-2+3",
        "inj_at": "@cmd",
        "inj_tab": "\tindent",
        "inj_cr": "\rreturn",
        "tricky": 'a,"b",\nc',
    }
    for username, display in names.items():
        make_user(username, display_name=display)
    rows = parse_csv(root_client.get(f"{ADMIN}/users/export.csv"))
    header, body = rows[0], rows[1:]
    by_name = {row[header.index("username")]: row[header.index("displayName")] for row in body}
    for username, display in names.items():
        if display[0] in "=+-@\t\r":
            assert by_name[username] == "'" + display, username
        else:
            assert by_name[username] == display
    assert not any(cell.startswith(("=", "+", "@", "\t", "\r")) for row in body for cell in row)


# ── 导出正确性（AC-229）───────────────────────────────────────────────────────


def test_users_export_matches_the_list_row_for_row(root_client, make_user) -> None:  # noqa: ANN001
    """AC-229：BOM；表头与 §25.3.5(a) 完全一致；行数与列表接口在同一过滤下逐条相等；文件名含当日。"""
    for i in range(7):
        make_user(f"member{i}")
    make_user("moderator", role="admin")
    response = root_client.get(f"{ADMIN}/users/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "X-Export-Truncated" not in response.headers and "x-export-truncated" not in response.headers
    day = clock.local_day_key(clock.now(), get_settings().quota_tz).replace("-", "")
    assert f'filename="aragon-users-{day}.csv"' in response.headers["content-disposition"]
    rows = parse_csv(response)
    assert tuple(rows[0]) == export_service.USER_COLUMNS
    listed = root_client.get(f"{ADMIN}/users", params={"limit": 200}).json()
    assert [row[0] for row in rows[1:]] == [item["username"] for item in listed["items"]]
    assert len(rows) - 1 == listed["total"]
    # 同一过滤下依然相等：role=user / q 前缀 / status。
    for params in ({"role": "user"}, {"q": "member"}, {"role": "admin"}, {"status": "disabled"}):
        again = parse_csv(root_client.get(f"{ADMIN}/users/export.csv", params=params))
        expected = root_client.get(f"{ADMIN}/users", params={**params, "limit": 200}).json()
        assert [row[0] for row in again[1:]] == [item["username"] for item in expected["items"]], params


def test_the_export_carries_quota_columns_and_masks_the_invite(root_client, make_user, maker) -> None:  # noqa: ANN001
    alice = make_user("quota_alice", recovery_hash="a" * 64)
    with maker() as db:
        db.add(
            QuotaLedger(
                user_id=alice.id,
                resource="report",
                delta=-1,
                reason="run",
                source="daily",
                day_key=clock.local_day_key(clock.now(), get_settings().quota_tz),
                created_at=iso(clock.now()),
            )
        )
        db.add(
            QuotaLedger(
                user_id=alice.id,
                resource="report",
                delta=4,
                reason="admin_grant",
                source="bonus",
                day_key="2026-01-01",
                created_at=iso(clock.now()),
            )
        )
        db.commit()
    rows = parse_csv(root_client.get(f"{ADMIN}/users/export.csv", params={"q": "quota_alice"}))
    header, row = rows[0], rows[1]
    cell = dict(zip(header, row, strict=True))
    assert (cell["usedToday"], cell["bonus"], cell["hasRecoveryKey"]) == ("1", "4", "true")
    assert cell["role"] == "user" and cell["status"] == "active"


def test_audit_export_matches_the_list_and_excludes_its_own_row(root_client, admin_client, maker) -> None:  # noqa: ANN001
    admin_client.get(f"{ADMIN}/overview")
    rows = parse_csv(root_client.get(f"{ADMIN}/audit/export.csv"))
    assert tuple(rows[0]) == export_service.AUDIT_COLUMNS
    listed = root_client.get(f"{ADMIN}/audit", params={"limit": 200}).json()["items"]
    # The export's own `admin.export` audit row is outside its snapshot; the list (read afterwards) has it.
    assert audit_rows(maker, "admin.export")
    exported_ids = [int(row[0]) for row in rows[1:]]
    listed_ids = [item["id"] for item in listed]
    assert exported_ids == [i for i in listed_ids if i in set(exported_ids)]
    assert len(exported_ids) == len(listed_ids) - 1
    filtered = parse_csv(root_client.get(f"{ADMIN}/audit/export.csv", params={"action": "auth.login.success"}))
    assert {row[4] for row in filtered[1:]} == {"auth.login.success"}
    assert root_client.get(f"{ADMIN}/audit/export.csv", params={"action": "nope"}).status_code == 422


# ── 反射 / 矩阵 / 限速 / 审计（AC-231）────────────────────────────────────────


def test_export_headers_contain_nothing_credential_like() -> None:
    forbidden = re.compile(r"password|hash|token|secret|recovery|captcha", re.I)
    # `hasRecoveryKey` is the one allowed derived boolean; every other header must be clean.
    for column in (*export_service.USER_COLUMNS, *export_service.AUDIT_COLUMNS):
        if column == "hasRecoveryKey":
            continue
        assert not forbidden.search(column), column
    assert export_service.USER_COLUMNS == (
        "username",
        "displayName",
        "role",
        "status",
        "createdAt",
        "lastLoginAt",
        "dailyLimit",
        "usedToday",
        "bonus",
        "hasRecoveryKey",
        "invitedBy",
    )


def test_no_secret_value_reaches_the_users_csv(root_client, make_user) -> None:  # noqa: ANN001
    make_user("secretive", recovery_hash="f" * 64)
    text = root_client.get(f"{ADMIN}/users/export.csv").text
    assert "f" * 64 not in text and "scrypt$" not in text


@pytest.mark.parametrize("path", ["/users/export.csv", "/audit/export.csv"])
def test_export_matrix(path, root_client, admin_client, user_client, auth_client) -> None:  # noqa: ANN001
    assert root_client.get(ADMIN + path).status_code == 200
    assert admin_client.get(ADMIN + path).status_code == 200
    assert user_client.get(ADMIN + path).status_code == 403
    guest = TestClient(auth_client.app)
    assert guest.get(ADMIN + path).status_code == 401


def test_the_service_principal_may_export_users_it_can_already_read(auth_client, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "api_token", "service-token", raising=False)
    headers = {"Authorization": "Bearer service-token"}
    service = TestClient(auth_client.app)
    assert service.get(f"{ADMIN}/users", headers=headers).status_code == 200
    assert service.get(f"{ADMIN}/users/export.csv", headers=headers).status_code == 200


def test_export_is_audited_and_rate_limited_per_user(root_client, admin_client, maker) -> None:  # noqa: ANN001
    """AC-231：每次导出写 `admin.export`；同一用户第 7 次/分钟 → 429；另一个用户不受牵连。"""
    for _ in range(6):
        assert root_client.get(f"{ADMIN}/users/export.csv", params={"role": "user"}).status_code == 200
    refused = root_client.get(f"{ADMIN}/users/export.csv")
    assert refused.status_code == 429
    assert code_of(refused) == "rate_limited"
    rows = audit_rows(maker, "admin.export")
    assert len(rows) == 6  # 被拒的那次什么都不写
    detail = json.loads(rows[0].detail_json)
    assert (
        detail["kind"] == "users" and detail["filters"] == {"role": "user"} and detail["rows"] == 0
    )  # 没有 role=user 的人
    assert rows[0].actor_role == "root"
    assert admin_client.get(f"{ADMIN}/users/export.csv").status_code == 200


# ── 取数机制（AC-247）─────────────────────────────────────────────────────────


def test_audit_and_ratelimit_are_committed_before_the_first_byte(root_client, maker, engine) -> None:  # noqa: ANN001
    """AC-247：响应对象刚被构造、生成器一次都还没跑时，审计行与限速记录就已经落库。"""
    from app.security import ratelimit

    with maker() as db:
        root = user_service.get_root(db)
        principal = user_principal(
            user_id=root.id, username=root.username, role="root", session_id=None, must_change_password=False
        )
        response = export_service.users_csv(db, principal, q=None, role=None, status=None, locked=False)
    assert audit_rows(maker, "admin.export"), "audit row must exist before streaming starts"
    decision = ratelimit.get_limiter().peek("admin.export", root.id)
    assert decision.allowed  # 1 of 6 used…
    for _ in range(5):
        ratelimit.check("admin.export", root.id)
    assert not ratelimit.get_limiter().peek("admin.export", root.id).allowed  # …so the first one was recorded
    # 现在才消费流：它自己开会话，不依赖上面那个请求会话是否还活着。
    import asyncio

    async def drain() -> bytes:
        return b"".join([chunk.encode() if isinstance(chunk, str) else chunk async for chunk in response.body_iterator])

    body = asyncio.run(drain())
    assert body.decode("utf-8").startswith(BOM)


def test_chunks_are_at_most_500_rows_with_one_quota_query_each(root_client, maker, engine, monkeypatch) -> None:  # noqa: ANN001
    """AC-247：1 200 行 ⇒ `quota_lite` 恰 3 次（500 + 500 + 200）；语句总数不随行数线性增长。"""
    assert export_service.CHUNK <= 500
    bulk_insert_users(maker, 1200)
    calls: list[int] = []
    original = admin_user_service.quota_lite

    def counting(db, users, now):  # noqa: ANN001, ANN202
        calls.append(len(users))
        return original(db, users, now)

    monkeypatch.setattr(admin_user_service, "quota_lite", counting)
    statements: list[str] = []

    def on_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202, ARG001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        response = root_client.get(f"{ADMIN}/users/export.csv", params={"role": "user"})
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    rows = parse_csv(response)
    assert len(rows) - 1 == 1200
    assert calls == [500, 500, 200]
    # 每块固定条数的语句（页 + 额度 GROUP BY + 默认额度设置）+ 常量：远低于"每行一条"。
    assert len(statements) < 40, len(statements)


def test_truncation_header_and_body_agree(root_client, maker, monkeypatch) -> None:  # noqa: ANN001
    """AC-247：`COUNT > 上限` 预置 `X-Export-Truncated: 1` 且输出恰好上限行；未超限则无该头且行数 == COUNT。"""
    bulk_insert_users(maker, 8)
    monkeypatch.setattr(export_service, "EXPORT_MAX_ROWS", 5)
    capped = root_client.get(f"{ADMIN}/users/export.csv", params={"role": "user"})
    assert capped.headers["X-Export-Truncated"] == "1"
    assert len(parse_csv(capped)) - 1 == 5
    # 正好等于上限不算截断。
    monkeypatch.setattr(export_service, "EXPORT_MAX_ROWS", 8)
    exact = root_client.get(f"{ADMIN}/users/export.csv", params={"role": "user"})
    assert "X-Export-Truncated" not in exact.headers and len(parse_csv(exact)) - 1 == 8
    monkeypatch.setattr(export_service, "EXPORT_MAX_ROWS", 50_000)
    roomy = root_client.get(f"{ADMIN}/users/export.csv", params={"role": "user"})
    assert "X-Export-Truncated" not in roomy.headers and len(parse_csv(roomy)) - 1 == 8


def test_the_generator_does_not_use_the_request_scoped_session(root_client, maker, monkeypatch) -> None:  # noqa: ANN001
    """AC-247：让 `get_db` 在 yield 后立即关闭的场景下导出仍完整（生成器自己开会话）。"""
    bulk_insert_users(maker, 30)
    from app.db import session as session_module

    opened: list[object] = []
    original = session_module.SessionLocal

    def tracking():  # noqa: ANN202
        session = original()
        opened.append(session)
        return session

    monkeypatch.setattr(session_module, "SessionLocal", tracking)
    rows = parse_csv(root_client.get(f"{ADMIN}/users/export.csv", params={"role": "user"}))
    assert len(rows) - 1 == 30
    assert opened, "the generator opened its own sessions"


def test_a_filter_that_matches_nobody_exports_only_the_header(root_client) -> None:
    rows = parse_csv(root_client.get(f"{ADMIN}/users/export.csv", params={"q": "%%%"}))
    assert [tuple(rows[0])] == [export_service.USER_COLUMNS] and len(rows) == 1


# ── 批量赠送额度（AC-232 / AC-245）────────────────────────────────────────────


def bonus_balance(maker, user_id: str) -> int:  # noqa: ANN001
    with maker() as db:
        return int(
            db.scalar(
                select(func.coalesce(func.sum(QuotaLedger.delta), 0)).where(
                    QuotaLedger.user_id == user_id, QuotaLedger.source == "bonus"
                )
            )
            or 0
        )


def ledger_count(maker) -> int:  # noqa: ANN001
    with maker() as db:
        return int(db.scalar(select(func.count()).select_from(QuotaLedger)) or 0)


def test_bulk_grant_100_users_at_the_boundary(root_client, maker) -> None:  # noqa: ANN001
    """AC-232：100 个目标、grant=50 一次成功；每人恰一条账本行 + 一条 `quota.grant`（含同一 batch）。"""
    ids = bulk_insert_users(maker, 100)
    response = root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids, "grant": 50, "note": "活动"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["applied"], body["skipped"]) == (100, 0)
    assert re.fullmatch(r"[0-9a-f]{12}", body["batchId"])
    with maker() as db:
        per_user = dict(
            db.execute(
                select(QuotaLedger.user_id, func.count())
                .where(QuotaLedger.user_id.in_(ids))
                .group_by(QuotaLedger.user_id)
            ).all()
        )
    assert set(per_user) == set(ids) and set(per_user.values()) == {1}
    rows = audit_rows(maker, "quota.grant")
    assert len(rows) == 100
    details = [json.loads(row.detail_json) for row in rows]
    assert {d["batch"] for d in details} == {body["batchId"]}
    assert all(d["bulk"] is True and d["amount"] == 50 for d in details)


@pytest.mark.parametrize("grant", [0, 51, -51, 100])
def test_bulk_grant_out_of_range_is_422_not_500(grant, root_client, maker) -> None:  # noqa: ANN001
    ids = bulk_insert_users(maker, 2)
    response = root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids, "grant": grant})
    assert response.status_code == 422
    assert code_of(response) == "validation_error"
    assert ledger_count(maker) == 0


def test_bulk_grant_shape_limits(root_client, maker) -> None:  # noqa: ANN001
    ids = bulk_insert_users(maker, 101)
    assert root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids, "grant": 1}).status_code == 422
    assert root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": [], "grant": 1}).status_code == 422
    assert ledger_count(maker) == 0


def test_bulk_grant_authority_is_all_or_nothing(root_client, admin_client, user_client, maker, make_user) -> None:  # noqa: ANN001
    """AC-232：调用者是 admin 时目标含 admin/root → 整体 403 且无账本行；调用者是 user → 403。"""
    ids = bulk_insert_users(maker, 3)
    other_admin = make_user("peer_admin", role="admin")
    with maker() as db:
        root_id = user_service.get_root(db).id
    for forbidden_target in (other_admin.id, root_id):
        response = admin_client.post(
            f"{ADMIN}/users/bulk-quota", json={"userIds": [*ids, forbidden_target], "grant": 5}
        )
        assert response.status_code == 403, response.text
        assert ledger_count(maker) == 0
    assert user_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids, "grant": 5}).status_code == 403
    # root 可以给 admin 赠送。
    ok = root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": [other_admin.id], "grant": 5})
    assert ok.status_code == 200 and ok.json()["applied"] == 1


def test_bulk_grant_dedupes_and_counts_the_deduped_users(root_client, maker) -> None:  # noqa: ANN001
    """AC-245：重复 id 去重后只处理一次（`applied` 按去重后计）。"""
    ids = bulk_insert_users(maker, 3)
    response = root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": [*ids, *ids, ids[0]], "grant": 2})
    assert response.json()["applied"] == 3
    assert ledger_count(maker) == 3
    assert all(bonus_balance(maker, user_id) == 2 for user_id in ids)


def test_bulk_grant_unknown_or_deleted_targets_are_404_and_write_nothing(root_client, maker, make_user) -> None:  # noqa: ANN001
    """AC-245：含未知 / 已注销 id → 整体 404；同时含未知 id 与越权目标 → 404 先于 403。"""
    ids = bulk_insert_users(maker, 2)
    unknown = uuid.uuid4().hex
    assert (
        root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": [*ids, unknown], "grant": 3}).status_code == 404
    )
    tomb = make_user("ghost", status="deleted")
    response = root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": [*ids, tomb.id], "grant": 3})
    assert response.status_code == 404 and code_of(response) == "not_found"
    assert ledger_count(maker) == 0


def test_bulk_grant_404_outranks_403(admin_client, maker, make_user) -> None:  # noqa: ANN001
    peer = make_user("peer_admin2", role="admin")
    response = admin_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": [peer.id, uuid.uuid4().hex], "grant": 3})
    assert response.status_code == 404
    assert ledger_count(maker) == 0


def test_bulk_take_back_is_clamped_and_counted_as_skipped(root_client, maker) -> None:  # noqa: ANN001
    """AC-245：grant=-50 而部分用户 bonus 不足 → 夹紧（含夹成 0）；夹成 0 的 `skipped`，不写账本也不审计。"""
    ids = bulk_insert_users(maker, 4)
    # 前两位有 10 点赠送，后两位没有。
    assert root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids[:2], "grant": 10}).status_code == 200
    audit_before = len(audit_rows(maker, "quota.grant"))
    ledger_before = ledger_count(maker)
    response = root_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids, "grant": -50})
    body = response.json()
    assert (body["applied"], body["skipped"]) == (2, 2)
    assert body["applied"] + body["skipped"] == 4
    assert ledger_count(maker) == ledger_before + 2
    assert len(audit_rows(maker, "quota.grant")) == audit_before + 2
    assert [bonus_balance(maker, user_id) for user_id in ids] == [0, 0, 0, 0]


def test_a_failure_midway_rolls_the_whole_batch_back(root_client, maker, monkeypatch) -> None:  # noqa: ANN001
    """AC-245：任何一步异常 → 整批回滚，账本行数不变。"""
    ids = bulk_insert_users(maker, 5)
    from app.services import quota_service

    original = quota_service.grant
    calls = {"n": 0}

    def flaky(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(quota_service, "grant", flaky)
    safe_client = TestClient(root_client.app, raise_server_exceptions=False)
    safe_client.cookies.update(root_client.cookies)
    response = safe_client.post(f"{ADMIN}/users/bulk-quota", json={"userIds": ids, "grant": 5})
    assert response.status_code == 500
    assert ledger_count(maker) == 0
    assert audit_rows(maker, "quota.grant") == []


# ── security24h（AC-233 / AC-246）─────────────────────────────────────────────


def put_failure(maker, action: str, *, count: int | None, name: str, ip: str, at, raw: str | None = None) -> None:  # noqa: ANN001
    with maker() as db:
        db.add(
            AuditLog(
                at=iso(at),
                actor_role="guest",
                action=action,
                target_type="user",
                target_name=name,
                ip=ip,
                detail_json=raw if raw is not None else json.dumps({"count": count}),
            )
        )
        db.commit()


def security(root_client) -> dict:  # noqa: ANN001
    response = root_client.get(f"{ADMIN}/overview")
    assert response.status_code == 200, response.text
    return response.json()["security24h"]


def test_security24h_sums_attempts_not_rows(root_client, maker) -> None:  # noqa: ANN001
    """AC-246：三组（count = 50、1、7）→ `loginFailures == 58`、`loginFailureBursts == 3`。"""
    now = clock.now()
    put_failure(maker, "auth.login.failure", count=50, name="a", ip="1.1.1.0", at=now - timedelta(hours=1))
    put_failure(maker, "auth.login.failure", count=1, name="b", ip="1.1.2.0", at=now - timedelta(hours=2))
    put_failure(maker, "auth.login.failure", count=7, name="c", ip="1.1.3.0", at=now - timedelta(hours=3))
    # 24 h 之外不计。
    put_failure(maker, "auth.login.failure", count=999, name="old", ip="1.1.4.0", at=now - timedelta(hours=25))
    put_failure(maker, "auth.recovery.failure", count=4, name="r", ip="1.1.5.0", at=now - timedelta(hours=1))
    put_failure(maker, "auth.recovery.failure", count=100, name="old", ip="1.1.5.0", at=now - timedelta(hours=30))
    block = security(root_client)
    assert block["loginFailures"] == 58
    assert block["loginFailureBursts"] == 3
    assert block["loginFailuresCapped"] is False
    assert block["recoveryFailures"] == 4


def test_a_damaged_detail_counts_as_one_and_never_raises(root_client, maker) -> None:  # noqa: ANN001
    now = clock.now()
    for raw in ("{not json", "{}", '{"count": "many"}', '{"count": true}', '{"count": 0}', "[1,2]", None):
        put_failure(maker, "auth.login.failure", count=None, raw=raw, name="x", ip="9.9.9.0", at=now)
    block = security(root_client)
    assert block["loginFailures"] == 7 and block["loginFailureBursts"] == 7


def test_security24h_is_capped_at_5001_rows(root_client, maker, engine) -> None:  # noqa: ANN001
    """AC-246：造 5 001 行 → `loginFailuresCapped == true`，且查询至多取 5001 行（只计前 5000）。"""
    now = clock.now()
    rows = [
        {
            "at": iso(now - timedelta(minutes=1)),
            "actor_role": "guest",
            "action": "auth.login.failure",
            "target_type": "user",
            "target_name": f"n{i}",
            "ip": "8.8.8.0",
            "detail_json": '{"count":2}',
        }
        for i in range(5001)
    ]
    with maker() as db:
        db.execute(AuditLog.__table__.insert(), rows)
        db.commit()
    limits: list[str] = []

    def on_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202, ARG001
        if "audit_log" in statement and "detail_json" in statement and "LIMIT" in statement.upper():
            limits.append(statement)

    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        block = security(root_client)
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    assert block["loginFailuresCapped"] is True
    assert block["loginFailureBursts"] == 5000 and block["loginFailures"] == 10000
    assert any("LIMIT" in s.upper() for s in limits)


def test_security24h_counts_the_one_row_per_event_actions(root_client, maker, make_user, login_as) -> None:  # noqa: ANN001
    """AC-233：1 次锁定 / 1 次恢复 / 1 次自助注销 / 1 次导出 → 逐项为 1（导出由真实调用产生）。"""
    now = clock.now()
    for action in ("auth.login.lockout", "auth.password.recover", "user.self_delete"):
        put_failure(maker, action, count=None, raw=None, name="z", ip="7.7.7.0", at=now - timedelta(hours=2))
    put_failure(
        maker, "auth.login.lockout", count=None, raw=None, name="old", ip="7.7.7.0", at=now - timedelta(hours=30)
    )
    assert root_client.get(f"{ADMIN}/users/export.csv").status_code == 200
    block = security(root_client)
    assert (block["lockouts"], block["recoveries"], block["selfDeletes"], block["exports"]) == (1, 1, 1, 1)


def test_security24h_end_to_end_with_real_failed_logins(root_client, make_user, auth_client) -> None:  # noqa: ANN001
    """AC-233：真实制造 N 次登录失败（同 `(用户名, ip)` 60 s 内合并成 1 行）→ `loginFailures == N`、bursts == 1。"""
    make_user("victim")
    prober = TestClient(auth_client.app)
    attempts = 2  # 低于锁定阈值（3），且不触发验证码
    for _ in range(attempts):
        assert prober.post(f"{AUTH}/login", json={"username": "victim", "password": "wrong-Pass-1"}).status_code == 401
    block = security(root_client)
    assert block["loginFailures"] == attempts
    assert block["loginFailureBursts"] == 1


def test_overview_with_security24h_is_still_fast_on_20k_users(root_client, maker) -> None:  # noqa: ANN001
    """AC-233：2 万用户下概览 5 次取最优仍 < 40 ms（AC-194 不退化）。"""
    bulk_insert_users(maker, 20_000, prefix="perf")
    best = min(_timed(maker) for _ in range(5))
    assert best < 40, f"{best:.1f} ms"


def _timed(maker) -> float:  # noqa: ANN001
    import time

    with maker() as db:
        started = time.perf_counter()
        admin_overview.overview(db)
        return (time.perf_counter() - started) * 1000


def test_actions_vocabulary_contains_what_this_module_writes() -> None:
    assert "admin.export" in audit_service.ACTIONS and "auth.recovery.failure" in audit_service.ACTIONS
