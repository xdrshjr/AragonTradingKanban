"""审计日志与日志脱敏（spec §24.3.10、§24.3.5；AC-163、AC-198 中属于 M45–M47 的部分）。

动作词表里**每一项都有写入点**的反查（"用词表反查 grep"）按里程碑分批点亮：这里只断言
M45–M47 已经实现的那一批真的落库，其余词表项登记在 `PENDING_ACTIONS` 里（M48/M50 落地时从中删除，
且删除后若找不到写入点测试就红——所以它只能缩小）。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.logging import RedactFilter, redact
from app.models import AuditLog
from app.security.principal import GUEST, SYSTEM, user_principal
from app.services import audit_service
from app.services.audit_service import AuditTarget

APP_DIR = Path(__file__).resolve().parents[1] / "app"
URL = "/api/v1/auth"

#: 词表里**尚无写入点**的动作。M50 之后为空：每个动作都有写入点，并由文件末尾那条端到端用例经真实流程各触发一次。
#: 机制保留：将来给词表加新动作而写入点滞后时，先登记在这里，写入点落地后**必须**删掉。
PENDING_ACTIONS: frozenset[str] = frozenset()


def alice() -> object:
    return user_principal(
        user_id="u" * 32,
        username="alice",
        role="user",
        session_id="s1",
        must_change_password=False,
        ip="203.0.113.0/24",
    )


# ── record() ────────────────────────────────────────────────────────────────


def test_record_snapshots_actor_and_target(session) -> None:  # noqa: ANN001
    row = audit_service.record(
        session, alice(), "auth.login.success", AuditTarget("user", "u" * 32, "alice"), {"remember": True}
    )
    session.commit()
    stored = session.get(AuditLog, row.id)
    assert (stored.actor_id, stored.actor_name, stored.actor_role) == ("u" * 32, "alice", "user")
    assert (stored.target_type, stored.target_id, stored.target_name) == ("user", "u" * 32, "alice")
    assert stored.ip == "203.0.113.0/24"
    assert json.loads(stored.detail_json) == {"remember": True}
    assert stored.at.endswith("Z")


def test_record_flushes_but_does_not_commit(session, engine) -> None:  # noqa: ANN001
    """审计行与它描述的动作**同一个事务**：调用方回滚，审计也回滚。"""
    audit_service.record(session, alice(), "auth.logout")
    session.rollback()
    assert session.execute(select(AuditLog)).first() is None


def test_system_and_service_actors_are_named(session) -> None:  # noqa: ANN001
    from app.security.principal import service_principal

    audit_service.record(session, SYSTEM, "root.bootstrap")
    audit_service.record(session, service_principal(), "schedule.create")
    session.commit()
    names = [
        (row.actor_name, row.actor_role) for row in session.execute(select(AuditLog).order_by(AuditLog.id)).scalars()
    ]
    assert names == [("system", "system"), ("service", "service")]


def test_secrets_never_reach_detail_json(session) -> None:  # noqa: ANN001
    """AC-163 / AC-198：detail 里凡是长得像密码/令牌/密钥/Cookie/验证码答案的键一律丢弃，递归。"""
    row = audit_service.record(
        session,
        alice(),
        "settings.update",
        detail={
            "password": "hunter2",
            "newPassword": "hunter3",
            "token": "abc",
            "sessionToken": "t",
            "api_key": "k",
            "captchaAnswer": "ABCDE",
            "Authorization": "Bearer x",
            "cookie": "aragon_sid=1",
            "nested": {"secret": "s", "ok": "kept", "list": [{"passwd": "p", "fine": 1}]},
            "field": "registrationMode",
            "value": "invite",
        },
    )
    text = row.detail_json
    for leaked in ("hunter", "abc", "ABCDE", "aragon_sid", "Bearer"):
        assert leaked not in text
    assert json.loads(text) == {
        "nested": {"ok": "kept", "list": [{"fine": 1}]},
        "field": "registrationMode",
        "value": "invite",
    }


def test_long_values_are_clipped_and_control_characters_stripped(session) -> None:  # noqa: ANN001
    row = audit_service.record(
        session,
        alice(),
        "auth.login.failure",
        AuditTarget("user", None, "bad\nname\x00" + "x" * 80),
        {"note": "y" * 500},
    )
    assert len(json.loads(row.detail_json)["note"]) == 200
    assert "\n" not in row.target_name and "\x00" not in row.target_name and len(row.target_name) <= 40


def test_request_id_comes_from_the_context_and_is_clipped_to_sixteen(session) -> None:  # noqa: ANN001
    from app.logging import request_id_var

    token = request_id_var.set("0123456789abcdef-and-more")
    try:
        row = audit_service.record(session, alice(), "auth.logout")
    finally:
        request_id_var.reset(token)
    assert row.request_id == "0123456789abcdef"
    other = audit_service.record(session, alice(), "auth.logout")
    assert other.request_id is None, "没有请求上下文（占位 `-`）就不写"


# ── 登录失败合并 ────────────────────────────────────────────────────────────


def test_failures_for_one_username_and_ip_merge_within_sixty_seconds(session, frozen_now) -> None:  # noqa: ANN001
    guest = GUEST.with_ip("203.0.113.0/24", "203.0.113.5")
    for _ in range(5):
        audit_service.record_login_failure(session, guest, "alice")
        frozen_now.advance(seconds=10)
    session.commit()
    rows = session.execute(select(AuditLog).where(AuditLog.action == "auth.login.failure")).scalars().all()
    assert len(rows) == 1 and json.loads(rows[0].detail_json) == {"count": 5}


def test_a_different_username_or_ip_or_a_later_minute_starts_a_new_row(session, frozen_now) -> None:  # noqa: ANN001
    here = GUEST.with_ip("203.0.113.0/24", "203.0.113.5")
    there = GUEST.with_ip("198.51.100.0/24", "198.51.100.5")
    audit_service.record_login_failure(session, here, "alice")
    audit_service.record_login_failure(session, here, "bob")
    audit_service.record_login_failure(session, there, "alice")
    frozen_now.advance(seconds=61)
    audit_service.record_login_failure(session, here, "alice")
    session.commit()
    assert len(session.execute(select(AuditLog)).scalars().all()) == 4


def test_a_flood_of_distinct_failures_cannot_hide_inside_one_row(session, frozen_now) -> None:  # noqa: ANN001
    """限频合并按 (用户名, IP) 键，不是全局：枚举不同用户名不会被合并成一行。"""
    guest = GUEST.with_ip("203.0.113.0/24", "203.0.113.5")
    for index in range(20):
        audit_service.record_login_failure(session, guest, f"name_{index}")
    session.commit()
    assert len(session.execute(select(AuditLog)).scalars().all()) == 20


# ── 查询与保留 ──────────────────────────────────────────────────────────────


def test_query_pages_newest_first_without_gaps_or_duplicates(session, frozen_now) -> None:  # noqa: ANN001
    for index in range(7):
        audit_service.record(session, alice(), "auth.login.success", detail={"n": index})
        if index % 2:
            frozen_now.advance(seconds=1)  # 一半行共享同一秒：游标必须靠 id 打破并列
    session.commit()
    seen: list[int] = []
    cursor = None
    while True:
        rows, cursor = audit_service.query(session, limit=3, before=cursor)
        seen.extend(json.loads(row.detail_json)["n"] for row in rows)
        if cursor is None:
            break
    assert seen == [6, 5, 4, 3, 2, 1, 0]


def test_query_filters(session) -> None:  # noqa: ANN001
    audit_service.record(session, alice(), "auth.login.success")
    audit_service.record(session, alice(), "auth.logout")
    audit_service.record(session, SYSTEM, "root.bootstrap", AuditTarget("user", "r" * 32, "jdqqjr"))
    session.commit()
    assert [row.action for row in audit_service.query(session, action="auth.logout")[0]] == ["auth.logout"]
    assert len(audit_service.query(session, actor="alice")[0]) == 2
    assert [row.action for row in audit_service.query(session, target="jdqqjr")[0]] == ["root.bootstrap"]


def test_a_malformed_cursor_is_a_422(session) -> None:  # noqa: ANN001
    from app.errors import InvalidCursor

    with pytest.raises(InvalidCursor):
        audit_service.query(session, before="not-a-cursor")


def test_purge_expired_respects_the_retention_window(session, frozen_now, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "audit_retention_days", 30, raising=False)
    audit_service.record(session, alice(), "auth.logout")  # 将变成 31 天前
    frozen_now.advance(days=31)
    audit_service.record(session, alice(), "auth.login.success")  # 今天
    session.commit()
    assert audit_service.purge_expired(session) == 1
    session.commit()
    assert [row.action for row in session.execute(select(AuditLog)).scalars()] == ["auth.login.success"]


def test_retention_zero_disables_the_purge(session, frozen_now, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "audit_retention_days", 0, raising=False)
    audit_service.record(session, alice(), "auth.logout")
    frozen_now.advance(days=4000)
    assert audit_service.purge_expired(session) == 0


# ── 词表与写入点 ────────────────────────────────────────────────────────────


def test_the_action_vocabulary_is_the_specified_one() -> None:
    assert len(audit_service.ACTIONS) == len(set(audit_service.ACTIONS)) == 36  # §25：31 + 5
    assert "auth.login.success" in audit_service.ACTIONS and "root.password.cli_reset" in audit_service.ACTIONS
    assert set(audit_service.ACTIONS) >= PENDING_ACTIONS
    # 每个 action 都 ≤ 32 字符（`audit_log.action VARCHAR(32)`）。
    assert max(len(action) for action in audit_service.ACTIONS) <= 32


def test_every_implemented_action_has_a_write_site() -> None:
    """用词表反查 grep：除 `PENDING_ACTIONS` 外，每个动作字符串都出现在某个 `record*()` 调用附近。"""

    def source_of(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if path.name == "audit_service.py":  # 词表本身不算写入点：把 `ACTIONS = (...)` 那一段切掉
            head, _, rest = text.partition("ACTIONS: tuple[str, ...] = (")
            text = head + rest.partition("FAILURE_MERGE_WINDOW")[2]
        return text

    sources = "\n".join(source_of(path) for path in APP_DIR.rglob("*.py") if "__pycache__" not in path.parts)
    missing = [
        action
        for action in audit_service.ACTIONS
        if action not in PENDING_ACTIONS and not re.search(rf"[\"']{re.escape(action)}[\"']", sources)
    ]
    assert missing == [], f"这些动作词表项没有写入点（也不在 PENDING_ACTIONS）：{missing}"
    # 反向：PENDING 里的项若已经有了写入点，必须从 PENDING 删掉（只许缩小）。
    already = [action for action in PENDING_ACTIONS if re.search(rf"[\"']{re.escape(action)}[\"']", sources)]
    assert already == [], f"这些动作已经有写入点了，请从 PENDING_ACTIONS 删除：{already}"


def test_no_route_can_update_or_delete_an_audit_row() -> None:
    """AC-198：审计表无任何更新/删除路由（路由表扫描）。"""
    from app.main import app

    offenders = [
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or ())
        if "audit" in route.path and method in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert offenders == []


def test_implemented_flows_write_their_audit_rows(auth_client, make_user, login_as, engine) -> None:
    user = make_user("alice")
    login_as(auth_client, user)
    auth_client.post(f"{URL}/password", json={"currentPassword": user.password, "newPassword": "Another-Strong-7q"})
    other = auth_client.__class__(auth_client.app)
    other.post(f"{URL}/login", json={"username": "alice", "password": "Another-Strong-7q"})
    sessions = auth_client.get(f"{URL}/sessions").json()["items"]
    auth_client.post(f"{URL}/sessions/revoke-others")
    auth_client.post(f"{URL}/logout")
    assert sessions
    with sessionmaker(bind=engine)() as db:
        actions = [row.action for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()]
    for expected in (
        "root.bootstrap",
        "auth.login.success",
        "auth.password.change",
        "auth.session.revoke",
        "auth.logout",
    ):
        assert expected in actions, (expected, actions)


def test_no_audit_row_contains_any_plaintext_password(auth_client, make_user, login_as, engine) -> None:
    user = make_user("alice")
    login_as(auth_client, user)
    auth_client.post(f"{URL}/password", json={"currentPassword": user.password, "newPassword": "Another-Strong-7q"})
    auth_client.post(f"{URL}/login", json={"username": "alice", "password": "wrong-Wrong-1"})
    with sessionmaker(bind=engine)() as db:
        blob = json.dumps(
            [
                [row.actor_name, row.target_name, row.detail_json, row.ip, row.request_id]
                for row in db.execute(select(AuditLog)).scalars()
            ],
            ensure_ascii=False,
        )
    for secret in (user.password, "Another-Strong-7q", "wrong-Wrong-1"):
        assert secret not in blob


# ── 日志脱敏（AC-163）──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("login password=Hunter2-secret ok", "Hunter2-secret"),
        ('body {"password": "Hunter2-secret", "user": "a"}', "Hunter2-secret"),
        ("Authorization: Bearer abc.DEF-123_xyz", "abc.DEF-123_xyz"),
        ("GET /api/v1/runs/x/events?token=1700000000.deadbeef HTTP/1.1", "deadbeef"),
        ("Set-Cookie: aragon_sid=SESSIONSECRET123; HttpOnly", "SESSIONSECRET123"),
        ("captchaAnswer=ABCDE accepted", "ABCDE"),
        ("api_key=sk-12345 used", "sk-12345"),
        ("newPassword: 'Quoted Secret 1'", "Quoted Secret 1"),
    ],
)
def test_redact_masks_the_value_and_keeps_the_key(raw: str, must_not_contain: str) -> None:
    masked = redact(raw)
    assert must_not_contain not in masked
    assert "***" in masked


def test_redact_leaves_ordinary_text_alone() -> None:
    for text in ("tokens counted=3 passwords=none", "run succeeded in 12s", "user alice logged in"):
        assert redact(text) == text


def test_the_filter_rewrites_records_without_dropping_them() -> None:
    logger = logging.getLogger("aragon.test.redact")
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = Capture()
    handler.addFilter(RedactFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("password=%s user=%s", "Hunter2-secret", "alice")
        logger.info("fine", extra={"kv": {"token": "t-secret", "n": 3}})
    finally:
        logger.removeHandler(handler)
    assert len(captured) == 2
    assert "Hunter2-secret" not in captured[0].getMessage() and "alice" in captured[0].getMessage()
    assert captured[1].kv == {"token": "***", "n": 3}


def test_the_configured_application_handler_carries_the_filter() -> None:
    from app.logging import configure_logging

    configure_logging("WARNING")
    handlers = logging.getLogger().handlers
    assert any(any(isinstance(f, RedactFilter) for f in handler.filters) for handler in handlers)


def test_a_full_login_and_registration_never_logs_a_secret(
    auth_client, make_user, solve_captcha, frozen_now, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-163：一次完整登录/注册请求后，应用日志里不出现明文密码、会话令牌、验证码答案。"""
    user = make_user("alice")
    with caplog.at_level(logging.DEBUG):
        auth_client.post(f"{URL}/login", json={"username": "alice", "password": "wrong-Wrong-1"})
        login = auth_client.post(f"{URL}/login", json={"username": "alice", "password": user.password})
        token = auth_client.cookies.get("aragon_sid")
        auth_client.post(f"{URL}/logout")
        challenge_id, answer = solve_captcha(auth_client, "register")
        auth_client.post(
            f"{URL}/register",
            json={
                "username": "carol",
                "password": "Registered-Pass-5",
                "captchaId": challenge_id,
                "captchaAnswer": answer,
            },
        )
    assert login.status_code == 200 and token
    text = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (user.password, "wrong-Wrong-1", "Registered-Pass-5", token, answer):
        assert secret not in text, f"日志里出现了 {secret!r}"


