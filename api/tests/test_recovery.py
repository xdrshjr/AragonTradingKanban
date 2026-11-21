"""恢复码与找回密码（spec §25.3.3；AC-217 – AC-224、AC-239 – AC-241、AC-243、AC-248、AC-249）。

先写的两条是 Y3 能否成立的前提：AC-239（`recover` 验证码用途）与 AC-240（`recover` 的 CAS）。
验证码答案取自测试侧表（`solve_captcha`），不 OCR；`frozen_now` 让限速窗口与 60 s 合并是确定性的。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, User, UserSession
from app.security import ratelimit, recovery
from app.services import auth_service
from app.services.audit_service import ACTIONS

AUTH = "/api/v1/auth"
ADMIN = "/api/v1/admin"
GOOD = "Aragon-Test-9x"
NEW = "Fresh-Pass-4kQ"
KEY_FORMAT = re.compile(r"^[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}$")


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _frozen(frozen_now):  # noqa: ANN001, ANN202
    """A frozen clock for every test here: registration needs the 1.2 s floor to have passed."""
    return frozen_now


@pytest.fixture()
def relaxed_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-IP recover budget (5 / 10 min) is not what most tests are about."""
    monkeypatch.setitem(ratelimit.RATE_LIMITS, "auth.recover", ratelimit.Limit(600, 10_000))


def register(client, solve_captcha, username: str = "alice") -> dict:  # noqa: ANN001
    challenge_id, answer = solve_captcha(client, "register")
    response = client.post(
        f"{AUTH}/register",
        json={"username": username, "password": GOOD, "captchaId": challenge_id, "captchaAnswer": answer},
    )
    assert response.status_code == 201, response.text
    return response.json()


def recover(  # noqa: ANN201
    client,  # noqa: ANN001
    solve_captcha,  # noqa: ANN001
    username: str = "alice",
    key: str = "AAAAA-AAAAA-AAAAA-AAAAA",
    password: str = NEW,
    *,
    captcha: bool = True,
    purpose: str = "recover",
):
    body: dict = {"username": username, "recoveryKey": key, "newPassword": password}
    if captcha:
        challenge_id, answer = solve_captcha(client, purpose)
        body.update(captchaId=challenge_id, captchaAnswer=answer)
    return client.post(f"{AUTH}/recover", json=body)


def error_of(response) -> dict:  # noqa: ANN001
    return response.json()["error"]


def body_without_request_id(response) -> dict:  # noqa: ANN001
    payload = response.json()
    payload["error"]["requestId"] = None
    return payload


def user_row(engine, username: str) -> User:  # noqa: ANN001
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        return db.execute(select(User).where(User.username_lc == username.lower())).scalar_one()


def audit_rows(engine, action: str) -> list[AuditLog]:  # noqa: ANN001
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        return list(db.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).scalars())


def login(client, username: str, password: str):  # noqa: ANN001, ANN201
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


# ── AC-239 · `recover` 验证码用途（RV-Y1）────────────────────────────────────


def test_the_captcha_endpoint_issues_recover_challenges(auth_client) -> None:  # noqa: ANN001
    assert auth_client.get(f"{AUTH}/captcha", params={"purpose": "recover"}).status_code == 200
    assert auth_client.get(f"{AUTH}/captcha", params={"purpose": "bogus"}).status_code == 422


def test_captcha_purposes_are_bound_both_ways(auth_client, solve_captcha, relaxed_ip, frozen_now) -> None:  # noqa: ANN001
    for issued_for in ("register", "login"):
        response = recover(auth_client, solve_captcha, purpose=issued_for)
        assert response.status_code == 422 and error_of(response)["code"] == "captcha_invalid"
    # …and a `recover` picture does not register anybody.
    challenge_id, answer = solve_captcha(auth_client, "recover")
    registered = auth_client.post(
        f"{AUTH}/register",
        json={"username": "bob", "password": GOOD, "captchaId": challenge_id, "captchaAnswer": answer},
    )
    assert registered.status_code == 422 and error_of(registered)["code"] == "captcha_invalid"


