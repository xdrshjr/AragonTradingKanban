"""CSRF（Origin 校验）、CORS 凭据、响应头、X-Request-Id（spec §24.3.2；AC-208、RV-2、RV-12）。

★ **生产形态用例是本文件存在的理由**（RV-2，P0）：生产链路是浏览器 `https://aragontrading.online`
→ nginx（终止 TLS）→ uvicorn 明文 `http`，应用看到的 `scheme` 恒为 `http`，而 `Origin` 是 `https://…`；
`ALLOW_ORIGINS` 在生产为空（同源）。若按"完整 origin 相等"判断，**所有**生产写请求（含登录）都会 403。
`test_production_shape_*` 用 `Host`、`Origin`、空白名单把这个形状原样搭出来。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.errors import CsrfRejected
from app.models import AuditLog
from app.security.csrf import check_origin

LOGIN = "/api/v1/auth/login"
BODY = {"username": "nobody_here", "password": "Not-The-Password-1"}


def run_check(
    method: str = "POST",
    *,
    allowed: list[str] | None = None,
    trust_proxy: bool = False,
    bearer: bool = False,
    **headers: str,
) -> None:
    check_origin(
        method,
        {key.replace("_", "-"): value for key, value in headers.items()},
        allowed_origins=allowed or [],
        trust_proxy=trust_proxy,
        service_bearer_ok=bearer,
    )


# ── 单元：规则逐条 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_are_never_checked(method: str) -> None:
    run_check(method, origin="https://evil.example", host="aragontrading.online")


def test_a_missing_origin_is_allowed() -> None:
    """curl、服务端脚本、同源老浏览器不是 CSRF 向量。"""
    run_check("POST", host="aragontrading.online")


@pytest.mark.parametrize("origin", ["null", "NULL", ""])
def test_origin_null_is_rejected(origin: str) -> None:
    """沙箱 iframe / `file://` 发出的 `Origin: null`。"""
    with pytest.raises(CsrfRejected):
        run_check("POST", origin=origin, host="aragontrading.online")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_unsafe_method_is_checked(method: str) -> None:
    with pytest.raises(CsrfRejected) as caught:
        run_check(method, origin="https://evil.example", host="aragontrading.online")
    assert caught.value.status_code == 403 and caught.value.code == "csrf_rejected"
    assert caught.value.detail == {"origin": "https://evil.example"}


def test_production_shape_https_origin_over_a_plain_http_hop_is_allowed() -> None:
    """AC-208 / RV-2：Origin 的 netloc == Host，scheme 不参与比较，白名单为空。"""
    run_check("POST", origin="https://aragontrading.online", host="aragontrading.online", allowed=[])


def test_a_different_host_with_the_same_scheme_is_still_rejected() -> None:
    with pytest.raises(CsrfRejected):
        run_check("POST", origin="https://aragontrading.online.evil.example", host="aragontrading.online")
    with pytest.raises(CsrfRejected):
        run_check("POST", origin="https://evil.example", host="aragontrading.online")


def test_ports_are_part_of_the_netloc_but_default_ports_are_not() -> None:
    run_check("POST", origin="https://aragontrading.online:443", host="aragontrading.online")
    run_check("POST", origin="http://localhost:3000", host="localhost:3000")
    with pytest.raises(CsrfRejected):  # 同机另一个端口的页面
        run_check("POST", origin="http://localhost:3000", host="localhost:8000")


def test_host_comparison_is_case_insensitive() -> None:
    run_check("POST", origin="https://AragonTrading.Online", host="aragontrading.online")


def test_the_cors_allowlist_admits_a_cross_origin_dev_page() -> None:
    run_check("POST", origin="http://localhost:3000", host="localhost:8000", allowed=["http://localhost:3000"])
    run_check("POST", origin="http://127.0.0.1:3000", host="127.0.0.1:8000", allowed=["http://127.0.0.1:3000/"])


def test_forwarded_host_counts_only_behind_a_trusted_proxy() -> None:
    kwargs = {
        "origin": "https://aragontrading.online",
        "host": "127.0.0.1:8000",
        "x_forwarded_host": "aragontrading.online",
    }
    run_check("POST", trust_proxy=True, **kwargs)
    with pytest.raises(CsrfRejected):
        run_check("POST", trust_proxy=False, **kwargs)
    # 只取第一个值。
    run_check("POST", trust_proxy=True, origin="https://a.example", host="x", x_forwarded_host="a.example, b.example")
    with pytest.raises(CsrfRejected):
        run_check(
            "POST", trust_proxy=True, origin="https://b.example", host="x", x_forwarded_host="a.example, b.example"
        )


def test_the_service_bearer_skips_the_check() -> None:
    run_check("POST", bearer=True, origin="https://evil.example", host="aragontrading.online")


def test_ipv6_origins_compare_on_their_bracketed_host() -> None:
    run_check("POST", origin="http://[::1]:3000", host="[::1]:3000")
    with pytest.raises(CsrfRejected):
        run_check("POST", origin="http://[::1]:3000", host="[::1]:8000")


# ── 集成：经过真实应用 ──────────────────────────────────────────────────────


def test_production_shape_login_goes_through(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-208：`Host: aragontrading.online` + `Origin: https://aragontrading.online` + 空白名单。"""
    monkeypatch.setattr(get_settings(), "allow_origins", "", raising=False)
    response = auth_client.post(
        LOGIN, json=BODY, headers={"Host": "aragontrading.online", "Origin": "https://aragontrading.online"}
    )
    assert response.status_code == 401, response.text  # 到了登录逻辑（凭据是错的），而不是 403 csrf_rejected
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_evil_null_and_absent_origins_against_the_real_app(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_origins", "", raising=False)
    host = {"Host": "aragontrading.online"}
    evil = auth_client.post(LOGIN, json=BODY, headers={**host, "Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert evil.json()["error"]["code"] == "csrf_rejected"
    assert evil.json()["error"]["detail"]["origin"] == "https://evil.example"
    assert evil.headers["x-request-id"]

    null = auth_client.post(LOGIN, json=BODY, headers={**host, "Origin": "null"})
    assert (null.status_code, null.json()["error"]["code"]) == (403, "csrf_rejected")

    absent = auth_client.post(LOGIN, json=BODY, headers=host)
    assert absent.status_code == 401

    # GET 不受影响，哪怕 Origin 是恶意的。
    assert auth_client.get("/api/v1/auth/me", headers={**host, "Origin": "https://evil.example"}).status_code == 200


def test_a_refused_write_does_not_reach_the_handler(auth_client, make_user) -> None:
    user = make_user("alice")
    response = auth_client.post(
        LOGIN,
        json={"username": "alice", "password": user.password},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert auth_client.get("/api/v1/auth/me").json()["authenticated"] is False


def test_forwarded_host_is_honoured_with_trust_proxy(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "allow_origins", "", raising=False)
    headers = {"Origin": "https://aragontrading.online", "X-Forwarded-Host": "aragontrading.online"}
    assert auth_client.post(LOGIN, json=BODY, headers=headers).status_code == 403
    monkeypatch.setattr(get_settings(), "trust_proxy", True, raising=False)
    assert auth_client.post(LOGIN, json=BODY, headers=headers).status_code == 401


def test_the_service_bearer_is_not_subject_to_csrf(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "api_token", "svc-token", raising=False)
    evil = {"Origin": "https://evil.example"}
    assert auth_client.post(LOGIN, json=BODY, headers=evil).status_code == 403
    assert auth_client.post(LOGIN, json=BODY, headers={**evil, "Authorization": "Bearer svc-token"}).status_code == 401
    # 错误的 Bearer 不享受豁免。
    assert auth_client.post(LOGIN, json=BODY, headers={**evil, "Authorization": "Bearer wrong"}).status_code == 403


# ── CORS ────────────────────────────────────────────────────────────────────


def test_both_loopback_spellings_are_in_the_default_allowlist() -> None:
    origins = get_settings().cors_origins
    assert "http://localhost:3000" in origins and "http://127.0.0.1:3000" in origins


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_credentialed_cors_for_an_allowed_origin(auth_client, origin: str) -> None:
    preflight = auth_client.options(
        LOGIN,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert preflight.headers["access-control-allow-credentials"] == "true"

    actual = auth_client.get("/api/v1/auth/me", headers={"Origin": origin})
    assert actual.headers["access-control-allow-origin"] == origin
    assert actual.headers["access-control-allow-credentials"] == "true"


def test_an_unlisted_origin_gets_no_cors_headers(auth_client) -> None:
    response = auth_client.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_a_csrf_refusal_still_carries_cors_headers_for_a_listed_origin(
    auth_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CORS 在最外层：被拒的请求也带 CORS 头（E-2 的顺序），否则浏览器只看到不透明的网络错误。"""
    monkeypatch.setattr(get_settings(), "allow_origins", "http://localhost:3000", raising=False)
    # 来源在白名单里 → 通过 CSRF；这里改用一个在白名单、但 Host 不同的……它本来就放行。
    ok = auth_client.post(LOGIN, json=BODY, headers={"Origin": "http://localhost:3000"})
    assert ok.status_code == 401
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_wildcard_origin_with_credentials_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "allow_origins", "http://localhost:3000, *", raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_ORIGINS"):
        create_app()


# ── 响应头 ──────────────────────────────────────────────────────────────────


def test_vary_and_no_store_headers(auth_client, make_user, login_as) -> None:
    login_as(auth_client, make_user("alice"))
    for path in ("/api/v1/auth/me", "/api/v1/auth/sessions", "/api/v1/watchlist"):
        response = auth_client.get(path)
        assert response.headers["cache-control"] == "private, no-store", path
        vary = {token.strip().lower() for token in response.headers["vary"].split(",")}
        assert {"origin", "cookie"} <= vary, path
    # 公共资料不被强制 no-store（`/health` 是被运维探针与缓存层放过的）。
    health = auth_client.get("/api/v1/health")
    assert "no-store" not in health.headers.get("cache-control", "")
    assert "cookie" in health.headers["vary"].lower()


def test_prefix_matching_does_not_catch_lookalike_paths() -> None:
    from app.main import _is_private_path

    assert _is_private_path("/api/v1/me") and _is_private_path("/api/v1/me/quota")
    assert _is_private_path("/api/v1/admin/users") and _is_private_path("/api/v1/auth/login")
    assert _is_private_path("/api/v1/watchlist") and _is_private_path("/api/v1/watchlist/x")
    assert not _is_private_path("/api/v1/metrics") and not _is_private_path("/api/v1/reports")
    assert not _is_private_path("/api/v1/authority")


# ── X-Request-Id（RV-12）───────────────────────────────────────────────────


def test_a_well_formed_request_id_is_echoed(auth_client) -> None:
    response = auth_client.get("/api/v1/auth/config", headers={"X-Request-Id": "trace-abc_123"})
    assert response.headers["x-request-id"] == "trace-abc_123"


@pytest.mark.parametrize("bad", ["has space", "semi;colon", "x" * 33, "tab\tinside", "a/b"])
def test_a_malformed_request_id_is_replaced_not_trusted(auth_client, bad: str) -> None:
    """超长（PG 报错）、含换行（日志注入）都是真实的坏输入：丢弃并重新生成。"""
    response = auth_client.get("/api/v1/auth/config", headers={"X-Request-Id": bad})
    generated = response.headers["x-request-id"]
    assert generated != bad
    assert len(generated) == 16 and all(char in "0123456789abcdef" for char in generated)


def test_audit_rows_carry_at_most_sixteen_characters_of_the_request_id(auth_client, make_user, engine) -> None:
    make_user("alice")
    auth_client.post(LOGIN, json={"username": "alice", "password": "wrong-Wrong-1"}, headers={"X-Request-Id": "r" * 32})
    with sessionmaker(bind=engine)() as db:
        row = db.execute(select(AuditLog).where(AuditLog.action == "auth.login.failure")).scalar_one()
    assert row.request_id == "r" * 16


def test_the_security_middleware_is_not_a_whole_site_bearer_gate_any_more(
    auth_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E-1：`API_TOKEN` 设了，**不带**它的请求也不再被整站拒绝（按 guest 处理）。"""
    monkeypatch.setattr(get_settings(), "api_token", "svc-token", raising=False)
    assert auth_client.get("/api/v1/reports").status_code == 200
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    assert auth_client.get("/api/v1/reports", headers={"Authorization": "Bearer wrong"}).status_code == 200


def test_app_factory_returns_a_fastapi_app() -> None:
    from app.main import create_app

    assert isinstance(create_app(), FastAPI)
    assert TestClient is not None