# ── AC-198：词表里每个动作都被一条真实流程触发并落库 ─────────────────────────


def test_every_action_in_the_vocabulary_is_written_by_a_real_flow(
    auth_client, make_user, login_as, solve_captcha, frozen_now, instrument, sample_report, engine
) -> None:  # noqa: ANN001
    """一个场景把 36 个动作各打一遍（经真实 HTTP / CLI / 引导，不是直接调 `record()`）。

    与"词表反查 grep"互补：grep 证明字符串**写在了**某处，这条证明那处**真的会被执行**。
    """
    from sqlalchemy import update

    from app import cli
    from app.models import Run, User
    from app.services import report_service
    from app.services.captcha import get_captcha_provider

    admin_url = "/api/v1/admin"

    def client() -> object:
        return auth_client.__class__(auth_client.app)

    with sessionmaker(bind=engine)() as db:  # root：清强制改密标志（默认口令是公开的）
        db.execute(update(User).where(User.role == "root").values(must_change_password=0))
        db.commit()
    root, admin = client(), client()
    assert root.post(f"{URL}/login", json={"username": "jdqqjr", "password": "13519126390"}).status_code == 200
    login_as(admin, make_user("flow_admin", role="admin"))
    victim, asker1, asker2 = make_user("flow_victim"), make_user("flow_asker1"), make_user("flow_asker2")

    # auth.register / auth.logout / auth.password.change / auth.session.revoke(用户自己)
    newbie = client()
    challenge_id, answer = solve_captcha(newbie, "register")
    registered = newbie.post(
        f"{URL}/register",
        json={
            "username": "flow_newbie",
            "password": "Aragon-Test-9x",
            "captchaId": challenge_id,
            "captchaAnswer": answer,
        },
    )
    assert registered.status_code == 201
    assert (
        newbie.post(
            f"{URL}/password", json={"currentPassword": "Aragon-Test-9x", "newPassword": "Aragon-Test-8y"}
        ).status_code
        == 204
    )
    assert newbie.post(f"{URL}/sessions/revoke-others").status_code == 200
    assert newbie.post(f"{URL}/logout").status_code == 204

    # auth.login.failure / auth.login.lockout：第 5 次失败（先要验证码）
    with sessionmaker(bind=engine)() as db:
        db.execute(update(User).where(User.username_lc == "flow_victim").values(failed_logins=4))
        db.commit()
    prober = client()
    needs = prober.post(f"{URL}/login", json={"username": "flow_victim", "password": "Wrong-Pass-1x"})
    assert needs.status_code == 422
    challenge = needs.json()["error"]["detail"]["challenge"]
    locked = prober.post(
        f"{URL}/login",
        json={
            "username": "flow_victim",
            "password": "Wrong-Pass-1x",
            "captchaId": challenge["challengeId"],
            "captchaAnswer": get_captcha_provider().debug_answer(challenge["challengeId"]),
        },
    )
    assert locked.status_code == 423

    # 后台：用户
    created = admin.post(f"{admin_url}/users", json={"username": "flow_created", "bonusCredits": 2})
    assert created.status_code == 201
    created_id = created.json()["user"]["id"]
    assert root.patch(f"{admin_url}/users/{created_id}", json={"role": "admin"}).status_code == 200  # user.role.change
    assert admin.patch(f"{admin_url}/users/{victim.id}", json={"status": "disabled"}).status_code == 200  # user.disable
    assert admin.patch(f"{admin_url}/users/{victim.id}", json={"status": "active"}).status_code == 200  # user.enable
    assert admin.post(f"{admin_url}/users/{victim.id}/reset-password").status_code == 200  # auth.password.reset
    assert admin.post(f"{admin_url}/users/{victim.id}/revoke-sessions").status_code == 200  # auth.session.revoke
    assert admin.post(f"{admin_url}/users/{victim.id}/unlock").status_code == 204  # user.unlock
    assert (
        admin.post(
            f"{admin_url}/users/{victim.id}/quota", json={"resource": "report", "grant": 3, "dailyLimit": 5}
        ).status_code
        == 200
    )
    assert (
        admin.delete(f"{admin_url}/users/{victim.id}", params={"confirm": "flow_victim"}).status_code == 204
    )  # user.delete

    # 后台：邀请码 / 设置 / 精选 / 定时刷新
    invite = admin.post(f"{admin_url}/invites", json={}).json()
    assert admin.delete(f"{admin_url}/invites/{invite['id']}").status_code == 204
    assert admin.patch(f"{admin_url}/settings", json={"signupBonus": 1}).status_code == 200
    assert admin.post(f"{admin_url}/featured-watchlist", json={"instrumentId": instrument.id}).status_code == 201
    assert admin.delete(f"{admin_url}/featured-watchlist/{instrument.id}").status_code == 204
    schedule = admin.post("/api/v1/schedules", json={"instrumentId": instrument.id, "cron": "0 9 * * *"})
    assert schedule.status_code in (200, 201), schedule.text
    schedule_id = schedule.json()["id"]
    assert admin.patch(f"/api/v1/schedules/{schedule_id}", json={"enabled": False}).status_code == 200
    assert admin.delete(f"/api/v1/schedules/{schedule_id}").status_code == 204

    # 额度申请：create（用户）→ approve / reject（管理员）
    for who, decision in (
        (asker1, {"decision": "approve", "grant": 2}),
        (asker2, {"decision": "reject", "note": "本周已有额度"}),
    ):
        person = client()
        login_as(person, who)
        made = person.post(
            "/api/v1/me/quota-requests", json={"resource": "report", "amount": 3, "reason": "想多做几份研究"}
        )
        assert made.status_code == 201, made.text
        assert admin.post(f"{admin_url}/quota-requests/{made.json()['id']}/resolve", json=decision).status_code == 200

    # 管理员动了别人的报告 / 取消了别人的 run
    with sessionmaker(bind=engine)() as db:
        report = report_service.create_version(db, instrument, sample_report, status="ready")
        db.add(
            Run(
                id="f" * 32,
                instrument_id=instrument.id,
                status="running",
                started_at="2026-01-01T00:00:00Z",
                created_by=asker1.id,
            )
        )
        db.commit()
        report_id = report.id
    assert admin.delete(f"/api/v1/reports/{report_id}").status_code in (200, 204)
    assert admin.post(f"/api/v1/runs/{'f' * 32}/cancel").status_code in (200, 202)

    # §25：恢复码 —— 失败（同一用户名 + 错码）/ 成功找回 / 登录后轮换 / 自助注销 / 管理员导出
    recoverer = client()
    first_challenge, first_answer = solve_captcha(recoverer, "recover")
    bad = recoverer.post(
        f"{URL}/recover",
        json={
            "username": "flow_newbie",
            "recoveryKey": "AAAAA-AAAAA-AAAAA-AAAAA",
            "newPassword": "Aragon-Test-7z",
            "captchaId": first_challenge,
            "captchaAnswer": first_answer,
        },
    )
    assert bad.status_code == 401  # auth.recovery.failure
    second_challenge, second_answer = solve_captcha(recoverer, "recover")
    recovered = recoverer.post(
        f"{URL}/recover",
        json={
            "username": "flow_newbie",
            "recoveryKey": registered.json()["recoveryKey"],
            "newPassword": "Aragon-Test-7z",
            "captchaId": second_challenge,
            "captchaAnswer": second_answer,
        },
    )
    assert recovered.status_code == 200, recovered.text  # auth.password.recover
    returning = client()
    assert (
        returning.post(
            f"{URL}/login", json={"username": "flow_newbie", "password": "Aragon-Test-7z"}
        ).status_code
        == 200
    )
    assert (
        returning.post(f"{URL}/recovery-key", json={"currentPassword": "Aragon-Test-7z"}).status_code == 200
    )  # auth.recovery.rotate
    assert (
        returning.request(
            "DELETE", f"{URL}/account", json={"password": "Aragon-Test-7z", "confirm": "flow_newbie"}
        ).status_code
        == 204
    )  # user.self_delete
    exported = admin.get(f"{admin_url}/users/export.csv")  # admin.export
    assert exported.status_code == 200, exported.text

    # 离线 CLI
    assert cli.main(["reset-root-password", "--password", "Recovered-Root-Pass-5k"]) == 0

    with sessionmaker(bind=engine)() as db:
        written = {row.action for row in db.execute(select(AuditLog)).scalars()}
    missing = sorted(set(audit_service.ACTIONS) - written)
    assert missing == [], f"这些动作没有被任何真实流程写入：{missing}"
    assert written <= set(audit_service.ACTIONS), "写入了词表之外的动作"