def test_the_too_fast_trap_belongs_to_register_only(auth_client, solve_captcha, relaxed_ip, frozen_now) -> None:  # noqa: ANN001
    """A `recover` answer submitted straight away (clock frozen: 0 s) is **not** treated as a script."""
    challenge_id, answer = solve_captcha(auth_client, "recover", settle=False)
    response = auth_client.post(
        f"{AUTH}/recover",
        json={
            "username": "nobody",
            "recoveryKey": "AAAAA-AAAAA-AAAAA-AAAAA",
            "newPassword": NEW,
            "captchaId": challenge_id,
            "captchaAnswer": answer,
        },
    )
    assert response.status_code == 401 and error_of(response)["code"] == "recovery_invalid"


# ── AC-240 · CAS（RV-Y3）─────────────────────────────────────────────────────


def test_two_recoveries_with_the_same_key_have_exactly_one_winner(  # noqa: ANN001
    auth_client, client_from, solve_captcha, relaxed_ip, engine, monkeypatch
) -> None:
    key = register(auth_client, solve_captcha)["recoveryKey"]
    rival = client_from("203.0.113.7")
    results: dict[str, object] = {}

    def interleave() -> None:
        # The *outer* request has verified the key and is about to swap. Run a complete second
        # `recover` right now: that is the double-click, with its interleaving made certain.
        monkeypatch.setattr(auth_service, "_after_recovery_verify", None)
        results["inner"] = recover(rival, solve_captcha, "alice", key, "Winner-Pass-3mZ")

    monkeypatch.setattr(auth_service, "_after_recovery_verify", interleave)
    outer = recover(auth_client, solve_captcha, "alice", key, "Loser-Pass-5nX")
    inner = results["inner"]

    assert inner.status_code == 200, inner.text
    assert outer.status_code == 401 and error_of(outer)["code"] == "recovery_invalid"

    winner_key = inner.json()["recoveryKey"]
    row = user_row(engine, "alice")
    assert row.recovery_hash == hashlib.sha256(recovery.normalize(winner_key).encode()).hexdigest()
    assert login(auth_client, "alice", "Winner-Pass-3mZ").status_code == 200
    assert login(auth_client, "alice", "Loser-Pass-5nX").status_code == 401
    assert len(audit_rows(engine, "auth.password.recover")) == 1
    assert len(audit_rows(engine, "auth.recovery.failure")) == 1


# ── AC-217 · 签发与"明文不落地" ──────────────────────────────────────────────


def test_registration_returns_a_well_formed_key_exactly_once(auth_client, solve_captcha, engine) -> None:  # noqa: ANN001
    body = register(auth_client, solve_captcha)
    key = body["recoveryKey"]
    assert KEY_FORMAT.match(key)
    assert body["user"]["hasRecoveryKey"] is True
    row = user_row(engine, "alice")
    assert row.recovery_hash == hashlib.sha256(recovery.normalize(key).encode()).hexdigest()
    assert row.recovery_set_at

    # Never again: neither `me` nor `login` carries a recoveryKey member, and no text contains it.
    for response in (auth_client.get(f"{AUTH}/me"), login(auth_client, "alice", GOOD)):
        assert "recoveryKey" not in response.json()
        assert key not in response.text and recovery.normalize(key) not in response.text


def test_the_plaintext_key_is_nowhere_in_the_database_the_audit_or_the_log(  # noqa: ANN001
    auth_client, solve_captcha, engine, caplog, relaxed_ip
) -> None:
    caplog.set_level(logging.DEBUG)
    first = register(auth_client, solve_captcha)["recoveryKey"]
    rotated = auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": GOOD}).json()["recoveryKey"]
    auth_client.post(f"{AUTH}/logout")
    third = recover(auth_client, solve_captcha, "alice", rotated).json()["recoveryKey"]
    assert len({first, rotated, third}) == 3

    needles = [needle for key in (first, rotated, third) for needle in (key, recovery.normalize(key))]
    path = Path(engine.url.database)
    blobs = [path.read_bytes()]
    for suffix in ("-wal", "-journal"):
        extra = Path(str(path) + suffix)
        if extra.exists():
            blobs.append(extra.read_bytes())
    with sessionmaker(bind=engine)() as db:
        audit_text = json.dumps([[row.detail_json, row.target_name, row.actor_name] for row in db.query(AuditLog)])
    for needle in needles:
        assert not any(needle.encode() in blob for blob in blobs), f"{needle} is in the database file"
        assert needle not in audit_text, f"{needle} is in the audit log"
        assert needle not in caplog.text, f"{needle} is in the application log"


