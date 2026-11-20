"""登录、锁定、影子节流、限速（spec §24.3.7；AC-153、AC-169–171、AC-183）。

**AC-170 的写法是本文件的重点**（RV-3）：对"存在 / 不存在"两个用户名各连发 6 次错误密码，
**逐次**比较 `status`、`error.code`、`detail` 键集合，必须逐次相同——含第 3 次起的验证码要求
与第 5 次起的锁定。两个用户名用**不同的客户端 IP**，否则第 11 次请求先撞上 IP 限速，而那与
本条要证明的东西无关。
"""

from __future__ import annotations

import hashlib
import logging

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, User, UserSession
from app.security import ratelimit
from app.services import user_service

URL = "/api/v1/auth"
BAD = "Not-The-Password-1"


def login(client, username: str, password: str, **extra):  # noqa: ANN001, ANN003, ANN201
    return client.post(f"{URL}/login", json={"username": username, "password": password, **extra})


def code_of(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def shape(response) -> tuple:  # noqa: ANN001
    """What AC-170 compares: status, error code, and the *keys* of detail (values may be fresh)."""
    error = response.json()["error"]
    return response.status_code, error["code"], tuple(sorted(error["detail"]))


def attempt_with_captcha(client, username: str, password: str, previous):  # noqa: ANN001, ANN201
    """Answer the captcha a previous `captcha_required` response carried, then retry."""
    from app.services.captcha import get_captcha_provider

    challenge = previous.json()["error"]["detail"]["challenge"]
    answer = get_captcha_provider().debug_answer(challenge["challengeId"])
    return login(client, username, password, captchaId=challenge["challengeId"], captchaAnswer=answer)


# ── 基本路径 ────────────────────────────────────────────────────────────────


def test_successful_login_returns_me_and_a_cookie(auth_client, make_user) -> None:
    user = make_user("alice")
    response = login(auth_client, "alice", user.password)
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] and body["user"]["username"] == "alice"
    assert body["authMode"] == "enforced"
    assert "aragon_sid=" in response.headers["set-cookie"]
    assert auth_client.get(f"{URL}/me").json()["user"]["id"] == user.id


def test_username_is_case_insensitive(auth_client, make_user) -> None:
    user = make_user("MixedCase")
    assert login(auth_client, "mixedcase", user.password).status_code == 200


def test_remember_controls_the_cookie_lifetime(auth_client, make_user) -> None:
    user = make_user("alice")
    session_cookie = login(auth_client, "alice", user.password).headers["set-cookie"].lower()
    assert "max-age" not in session_cookie and "expires" not in session_cookie
    auth_client.post(f"{URL}/logout")
    remembered = login(auth_client, "alice", user.password, remember=True).headers["set-cookie"].lower()
    assert f"max-age={30 * 24 * 3600}" in remembered


