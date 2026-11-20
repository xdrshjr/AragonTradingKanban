"""主体解析：service 令牌、Cookie、错误 Bearer、`AUTH_MODE`（spec §24.3.3、D-7、D-8；AC-182、AC-206 ③④）。

要钉住的是 RV-1 的后果：`API_TOKEN` 现在是**服务主体凭据**，不再是整站闸门——
带它的脚本是 `service`（admin 全集减去"人、钱、门"），不带它的浏览器照常按 Cookie/游客处理，
**错误**的 Bearer 既不让公开路由 401，也不遮蔽合法的 Cookie 会话。
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.security.permissions import ALL_PERMISSIONS, SERVICE_PERMISSIONS

URL = "/api/v1"
TOKEN = "service-token-for-tests"


@pytest.fixture()
def with_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "api_token", TOKEN, raising=False)
    return TOKEN


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def me(client, **kwargs):  # noqa: ANN001, ANN003, ANN201
    return client.get(f"{URL}/auth/me", **kwargs).json()


# ── AC-182 ─────────────────────────────────────────────────────────────────


def test_a_correct_bearer_is_the_service_principal(auth_client, with_token) -> None:
    body = me(auth_client, headers=bearer())
    assert body["authenticated"] is False and body["user"] is None, "service 没有账号"
    assert set(body["permissions"]) == SERVICE_PERMISSIONS
    assert auth_client.get(f"{URL}/cost/summary", headers=bearer()).status_code == 200
    assert auth_client.get(f"{URL}/agent/probe", headers=bearer()).status_code != 403


def test_service_may_generate_and_curate_but_not_touch_people_money_or_doors(auth_client, with_token) -> None:
    """RV-1 / RV-17 / AC-182 / AC-206④：对 `service.minus` 所列权限的路由得 403 forbidden。"""
    allowed = [
        ("GET", f"{URL}/cost/summary"),
        ("GET", f"{URL}/schedules"),
        ("POST", f"{URL}/instruments/CN:600519.SH/reports"),  # report.generate（不记账本）
        ("PATCH", f"{URL}/reports/none"),  # report.manage_own ⇐ report.manage_any
        ("DELETE", f"{URL}/reports/none"),
    ]
    for method, path in allowed:
        response = auth_client.request(method, path, headers=bearer(), json={} if method != "GET" else None)
        assert response.status_code not in (401, 403), (method, path, response.text)

    denied = [
        ("POST", f"{URL}/cost/budget", "cost.manage"),
        ("POST", f"{URL}/watchlist", "watchlist.own"),
        ("GET", f"{URL}/auth/sessions", "account.self"),
        ("POST", f"{URL}/auth/password", "account.self"),
        ("PATCH", f"{URL}/auth/profile", "account.self"),
    ]
    for method, path, permission in denied:
        response = auth_client.request(method, path, headers=bearer(), json={})
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "forbidden"
        assert response.json()["error"]["detail"]["permission"] == permission


def test_without_the_bearer_a_request_is_a_guest_not_a_rejection(auth_client, with_token) -> None:
    """E-1：`API_TOKEN` 设了，**不带**它也不再被整站拒绝。"""
    assert auth_client.get(f"{URL}/reports").status_code == 200
    assert auth_client.get(f"{URL}/auth/config").status_code == 200
    assert me(auth_client)["permissions"] and "report.generate" not in me(auth_client)["permissions"]
    blocked = auth_client.get(f"{URL}/cost/summary")
    assert (blocked.status_code, blocked.json()["error"]["code"]) == (401, "unauthorized")


@pytest.mark.parametrize(
    "header", ["Bearer wrong-token", "Bearer ", "Basic abc", f"bearer{TOKEN}", TOKEN, "Bearer " + TOKEN + "x"]
)
def test_a_wrong_or_malformed_authorization_header_is_just_a_guest(auth_client, with_token, header: str) -> None:
    response = auth_client.get(f"{URL}/reports", headers={"Authorization": header})
    assert response.status_code == 200, "错误 Bearer 不应让公开路由 401"
    assert me(auth_client, headers={"Authorization": header})["authenticated"] is False
    assert auth_client.get(f"{URL}/cost/summary", headers={"Authorization": header}).status_code == 401


def test_the_scheme_name_is_case_insensitive(auth_client, with_token) -> None:
    assert auth_client.get(f"{URL}/cost/summary", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200


def test_an_unset_api_token_never_matches_an_empty_bearer(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "api_token", "", raising=False)
    assert auth_client.get(f"{URL}/cost/summary", headers={"Authorization": "Bearer "}).status_code == 401
    assert auth_client.get(f"{URL}/cost/summary", headers={"Authorization": "Bearer"}).status_code == 401


# ── AC-206 ③：Bearer 与 Cookie 并存 ─────────────────────────────────────────


def test_the_cookie_session_still_resolves_when_api_token_is_set_and_no_bearer_is_sent(
    auth_client, with_token, make_user, login_as
) -> None:
    """浏览器侧不再持有令牌（E-15）：`API_TOKEN` 非空、请求不带 Authorization，Cookie 会话照旧是 user。"""
    login_as(auth_client, make_user("alice"))
    body = me(auth_client)
    assert body["authenticated"] is True and body["user"]["role"] == "user"
    assert auth_client.get(f"{URL}/cost/summary").status_code == 403, "user 不是 admin"


def test_a_wrong_bearer_does_not_mask_a_valid_cookie_session(auth_client, with_token, make_user, login_as) -> None:
    login_as(auth_client, make_user("alice"))
    body = me(auth_client, headers=bearer("stale-token-baked-into-old-js"))
    assert body["authenticated"] is True and body["user"]["username"] == "alice"


def test_a_correct_bearer_wins_over_a_cookie(auth_client, with_token, make_user, login_as) -> None:
    login_as(auth_client, make_user("alice"))
    body = me(auth_client, headers=bearer())
    assert body["authenticated"] is False and set(body["permissions"]) == SERVICE_PERMISSIONS


# ── AC-183：open 模式 ───────────────────────────────────────────────────────


def test_open_mode_service_has_the_whole_vocabulary_not_the_narrowed_set(client) -> None:
    """D-8 / spec §24.3.3 第 1 步：open = 旧"本地优先"的等价物，**不受** `service.minus` 收窄。"""
    body = me(client)
    assert set(body["permissions"]) == ALL_PERMISSIONS != SERVICE_PERMISSIONS
    assert client.post(f"{URL}/cost/budget", json={"monthlyUsd": 0}).status_code != 403


def test_open_mode_ignores_cookies_and_bearers_alike(client, make_user, with_token) -> None:
    assert me(client, headers={"Authorization": "Bearer wrong"})["authMode"] == "open"
    assert client.get(f"{URL}/cost/summary").status_code == 200


def test_account_routes_refuse_the_serviceprincipal_even_in_open_mode(client) -> None:
    """服务主体没有"自己的"账号：`/auth/sessions` 在 open 模式下得 403，而不是 500 或空数据。"""
    response = client.get(f"{URL}/auth/sessions")
    assert response.status_code == 403
    assert response.json()["error"]["detail"]["permission"] == "account.self"


# ── 访客的 /auth/me 等永不 401 ───────────────────────────────────────────────


def test_public_auth_surface_never_401s(auth_client) -> None:
    for path in ("/auth/config", "/auth/me", "/health"):
        assert auth_client.get(f"{URL}{path}").status_code == 200, path
    assert auth_client.post(f"{URL}/auth/logout").status_code == 204
    assert "www-authenticate" not in auth_client.get(f"{URL}/cost/summary").headers, "设了会弹浏览器原生 Basic 对话框"