# ── AC-218 · 成功路径 ────────────────────────────────────────────────────────


def test_recovery_replaces_the_password_revokes_every_session_and_rotates_the_key(  # noqa: ANN001
    auth_client, client_from, solve_captcha, relaxed_ip, engine, make_user
) -> None:
    key = register(auth_client, solve_captcha)["recoveryKey"]
    other_device = client_from("198.51.100.9")
    assert login(other_device, "alice", GOOD).status_code == 200
    with sessionmaker(bind=engine)() as db:  # locked + failed attempts: recovery must clear them
        db.execute(
            update(User).where(User.username_lc == "alice").values(failed_logins=7, locked_until="2999-01-01T00:00:00Z")
        )
        db.commit()

    response = recover(auth_client, solve_captcha, "alice", key.lower().replace("-", " "))
    assert response.status_code == 200, response.text
    new_key = response.json()["recoveryKey"]
    assert KEY_FORMAT.match(new_key) and new_key != key and response.json()["setAt"]

    assert other_device.get(f"{AUTH}/sessions").status_code == 401  # every session is gone
    assert auth_client.get(f"{AUTH}/me").json()["authenticated"] is False  # and it did not sign anybody in
    assert login(auth_client, "alice", GOOD).status_code == 401
    assert login(auth_client, "alice", NEW).status_code == 200  # no lockout left
    row = user_row(engine, "alice")
    assert (row.failed_logins, row.locked_until) == (0, None)
    assert auth_client.get(f"{AUTH}/me").json()["user"]["hasRecoveryKey"] is True

    with sessionmaker(bind=engine)() as db:
        reasons = {s.revoked_reason for s in db.execute(select(UserSession)).scalars() if s.revoked_at}
    assert "password_recover" in reasons

    # The old key is dead, the new one works.
    auth_client.post(f"{AUTH}/logout")
    assert recover(auth_client, solve_captcha, "alice", key).status_code == 401
    assert recover(auth_client, solve_captcha, "alice", new_key, "Third-Pass-8wQ").status_code == 200

    rows = audit_rows(engine, "auth.password.recover")
    assert len(rows) == 2 and rows[0].actor_name == "alice"
    for row in rows:
        assert key not in (row.detail_json or "") and new_key not in (row.detail_json or "")
    del make_user


def test_the_key_is_forgiving_about_how_it_was_copied(auth_client, solve_captcha, relaxed_ip) -> None:  # noqa: ANN001
    key = register(auth_client, solve_captcha)["recoveryKey"]
    sloppy = key.replace("0", "O").replace("1", "I").lower().replace("-", "  ")
    assert recover(auth_client, solve_captcha, "alice", f"  {sloppy}\n").status_code == 200


# ── AC-219 · 逐字节一致的失败 ────────────────────────────────────────────────


