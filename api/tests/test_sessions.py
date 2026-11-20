"""会话（spec §24.3.2；AC-155–162、164）：令牌只存哈希、到期、滑动写节流、10 会话上限、即时失效。

时间全部来自注入的 `frozen_now`——到期与节流是被"推进时钟"验证的，没有一个 `sleep`。
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, UserSession
from app.services import user_service

URL = "/api/v1/auth"


def me(client):  # noqa: ANN001, ANN201
    return client.get(f"{URL}/me").json()


def rows(engine) -> list[UserSession]:  # noqa: ANN001
    with sessionmaker(bind=engine)() as db:
        return list(db.execute(select(UserSession).order_by(UserSession.created_at, UserSession.id)).scalars())


# ── 到期（AC-156）──────────────────────────────────────────────────────────


def test_a_browser_session_expires_after_twelve_hours(auth_client, make_user, login_as, frozen_now) -> None:
    login_as(auth_client, make_user("alice"))
    frozen_now.advance(hours=11, minutes=59)
    assert me(auth_client)["authenticated"] is True
    frozen_now.advance(minutes=2)
    response = auth_client.get(f"{URL}/me")
    assert response.json()["authenticated"] is False
    # 死 Cookie 被清掉（`Max-Age=0`），主体降级为 guest，**不**对公开路由报 401。
    assert response.status_code == 200
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert 'aragon_sid=""' in response.headers["set-cookie"] or "aragon_sid=;" in response.headers["set-cookie"]


def test_a_remembered_session_idles_out_after_seven_days(auth_client, make_user, login_as, frozen_now) -> None:
    login_as(auth_client, make_user("alice"), remember=True)
    frozen_now.advance(days=6, hours=23)
    assert me(auth_client)["authenticated"] is True  # 这次请求把空闲窗口滑到 +7 天
    frozen_now.advance(days=6, hours=23)
    assert me(auth_client)["authenticated"] is True
    frozen_now.advance(days=7, minutes=1)
    assert me(auth_client)["authenticated"] is False


def test_a_remembered_session_has_a_thirty_day_absolute_ceiling(auth_client, make_user, login_as, frozen_now) -> None:
    """哪怕每隔 6 天活跃一次，绝对 30 天后也失效。"""
    login_as(auth_client, make_user("alice"), remember=True)
    for _ in range(4):
        frozen_now.advance(days=6)
        assert me(auth_client)["authenticated"] is True  # 24 天
    frozen_now.advance(days=5, hours=23)  # 29 天 23 小时：还在
    assert me(auth_client)["authenticated"] is True
    frozen_now.advance(hours=2)  # 30 天 1 小时：绝对到期
    assert me(auth_client)["authenticated"] is False


def test_an_expired_cookie_on_a_protected_route_is_401_expired(auth_client, make_user, login_as, frozen_now) -> None:
    login_as(auth_client, make_user("alice"))
    frozen_now.advance(hours=13)
    response = auth_client.post("/api/v1/watchlist", json={"instrumentId": "CN:600519.SH"})
    assert response.status_code == 401
    assert response.json()["error"]["detail"] == {"reason": "expired"}


# ── 令牌只存哈希（AC-155）──────────────────────────────────────────────────


def test_the_database_holds_a_hash_not_the_cookie(auth_client, make_user, login_as, engine) -> None:
    import hashlib

    login_as(auth_client, make_user("alice"))
    cookie = auth_client.cookies.get("aragon_sid")
    (row,) = rows(engine)
    assert row.token_hash == hashlib.sha256(cookie.encode()).hexdigest()
    assert row.token_hash != cookie and len(row.token_hash) == 64
    # 拿数据库内容（哈希）当 Cookie 用，认不出任何人。
    auth_client.cookies.clear()
    auth_client.cookies.set("aragon_sid", row.token_hash)
    assert me(auth_client)["authenticated"] is False


# ── 写节流（AC-162）────────────────────────────────────────────────────────


def _count_session_updates(engine):  # noqa: ANN001, ANN202
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _spy(_conn, _cursor, statement, *_args):  # noqa: ANN001, ANN202
        if statement.lstrip().upper().startswith("UPDATE SESSIONS"):
            statements.append(statement)

    return statements


def test_last_seen_is_written_at_most_once_per_five_minutes(
    auth_client, make_user, login_as, frozen_now, engine
) -> None:
    login_as(auth_client, make_user("alice"))
    writes = _count_session_updates(engine)
    for _ in range(100):
        me(auth_client)
    assert len(writes) == 0, "5 分钟内一次都不该写（会话刚建，last_seen 就是现在）"

    frozen_now.advance(minutes=5, seconds=1)
    for _ in range(100):
        me(auth_client)
    assert len(writes) == 1, "跨过 5 分钟后 100 次请求只写 1 次"


def test_the_touch_slides_the_idle_window_but_never_past_the_absolute_expiry(
    auth_client, make_user, login_as, frozen_now, engine
) -> None:
    login_as(auth_client, make_user("alice"), remember=True)
    (before,) = rows(engine)
    frozen_now.advance(days=3)
    me(auth_client)
    (after,) = rows(engine)
    assert after.idle_expires_at > before.idle_expires_at
    assert after.idle_expires_at <= after.expires_at
    assert after.expires_at == before.expires_at, "绝对到期永不滑动"


# ── 上限（AC-158）──────────────────────────────────────────────────────────


def test_the_eleventh_session_evicts_the_oldest(auth_client, make_user, frozen_now, engine, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False, raising=False)
    user = make_user("alice")
    for index in range(11):
        frozen_now.advance(seconds=1)
        response = auth_client.post(f"{URL}/login", json={"username": "alice", "password": user.password})
        assert response.status_code == 200, (index, response.text)
    sessions = rows(engine)
    assert len(sessions) == 11
    live = [row for row in sessions if row.revoked_at is None]
    assert len(live) == 10
    assert sessions[0].revoked_at is not None and sessions[0].revoked_reason == "evicted"
    assert all(row.revoked_reason is None for row in sessions[1:])


# ── 即时生效（AC-157）──────────────────────────────────────────────────────


def test_disabling_a_user_logs_them_out_on_the_very_next_request(auth_client, make_user, login_as, engine) -> None:
    user = make_user("alice")
    login_as(auth_client, user)
    assert me(auth_client)["authenticated"] is True
    with sessionmaker(bind=engine)() as db:
        user_service.disable(db, user_service.get_by_id(db, user.id), "测试")
        db.commit()
    assert me(auth_client)["authenticated"] is False
    # 浏览器收到清 Cookie 的指令，死 Cookie 不会留着。
    assert auth_client.cookies.get("aragon_sid") is None


def test_a_role_change_is_visible_on_the_next_request(auth_client, make_user, login_as, engine) -> None:
    user = make_user("alice")
    login_as(auth_client, user)
    assert auth_client.get("/api/v1/cost/summary").status_code == 403
    with sessionmaker(bind=engine)() as db:
        user_service.set_role(db, user_service.get_by_id(db, user.id), "admin")
        db.commit()
    assert me(auth_client)["user"]["role"] == "admin"
    assert auth_client.get("/api/v1/cost/summary").status_code == 200
    with sessionmaker(bind=engine)() as db:
        user_service.set_role(db, user_service.get_by_id(db, user.id), "user")
        db.commit()
    assert auth_client.get("/api/v1/cost/summary").status_code == 403


def test_changing_the_password_revokes_other_sessions_and_keeps_this_one(
    auth_client, client_from, make_user, login_as, engine
) -> None:
    """AC-159。"""
    user = make_user("alice")
    phone = client_from("198.51.100.8")
    login_as(phone, user)
    login_as(auth_client, user)

    changed = auth_client.post(
        f"{URL}/password", json={"currentPassword": user.password, "newPassword": "Another-Strong-7q"}
    )
    assert changed.status_code == 204
    assert me(auth_client)["authenticated"] is True, "当前会话保持"
    assert me(phone)["authenticated"] is False, "其它设备被踢"
    reasons = sorted(row.revoked_reason or "" for row in rows(engine))
    assert reasons == ["", "password_change"]

    # 旧密码不可再登录，新密码可以。
    fresh = client_from("198.51.100.9")
    assert fresh.post(f"{URL}/login", json={"username": "alice", "password": user.password}).status_code == 401
    assert fresh.post(f"{URL}/login", json={"username": "alice", "password": "Another-Strong-7q"}).status_code == 200


def test_password_change_validates_current_and_policy(auth_client, make_user, login_as) -> None:
    user = make_user("alice")
    login_as(auth_client, user)
    wrong = auth_client.post(
        f"{URL}/password", json={"currentPassword": "nope-Nope-1", "newPassword": "Another-Strong-7q"}
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "invalid_credentials")
    weak = auth_client.post(f"{URL}/password", json={"currentPassword": user.password, "newPassword": "12345678"})
    assert (weak.status_code, weak.json()["error"]["code"]) == (422, "weak_password")
    guest = auth_client.__class__(auth_client.app)
    assert guest.post(f"{URL}/password", json={"currentPassword": "x", "newPassword": "y"}).status_code == 401


# ── /auth/me、/auth/sessions、登出 ─────────────────────────────────────────


def test_me_never_401s_for_a_guest(auth_client) -> None:
    """AC-160。"""
    response = auth_client.get(f"{URL}/me")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False and body["user"] is None and body["quota"] is None
    assert set(body["permissions"]) == {"report.read", "market.read", "search.read", "ask.cite", "export.read"}


def test_session_list_truncates_ips_and_only_shows_your_own(client_from, make_user, login_as) -> None:
    """AC-161。"""
    alice, bob = make_user("alice"), make_user("bobby")
    alice_phone = client_from("203.0.113.77")
    alice_laptop = client_from("198.51.100.5")
    bob_client = client_from("192.0.2.33")
    login_as(alice_phone, alice)
    login_as(alice_laptop, alice)
    login_as(bob_client, bob)

    listing = alice_laptop.get(f"{URL}/sessions").json()["items"]
    assert len(listing) == 2
    assert {item["ip"] for item in listing} == {"203.0.113.0/24", "198.51.100.0/24"}
    assert [item["current"] for item in listing].count(True) == 1
    assert [item for item in listing if item["current"]][0]["ip"] == "198.51.100.0/24"

    foreign = bob_client.get(f"{URL}/sessions").json()["items"][0]["id"]
    # 只能删自己的：别人的会话 id 是 404，不是 403（不泄露存在）。
    assert alice_laptop.delete(f"{URL}/sessions/{foreign}").status_code == 404
    other = [item["id"] for item in listing if not item["current"]][0]
    assert alice_laptop.delete(f"{URL}/sessions/{other}").status_code == 204
    assert me(alice_phone)["authenticated"] is False
    assert me(alice_laptop)["authenticated"] is True


def test_revoking_the_current_session_is_a_logout_and_clears_the_cookie(auth_client, make_user, login_as) -> None:
    login_as(auth_client, make_user("alice"))
    current = auth_client.get(f"{URL}/sessions").json()["items"][0]["id"]
    response = auth_client.delete(f"{URL}/sessions/{current}")
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert me(auth_client)["authenticated"] is False


def test_revoke_others_keeps_the_current_session(auth_client, client_from, make_user, login_as) -> None:
    user = make_user("alice")
    others = [client_from(f"203.0.113.{index}") for index in (11, 12)]
    for other in others:
        login_as(other, user)
    login_as(auth_client, user)
    assert auth_client.post(f"{URL}/sessions/revoke-others").json() == {"revoked": 2}
    assert me(auth_client)["authenticated"] is True
    assert all(me(other)["authenticated"] is False for other in others)


def test_logout_invalidates_the_cookie_immediately_and_is_idempotent(auth_client, make_user, login_as, engine) -> None:
    """AC-164。"""
    login_as(auth_client, make_user("alice"))
    saved = auth_client.cookies.get("aragon_sid")
    assert auth_client.post(f"{URL}/logout").status_code == 204
    assert me(auth_client)["authenticated"] is False
    # 回放旧 Cookie：仍是 guest。
    auth_client.cookies.clear()
    auth_client.cookies.set("aragon_sid", saved)
    assert me(auth_client)["authenticated"] is False
    # 幂等：无会话 / 死会话再登出也是 204。
    assert auth_client.post(f"{URL}/logout").status_code == 204
    assert auth_client.__class__(auth_client.app).post(f"{URL}/logout").status_code == 204
    with sessionmaker(bind=engine)() as db:
        logouts = db.execute(select(AuditLog).where(AuditLog.action == "auth.logout")).scalars().all()
    assert len(logouts) == 1, "审计 auth.logout 只在确有会话时写"


def test_profile_display_name_roundtrip(auth_client, make_user, login_as) -> None:
    login_as(auth_client, make_user("alice"))
    renamed = auth_client.patch(f"{URL}/profile", json={"displayName": "艾丽丝"})
    assert renamed.status_code == 200 and renamed.json()["user"]["displayName"] == "艾丽丝"
    cleared = auth_client.patch(f"{URL}/profile", json={"displayName": None})
    assert cleared.json()["user"]["displayName"] is None
    assert auth_client.patch(f"{URL}/profile", json={"displayName": ""}).status_code == 422
    assert auth_client.patch(f"{URL}/profile", json={"displayName": "x" * 41}).status_code == 422
    assert me(auth_client)["user"]["username"] == "alice", "用户名不可改"


@pytest.mark.parametrize("path", ["/auth/password", "/auth/sessions", "/auth/profile"])
def test_account_routes_need_a_session(auth_client, path: str) -> None:
    method = {
        "/auth/password": auth_client.post,
        "/auth/sessions": auth_client.get,
        "/auth/profile": auth_client.patch,
    }[path]
    response = method(f"/api/v1{path}", json={}) if path != "/auth/sessions" else method(f"/api/v1{path}")
    assert response.status_code == 401
    assert response.json()["error"]["detail"] == {"reason": "no_session"}