def test_wrong_password_and_unknown_user_share_one_message(auth_client, make_user) -> None:
    make_user("alice")
    wrong = login(auth_client, "alice", BAD)
    unknown = login(auth_client, "nobody_here", BAD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"] == "用户名或密码不正确"
    # 头也一致（除每请求都不同的几个）
    volatile = {"x-request-id", "server-timing", "content-length"}
    assert {k: v for k, v in wrong.headers.items() if k not in volatile} == {
        k: v for k, v in unknown.headers.items() if k not in volatile
    }


def test_each_path_runs_exactly_one_scrypt(auth_client, make_user, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-170：不存在的用户与错误密码，**各调用一次** scrypt（调用次数，不是墙钟）。"""
    make_user("alice")
    calls = {"n": 0}
    real = hashlib.scrypt

    def counting(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting)
    login(auth_client, "alice", BAD)
    assert calls["n"] == 1
    login(auth_client, "ghost_user", BAD)
    assert calls["n"] == 2


# ── AC-170 / AC-169：影子节流器 ─────────────────────────────────────────────


def test_existing_and_unknown_usernames_answer_identically_six_times_running(client_from, make_user) -> None:
    make_user("alice")
    real = client_from("203.0.113.1")
    ghost = client_from("203.0.113.2")

    real_shapes, ghost_shapes = [], []
    last_real = last_ghost = None
    for _ in range(6):
        # 第 4 次起需要验证码：用上一次响应里带的那张图作答，答案依然是错误密码。
        if last_real is not None and code_of(last_real) == "captcha_required":
            r = attempt_with_captcha(real, "alice", BAD, last_real)
            g = attempt_with_captcha(ghost, "no_such_person", BAD, last_ghost)
        else:
            r = login(real, "alice", BAD)
            g = login(ghost, "no_such_person", BAD)
        real_shapes.append(shape(r))
        ghost_shapes.append(shape(g))
        last_real, last_ghost = r, g

    assert real_shapes == ghost_shapes, f"\nreal ={real_shapes}\nghost={ghost_shapes}"
    # 并且这个序列恰是规格里的那一个：401, 401, 401(带 captchaRequired), 422 要验证码…
    codes = [shape_[1] for shape_ in real_shapes]
    assert codes[0] == codes[1] == "invalid_credentials"
    assert real_shapes[2] == (401, "invalid_credentials", ("captchaRequired",))
    assert codes[3] == "captcha_required"


def test_three_failures_require_a_captcha_and_five_lock_for_fifteen_minutes(auth_client, make_user, frozen_now) -> None:
    user = make_user("alice")
    for _ in range(2):
        assert login(auth_client, "alice", BAD).json()["error"]["detail"] == {}
    third = login(auth_client, "alice", BAD)
    assert third.json()["error"]["detail"] == {"captchaRequired": True}

    needs = login(auth_client, "alice", BAD)
    assert (needs.status_code, code_of(needs)) == (422, "captcha_required")
    challenge = needs.json()["error"]["detail"]["challenge"]
    assert challenge["image"].startswith("data:image/png;base64,")

    fourth = attempt_with_captcha(auth_client, "alice", BAD, needs)
    assert (fourth.status_code, code_of(fourth)) == (401, "invalid_credentials")
    needs = login(auth_client, "alice", BAD)
    fifth = attempt_with_captcha(auth_client, "alice", BAD, needs)
    assert (fifth.status_code, code_of(fifth)) == (423, "account_locked")
    retry_ms = fifth.json()["error"]["detail"]["retryAfterMs"]
    assert 14 * 60_000 < retry_ms <= 15 * 60_000
    assert int(fifth.headers["retry-after"]) == (retry_ms + 999) // 1000

    # 锁定期间**正确密码也被拒**，且不再叠加失败。
    locked = login(auth_client, "alice", user.password)
    assert (locked.status_code, code_of(locked)) == (423, "account_locked")
    again = login(auth_client, "alice", BAD)
    assert code_of(again) == "account_locked"


def test_lock_lapses_after_fifteen_minutes_and_success_resets_the_counters(
    auth_client, make_user, frozen_now, engine
) -> None:
    user = make_user("alice")
    last = None
    for _ in range(10):
        last = (
            login(auth_client, "alice", BAD)
            if last is None or code_of(last) != "captcha_required"
            else (attempt_with_captcha(auth_client, "alice", BAD, last))
        )
        if code_of(last) == "account_locked":
            break
    assert code_of(last) == "account_locked"

    frozen_now.advance(minutes=15, seconds=1)
    needs = login(auth_client, "alice", user.password)
    assert code_of(needs) == "captcha_required"  # 计数还在 ≥3：先要验证码
    ok = attempt_with_captcha(auth_client, "alice", user.password, needs)
    assert ok.status_code == 200, ok.text
    with sessionmaker(bind=engine)() as db:
        row = db.execute(select(User).where(User.username_lc == "alice")).scalar_one()
        assert (row.failed_logins, row.locked_until) == (0, None)


def test_a_wrong_captcha_is_refused_without_counting_a_password_failure(auth_client, make_user, engine) -> None:
    user = make_user("alice")
    for _ in range(3):
        login(auth_client, "alice", BAD)
    needs = login(auth_client, "alice", user.password)
    assert code_of(needs) == "captcha_required"
    challenge = needs.json()["error"]["detail"]["challenge"]
    wrong = login(auth_client, "alice", user.password, captchaId=challenge["challengeId"], captchaAnswer="AAAAA")
    assert (wrong.status_code, code_of(wrong)) == (422, "captcha_invalid")
    with sessionmaker(bind=engine)() as db:
        assert db.execute(select(User).where(User.username_lc == "alice")).scalar_one().failed_logins == 3


def test_an_admin_unlock_lets_the_user_straight_back_in(auth_client, make_user, engine) -> None:
    user = make_user("alice")
    last = None
    for _ in range(10):
        last = (
            login(auth_client, "alice", BAD)
            if last is None or code_of(last) != "captcha_required"
            else (attempt_with_captcha(auth_client, "alice", BAD, last))
        )
        if code_of(last) == "account_locked":
            break
    assert code_of(last) == "account_locked"
    with sessionmaker(bind=engine)() as db:
        user_service.unlock(db, db.execute(select(User).where(User.username_lc == "alice")).scalar_one())
        db.commit()
    assert login(auth_client, "alice", user.password).status_code == 200


def test_unknown_usernames_lock_too_and_registration_ignores_the_shadow(auth_client, solve_captcha, frozen_now) -> None:
    """影子节流器的副作用与边界：被"锁"的未注册名依然可以被真人注册（注册不查它）。"""
    last = None
    for _ in range(10):
        last = (
            login(auth_client, "future_user", BAD)
            if last is None or code_of(last) != "captcha_required"
            else (attempt_with_captcha(auth_client, "future_user", BAD, last))
        )
        if code_of(last) == "account_locked":
            break
    assert code_of(last) == "account_locked"

    challenge_id, answer = solve_captcha(auth_client, "register")
    registered = auth_client.post(
        f"{URL}/register",
        json={
            "username": "future_user",
            "password": "Aragon-Test-9x",
            "captchaId": challenge_id,
            "captchaAnswer": answer,
        },
    )
    assert registered.status_code == 201


def test_shadow_throttle_forgets_after_thirty_quiet_minutes(frozen_now) -> None:
    ratelimit.shadow_throttle.record_failure("ghost")
    ratelimit.shadow_throttle.record_failure("ghost")
    assert ratelimit.shadow_throttle.state("ghost").failed == 2
    frozen_now.advance(minutes=31)
    assert ratelimit.shadow_throttle.state("ghost").failed == 0


def test_shadow_throttle_is_capacity_bounded() -> None:
    small = ratelimit.ShadowThrottle(capacity=50)
    for index in range(500):
        small.record_failure(f"name{index}")
    assert len(small) == 50


# ── 停用账号 ────────────────────────────────────────────────────────────────


def test_disabled_account_is_revealed_only_to_a_correct_password(auth_client, make_user) -> None:
    make_user("alice", status="disabled", disabled_reason="违规")
    wrong = login(auth_client, "alice", BAD)
    assert (wrong.status_code, code_of(wrong)) == (401, "invalid_credentials")  # 不泄露"被停用"
    right = login(auth_client, "alice", "Aragon-Test-9x")
    assert (right.status_code, code_of(right)) == (403, "account_disabled")
    assert right.json()["error"]["detail"] == {"reason": "违规"}


def test_a_deleted_account_cannot_log_in(auth_client, make_user, engine) -> None:
    user = make_user("alice")
    with sessionmaker(bind=engine)() as db:
        user_service.anonymize(db, user_service.get_by_id(db, user.id))
        db.commit()
    assert code_of(login(auth_client, "alice", user.password)) == "invalid_credentials"


# ── 限速（AC-171）──────────────────────────────────────────────────────────


def test_eleventh_login_from_one_ip_in_a_minute_is_429(auth_client, make_user) -> None:
    user = make_user("alice")
    for index in range(10):
        assert login(auth_client, f"nobody_{index}", BAD).status_code == 401
    eleventh = login(auth_client, "alice", user.password)
    assert eleventh.status_code == 429
    assert code_of(eleventh) == "rate_limited"
    assert int(eleventh.headers["retry-after"]) >= 1
    assert eleventh.json()["error"]["detail"]["scope"] == "auth.login"


def test_same_username_is_limited_across_ips(client_from, make_user) -> None:
    make_user("alice")
    statuses = [login(client_from(f"198.51.100.{index}"), "alice", BAD).status_code for index in range(1, 12)]
    assert statuses[:3] == [401, 401, 401]
    assert 429 in statuses


def test_forged_forwarded_for_cannot_dodge_the_limit_when_proxy_is_not_trusted(auth_client, make_user) -> None:
    """`TRUST_PROXY=0`：`X-Forwarded-For` 被忽略，限速桶仍按连接的对端地址。"""
    for index in range(10):
        response = auth_client.post(
            f"{URL}/login",
            json={"username": f"nobody_{index}", "password": BAD},
            headers={"X-Forwarded-For": f"10.9.8.{index}"},
        )
        assert response.status_code == 401
    forged = auth_client.post(
        f"{URL}/login",
        json={"username": "nobody_x", "password": BAD},
        headers={"X-Forwarded-For": "10.9.8.200"},
    )
    assert forged.status_code == 429


def test_trusted_proxy_uses_the_rightmost_hop(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "trust_proxy", True, raising=False)

    counter = {"n": 0}

    def hit(forwarded: str) -> int:
        counter["n"] += 1  # 每次换用户名：同用户名维度的 10/min 是另一条限速，这里只验 IP 维度
        return auth_client.post(
            f"{URL}/login",
            json={"username": f"nobody_{counter['n']}", "password": BAD},
            headers={"X-Forwarded-For": forwarded},
        ).status_code

    # 最左值是客户端自称的、可任意伪造；最右才是 nginx 追加的真实对端。
    for index in range(10):
        assert hit(f"1.2.3.{index}, 203.0.113.50") in (401, 422)
    assert hit("9.9.9.9, 203.0.113.50") == 429
    assert hit("9.9.9.9, 203.0.113.51") != 429


def test_the_forwarded_for_misconfiguration_warning_fires_once(auth_client, caplog: pytest.LogCaptureFixture) -> None:
    """RV-14：`TRUST_PROXY=0` 却见到代理头，只打一次 WARNING。"""
    with caplog.at_level(logging.WARNING, logger="aragon.security"):
        for _ in range(3):
            auth_client.get(f"{URL}/config", headers={"X-Forwarded-For": "203.0.113.9"})
    warnings = [record for record in caplog.records if "TRUST_PROXY=0" in record.getMessage()]
    assert len(warnings) == 1
    assert "TRUST_PROXY=1" in warnings[0].getMessage()


# ── 审计 ────────────────────────────────────────────────────────────────────


def test_login_outcomes_are_audited_and_failures_are_merged(auth_client, make_user, engine, frozen_now) -> None:
    make_user("alice")
    login(auth_client, "alice", BAD)
    login(auth_client, "alice", BAD)
    frozen_now.advance(seconds=61)
    login(auth_client, "alice", BAD)
    with sessionmaker(bind=engine)() as db:
        failures = (
            db.execute(select(AuditLog).where(AuditLog.action == "auth.login.failure").order_by(AuditLog.id))
            .scalars()
            .all()
        )
    # 同 (用户名, IP) 60 s 内合并成一行并带 count；60 s 后另起一行。
    assert [row.detail_json for row in failures] == ['{"count":2}', '{"count":1}']
    assert all(row.actor_id is None and row.target_name == "alice" for row in failures)


def test_successful_login_and_lockout_are_audited(auth_client, make_user, engine) -> None:
    user = make_user("alice")
    assert login(auth_client, "alice", user.password).status_code == 200
    auth_client.post(f"{URL}/logout")
    last = None
    for _ in range(10):
        last = (
            login(auth_client, "alice", BAD)
            if last is None or code_of(last) != "captcha_required"
            else (attempt_with_captcha(auth_client, "alice", BAD, last))
        )
        if code_of(last) == "account_locked":
            break
    with sessionmaker(bind=engine)() as db:
        actions = [row.action for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars()]
    assert actions.count("auth.login.success") == 1
    assert "auth.login.lockout" in actions
    assert "auth.logout" in actions


def test_login_touches_last_login_and_creates_one_session(auth_client, make_user, engine) -> None:
    user = make_user("alice")
    login(auth_client, "alice", user.password)
    with sessionmaker(bind=engine)() as db:
        row = db.get(User, user.id)
        assert row.last_login_at is not None
        assert len(db.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars().all()) == 1


# ── 锁定对 root 同样生效（AC-169）──────────────────────────────────────────


def test_root_is_locked_like_anyone_else(auth_client) -> None:
    last = None
    for _ in range(10):
        last = (
            login(auth_client, "jdqqjr", BAD)
            if last is None or code_of(last) != "captcha_required"
            else (attempt_with_captcha(auth_client, "jdqqjr", BAD, last))
        )
        if code_of(last) == "account_locked":
            break
    assert code_of(last) == "account_locked"
    assert code_of(login(auth_client, "jdqqjr", "13519126390")) == "account_locked"


# ── AC-183：AUTH_MODE=open ──────────────────────────────────────────────────


def test_open_mode_makes_everyone_a_service_principal_and_says_so(client, caplog: pytest.LogCaptureFixture) -> None:
    me = client.get(f"{URL}/me").json()
    assert me["authMode"] == "open"
    assert me["authenticated"] is False and me["user"] is None
    assert "admin.audit.read" in me["permissions"] and "cost.manage" in me["permissions"], "open = 全权限集"
    assert client.get("/api/v1/cost/summary").status_code == 200


def test_open_mode_logs_an_error_banner_at_startup(client, caplog: pytest.LogCaptureFixture) -> None:
    """启动日志里有 ERROR 级横幅（lifespan 在 `client` 夹具 setup 阶段跑，所以读 setup 阶段的记录）。"""
    banners = [
        record
        for record in caplog.get_records("setup")
        if record.levelno == logging.ERROR and "AUTH_MODE=open" in record.getMessage()
    ]
    assert banners, "AUTH_MODE=open 必须在启动时打 ERROR 横幅"


def test_flipping_to_enforced_takes_effect_on_the_next_request(client, auth_mode_enforced) -> None:
    """D-8：`auth_mode` 在**请求时**读取，不在导入期缓存。"""
    assert client.get(f"{URL}/me").json()["authMode"] == "enforced"
    guest_write = client.post("/api/v1/watchlist", json={"instrumentId": "CN:600519.SH"})
    assert guest_write.status_code == 401
    assert guest_write.json()["error"]["code"] == "unauthorized"
    assert guest_write.json()["error"]["detail"] == {"reason": "no_session"}