def test_every_key_or_user_failure_is_the_same_response(  # noqa: ANN001
    auth_client, solve_captcha, relaxed_ip, engine, make_user, monkeypatch
) -> None:
    good_key = register(auth_client, solve_captcha, "has_key")["recoveryKey"]
    auth_client.post(f"{AUTH}/logout")
    make_user("no_key")
    make_user("off_user", status="disabled", recovery_hash=recovery.hash_key(good_key))
    make_user("gone_user", status="deleted", recovery_hash=recovery.hash_key(good_key))
    with sessionmaker(bind=engine)() as db:  # root *with* a key set, asked with the right key
        db.execute(update(User).where(User.role == "root").values(recovery_hash=recovery.hash_key(good_key)))
        db.commit()

    calls: list[str | None] = []
    real_verify = recovery.verify
    monkeypatch.setattr(recovery, "verify", lambda value, stored: calls.append(stored) or real_verify(value, stored))

    cases = {
        "unknown name": ("ghost_user", good_key),
        "wrong key": ("has_key", "BBBBB-BBBBB-BBBBB-BBBBB"),
        "no key set": ("no_key", good_key),
        "disabled": ("off_user", good_key),
        "deleted": ("gone_user", good_key),
        "root": ("jdqqjr", good_key),
        "malformed key": ("has_key", "not a key"),
        "empty key": ("has_key", ""),
    }
    responses = {name: recover(auth_client, solve_captcha, user, key) for name, (user, key) in cases.items()}
    shapes = {name: (r.status_code, body_without_request_id(r)) for name, r in responses.items()}
    assert {shape[0] for shape in shapes.values()} == {401}
    assert all(shape == shapes["wrong key"] for shape in shapes.values()), shapes
    assert error_of(responses["root"])["code"] == "recovery_invalid"
    # The unknown-name path did the same hash + constant-time compare as the others (timing).
    assert len(calls) == len(cases) and calls[0] == recovery.DUMMY_HASH
    # …and none of it changed any password.
    assert login(auth_client, "has_key", GOOD).status_code == 200


# ── AC-220 / AC-248 · 限速 ──────────────────────────────────────────────────


def test_the_sixth_attempt_from_one_ip_is_rate_limited(auth_client, solve_captcha) -> None:  # noqa: ANN001
    statuses = [recover(auth_client, solve_captcha, f"ghost_{i}").status_code for i in range(6)]
    assert statuses == [401] * 5 + [429]


def test_the_site_wide_budget_is_thirty_an_hour(auth_client, solve_captcha, relaxed_ip) -> None:  # noqa: ANN001
    statuses = [recover(auth_client, solve_captcha, f"ghost_{i}").status_code for i in range(31)]
    assert statuses[:30] == [401] * 30 and statuses[30] == 429


def test_the_username_bucket_counts_only_after_the_captcha_and_for_ghosts_too(  # noqa: ANN001
    auth_client, solve_captcha, relaxed_ip, monkeypatch
) -> None:
    monkeypatch.setitem(ratelimit.RATE_LIMITS, "auth.recover.global", ratelimit.Limit(3600, 10_000))
    # AC-248: ten wrong pictures do not touch the name's bucket…
    for _ in range(10):
        response = recover(auth_client, solve_captcha, "ghost_name", captcha=False)
        assert response.status_code == 422 and error_of(response)["code"] == "captcha_invalid"
    # …so the first *correct* picture is still processed (401, not 429)…
    assert recover(auth_client, solve_captcha, "ghost_name").status_code == 401
    # …and a non-existent name is counted exactly like an existing one: five in all, the sixth is 429.
    statuses = [recover(auth_client, solve_captcha, "ghost_name").status_code for _ in range(5)]
    assert statuses == [401, 401, 401, 401, 429]


def test_a_wrong_picture_does_not_burn_the_key(auth_client, solve_captcha, relaxed_ip) -> None:  # noqa: ANN001
    key = register(auth_client, solve_captcha)["recoveryKey"]
    auth_client.post(f"{AUTH}/logout")
    bad = recover(auth_client, solve_captcha, "alice", key, captcha=False)
    assert bad.status_code == 422 and error_of(bad)["code"] == "captcha_invalid"
    assert recover(auth_client, solve_captcha, "alice", key).status_code == 200


# ── AC-221 · 顺序性质 ───────────────────────────────────────────────────────


def test_the_password_policy_is_judged_before_the_key_and_does_not_burn_it(  # noqa: ANN001
    auth_client, solve_captcha, relaxed_ip
) -> None:
    key = register(auth_client, solve_captcha)["recoveryKey"]
    auth_client.post(f"{AUTH}/logout")
    wrong_key = recover(auth_client, solve_captcha, "alice", "BBBBB-BBBBB-BBBBB-BBBBB", "weak")
    assert wrong_key.status_code == 422 and error_of(wrong_key)["code"] == "weak_password"
    right_key = recover(auth_client, solve_captcha, "alice", key, "weak")
    assert right_key.status_code == 422 and error_of(right_key)["code"] == "weak_password"
    assert "min_length" in error_of(right_key)["detail"]["rules"]
    assert recover(auth_client, solve_captcha, "alice", key).status_code == 200  # still valid


# ── AC-222 · 轮换与总开关 ───────────────────────────────────────────────────


def test_rotating_needs_the_password_and_kills_the_old_key(auth_client, solve_captcha, engine, relaxed_ip) -> None:  # noqa: ANN001
    old = register(auth_client, solve_captcha)["recoveryKey"]
    wrong = auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": "Wrong-Pass-1x"})
    assert wrong.status_code == 401 and error_of(wrong)["code"] == "invalid_credentials"
    rotated = auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": GOOD})
    assert rotated.status_code == 200
    new = rotated.json()["recoveryKey"]
    assert KEY_FORMAT.match(new) and new != old
    assert [r.actor_name for r in audit_rows(engine, "auth.recovery.rotate")] == ["alice"]

    auth_client.post(f"{AUTH}/logout")
    assert recover(auth_client, solve_captcha, "alice", old).status_code == 401
    assert recover(auth_client, solve_captcha, "alice", new).status_code == 200


def test_rotation_is_limited_to_three_a_minute_and_a_guest_gets_401(auth_client, solve_captcha, frozen_now) -> None:  # noqa: ANN001
    assert auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": GOOD}).status_code == 401
    register(auth_client, solve_captcha)
    statuses = [auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": GOOD}).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    frozen_now.advance(seconds=61)
    assert auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": GOOD}).status_code == 200


def test_an_older_account_without_a_key_can_mint_one(auth_client, make_user, login_as, engine) -> None:  # noqa: ANN001
    older = make_user("older_user")
    login_as(auth_client, older)
    assert auth_client.get(f"{AUTH}/me").json()["user"]["hasRecoveryKey"] is False
    response = auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": older.password})
    assert response.status_code == 200 and KEY_FORMAT.match(response.json()["recoveryKey"])
    assert auth_client.get(f"{AUTH}/me").json()["user"]["hasRecoveryKey"] is True


def test_the_master_switch_is_read_fresh_every_time(  # noqa: ANN001
    auth_client, solve_captcha, make_user, login_as, relaxed_ip
) -> None:
    admin_user = make_user("switch_admin", role="admin")
    admin = auth_client.__class__(auth_client.app)
    login_as(admin, admin_user)
    key = register(auth_client, solve_captcha)["recoveryKey"]

    def switch(value: bool) -> None:
        assert admin.patch(f"{ADMIN}/settings", json={"authRecoveryEnabled": value}).status_code == 200

    switch(False)
    assert auth_client.get(f"{AUTH}/config").json()["recoveryEnabled"] is False
    assert recover(auth_client, solve_captcha, "alice", key).status_code == 403
    assert error_of(recover(auth_client, solve_captcha, "alice", key))["code"] == "recovery_disabled"
    rotate = auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": GOOD})
    assert rotate.status_code == 403 and error_of(rotate)["code"] == "recovery_disabled"
    newcomer = auth_client.__class__(auth_client.app)
    assert register(newcomer, solve_captcha, "carol")["recoveryKey"] is None

    switch(True)
    assert auth_client.get(f"{AUTH}/config").json()["recoveryEnabled"] is True
    assert recover(auth_client, solve_captcha, "alice", key).status_code == 200


# ── AC-243 · 布尔设置的严格口径与缺省（RV-Y8）───────────────────────────────


@pytest.fixture()
def admin_client(auth_client, make_user, login_as):  # noqa: ANN001, ANN201
    client = auth_client.__class__(auth_client.app)
    login_as(client, make_user("settings_admin", role="admin"))
    return client


def test_the_bool_setting_is_strict_and_a_bad_patch_applies_nothing(admin_client) -> None:  # noqa: ANN001
    assert admin_client.get(f"{ADMIN}/settings").json()["authRecoveryEnabled"] is True
    for bad in (1, 0, "true", None, "false"):
        response = admin_client.patch(f"{ADMIN}/settings", json={"authRecoveryEnabled": bad})
        assert response.status_code == 422 and error_of(response)["code"] == "invalid_setting", bad
        assert error_of(response)["detail"]["field"] == "authRecoveryEnabled"
    mixed = admin_client.patch(f"{ADMIN}/settings", json={"signupBonus": 5, "authRecoveryEnabled": "yes"})
    assert mixed.status_code == 422
    assert admin_client.get(f"{ADMIN}/settings").json()["signupBonus"] == 0  # not half-applied
    ok = admin_client.patch(f"{ADMIN}/settings", json={"authRecoveryEnabled": False})
    assert ok.json()["authRecoveryEnabled"] is False


def test_the_default_comes_from_the_environment_and_the_audit_carries_booleans(  # noqa: ANN001
    admin_client, engine, monkeypatch
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_recovery_enabled", False, raising=False)
    assert admin_client.get(f"{ADMIN}/settings").json()["authRecoveryEnabled"] is False
    monkeypatch.setattr(get_settings(), "auth_recovery_enabled", True, raising=False)
    assert admin_client.get(f"{ADMIN}/settings").json()["authRecoveryEnabled"] is True

    assert admin_client.patch(f"{ADMIN}/settings", json={"authRecoveryEnabled": False}).status_code == 200
    detail = json.loads(audit_rows(engine, "settings.update")[-1].detail_json)
    assert detail["changes"]["authRecoveryEnabled"] == {"from": True, "to": False}
    # A stored row now wins over the environment.
    monkeypatch.setattr(get_settings(), "auth_recovery_enabled", True, raising=False)
    assert admin_client.get(f"{ADMIN}/settings").json()["authRecoveryEnabled"] is False


# ── AC-223 · root 与运维兜底 ────────────────────────────────────────────────


def test_root_cannot_use_or_mint_a_recovery_key(auth_client, engine) -> None:  # noqa: ANN001
    with sessionmaker(bind=engine)() as db:
        db.execute(update(User).where(User.role == "root").values(must_change_password=0))
        db.commit()
    assert login(auth_client, "jdqqjr", "13519126390").status_code == 200
    response = auth_client.post(f"{AUTH}/recovery-key", json={"currentPassword": "13519126390"})
    assert response.status_code == 403
    assert error_of(response)["code"] == "root_protected"
    assert error_of(response)["detail"] == {"action": "recovery_key"}


def test_the_cli_clears_a_recovery_key_and_audits_as_system(  # noqa: ANN001
    auth_client, solve_captcha, engine, monkeypatch, relaxed_ip
) -> None:
    from sqlalchemy.orm import sessionmaker as make_sessionmaker

    from app import cli
    from app.db import session as db_session

    key = register(auth_client, solve_captcha)["recoveryKey"]
    monkeypatch.setattr(db_session, "SessionLocal", make_sessionmaker(bind=engine, expire_on_commit=False))
    assert cli.main(["clear-recovery", "alice"]) == 0
    assert user_row(engine, "alice").recovery_hash is None
    row = audit_rows(engine, "auth.recovery.rotate")[-1]
    assert row.actor_name == "system" and json.loads(row.detail_json)["cleared"] is True
    auth_client.post(f"{AUTH}/logout")
    assert recover(auth_client, solve_captcha, "alice", key).status_code == 401
    assert cli.main(["clear-recovery", "nobody_here"]) == 1


# ── AC-241 · `auth.recovery.failure` ────────────────────────────────────────


def test_recovery_failures_are_audited_merged_and_shape_blind(  # noqa: ANN001
    auth_client, solve_captcha, relaxed_ip, engine, make_user, frozen_now
) -> None:
    assert "auth.recovery.failure" in ACTIONS and len(ACTIONS) == 41
    register(auth_client, solve_captcha, "real_one")
    auth_client.post(f"{AUTH}/logout")

    for _ in range(3):
        recover(auth_client, solve_captcha, "real_one", "BBBBB-BBBBB-BBBBB-BBBBB")
    for _ in range(2):
        recover(auth_client, solve_captcha, "ghost_one", "BBBBB-BBBBB-BBBBB-BBBBB")
    # A wrong picture and a rate-limited request are *not* audited (they would flood the log).
    recover(auth_client, solve_captcha, "real_one", "BBBBB-BBBBB-BBBBB-BBBBB", captcha=False)

    rows = audit_rows(engine, "auth.recovery.failure")
    by_name = {row.target_name: row for row in rows}
    assert set(by_name) == {"real_one", "ghost_one"} and len(rows) == 2
    assert json.loads(by_name["real_one"].detail_json) == {"count": 3}
    assert json.loads(by_name["ghost_one"].detail_json) == {"count": 2}

    def shape(row: AuditLog) -> dict:
        return {
            "target_type": row.target_type,
            "target_id": row.target_id,
            "actor_id": row.actor_id,
            "actor_role": row.actor_role,
            "ip": row.ip,
            "detail_keys": sorted(json.loads(row.detail_json)),
        }

    assert shape(by_name["real_one"]) == shape(by_name["ghost_one"])
    assert by_name["real_one"].target_id is None  # never the user id

    frozen_now.advance(seconds=61)  # a later minute starts a new row
    recover(auth_client, solve_captcha, "real_one", "BBBBB-BBBBB-BBBBB-BBBBB")
    assert len(audit_rows(engine, "auth.recovery.failure")) == 3
    del make_user


# ── AC-249 · 日志脱敏 ───────────────────────────────────────────────────────


def test_the_redactor_knows_the_recovery_key() -> None:
    from app.logging import redact
    from app.services.audit_service import sanitize_detail

    assert "K7QM4-XNP2R" not in redact("recoveryKey=K7QM4-XNP2R-ABCDE-12345")
    assert "K7QM4-XNP2R" not in redact('{"recoveryKey": "K7QM4-XNP2R-ABCDE-12345"}')
    assert "K7QM4-XNP2R" not in redact("recovery_key: K7QM4-XNP2R-ABCDE-12345")
    assert sanitize_detail({"recoveryKey": "K7QM4", "ok": 1}) == {"ok": 1}


def test_the_structured_log_masks_a_recovery_key_field() -> None:
    from app.logging import RedactFilter

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "x", (), None)
    record.kv = {"recoveryKey": "K7QM4-XNP2R-ABCDE-12345", "note": "fine"}
    RedactFilter().filter(record)
    assert record.kv["recoveryKey"] == "***" and record.kv["note"] == "fine"


# ── the pure module ─────────────────────────────────────────────────────────


def test_generated_keys_are_well_formed_and_unique() -> None:
    keys = {recovery.generate_key() for _ in range(500)}
    assert len(keys) == 500 and all(KEY_FORMAT.match(key) for key in keys)


def test_normalize_hash_and_verify() -> None:
    key = recovery.generate_key()
    assert recovery.normalize(f" {key.lower()} ") == key.replace("-", "")
    assert recovery.normalize("o0-iL1") == "001" + "11"
    digest = recovery.hash_key(key)
    assert len(digest) == 64 and recovery.verify(key.lower(), digest)
    assert not recovery.verify("", digest) and not recovery.verify(key, None) and not recovery.verify(key, "")
    assert not recovery.verify(key, recovery.DUMMY_HASH)


def test_verify_is_not_a_timing_oracle_for_the_unknown_name_path() -> None:
    """Both paths run the same two operations (hash the input, compare 64 bytes)."""
    digest = recovery.hash_key("A" * 20)
    start = time.perf_counter()
    for _ in range(2000):
        recovery.verify("B" * 20, digest)
    known = time.perf_counter() - start
    start = time.perf_counter()
    for _ in range(2000):
        recovery.verify("B" * 20, recovery.DUMMY_HASH)
    dummy = time.perf_counter() - start
    assert 0.2 < known / max(dummy, 1e-9) < 5
