"""迭代 2 的遗留风险清零与交付卫生（spec §25.3.1c、§25.3.2；AC-212、AC-214、AC-215、AC-242）。

* A-18（D-10）：管理员创建的用户**无论**是否手填密码，首登都必须改密；
* F-26：`GET /admin/users?locked=1` 只返回处于锁定期的 `active` 账号，且不破坏 AC-195 的语句数上限；
* `X-Content-Type-Options: nosniff` 覆盖 401 / 404 / 422 / 500 / CSV；
* nginx 配置的 HSTS 静态断言：server 级、不含 includeSubDomains/preload、`/api/` 与 `/` 内无 `add_header`。
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.db.base import iso
from app.services import user_service

ADMIN = "/api/v1/admin"
AUTH = "/api/v1/auth"
ROOT_PASSWORD = "13519126390"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def maker(engine):  # noqa: ANN001, ANN201
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def admin_client(auth_client, make_user, login_as) -> TestClient:  # noqa: ANN001
    client = TestClient(auth_client.app)
    login_as(client, make_user("boss_admin", role="admin"))
    return client


def code_of(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


# ── A-18 / D-10（AC-214）───────────────────────────────────────────────────────


def test_an_admin_supplied_password_still_forces_a_change_on_first_login(admin_client, auth_client, maker) -> None:  # noqa: ANN001
    created = admin_client.post(f"{ADMIN}/users", json={"username": "handpicked", "password": "Correct-Horse-4x"})
    assert created.status_code == 201, created.text
    assert created.json()["temporaryPassword"] is None  # 手填的口令不回显
    with maker() as db:
        assert user_service.get_by_username(db, "handpicked").must_change_password == 1
    fresh = TestClient(auth_client.app)
    login = fresh.post(f"{AUTH}/login", json={"username": "handpicked", "password": "Correct-Horse-4x"})
    assert login.status_code == 200 and login.json()["user"]["mustChangePassword"] is True
    # 只能访问 /auth/me|password|logout；其余 403 password_change_required。
    assert fresh.get(f"{AUTH}/me").status_code == 200
    blocked = fresh.get("/api/v1/watchlist")
    assert blocked.status_code == 403 and code_of(blocked) == "password_change_required"
    changed = fresh.post(
        f"{AUTH}/password", json={"currentPassword": "Correct-Horse-4x", "newPassword": "Another-Horse-7y"}
    )
    assert changed.status_code in (200, 204), changed.text
    assert fresh.get("/api/v1/watchlist").status_code == 200


def test_a_generated_password_still_forces_a_change(admin_client, maker) -> None:  # noqa: ANN001
    assert admin_client.post(f"{ADMIN}/users", json={"username": "generated"}).status_code == 201
    with maker() as db:
        assert user_service.get_by_username(db, "generated").must_change_password == 1


# ── locked 过滤（AC-215）────────────────────────────────────────────────────────


def test_locked_filter_returns_only_active_users_inside_the_lock(admin_client, make_user, frozen_now) -> None:  # noqa: ANN001
    soon = iso(frozen_now.current + timedelta(minutes=10))
    past = iso(frozen_now.current - timedelta(minutes=10))
    make_user("locked_one", locked_until=soon)
    make_user("locked_two", locked_until=soon)
    make_user("lock_expired", locked_until=past)
    make_user("never_locked")
    make_user("locked_but_disabled", locked_until=soon, status="disabled")
    make_user("locked_admin", role="admin", locked_until=soon)

    body = admin_client.get(f"{ADMIN}/users", params={"locked": 1}).json()
    assert {item["username"] for item in body["items"]} == {"locked_one", "locked_two", "locked_admin"}
    assert body["total"] == 3 and all(item["locked"] for item in body["items"])
    # 与 q / role 组合正确。
    combined = admin_client.get(f"{ADMIN}/users", params={"locked": 1, "q": "locked_t"}).json()
    assert [item["username"] for item in combined["items"]] == ["locked_two"]
    only_users = admin_client.get(f"{ADMIN}/users", params={"locked": 1, "role": "user"}).json()
    assert {item["username"] for item in only_users["items"]} == {"locked_one", "locked_two"}
    # `status=disabled` 与 locked 矛盾 → 空；不带 locked 时行为不变。
    assert admin_client.get(f"{ADMIN}/users", params={"locked": 1, "status": "disabled"}).json()["items"] == []
    everyone = admin_client.get(f"{ADMIN}/users").json()
    assert {"never_locked", "lock_expired"} <= {item["username"] for item in everyone["items"]}


def test_the_locked_filter_keeps_the_statement_budget(admin_client, engine, make_user, frozen_now) -> None:  # noqa: ANN001
    """AC-215：语句数仍 ≤ 6，且与页大小无关。"""
    for i in range(30):
        make_user(f"lk{i:02d}", locked_until=iso(frozen_now.current + timedelta(hours=1)))

    def measure(limit: int) -> int:
        statements: list[str] = []

        def grab(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", grab)
        try:
            assert admin_client.get(f"{ADMIN}/users", params={"locked": 1, "limit": limit}).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", grab)
        return len([s for s in statements if not s.startswith(("PRAGMA", "BEGIN", "COMMIT", "ROLLBACK"))])

    small, large = measure(5), measure(50)
    assert small <= 6 and small == large, (small, large)


def test_the_locked_filter_pages_with_a_cursor(admin_client, make_user, frozen_now) -> None:  # noqa: ANN001
    for i in range(5):
        make_user(f"pg{i}", locked_until=iso(frozen_now.current + timedelta(hours=1)))
    first = admin_client.get(f"{ADMIN}/users", params={"locked": 1, "limit": 2}).json()
    assert len(first["items"]) == 2 and first["nextCursor"] and first["total"] == 5
    second = admin_client.get(f"{ADMIN}/users", params={"locked": 1, "limit": 2, "before": first["nextCursor"]}).json()
    assert {i["username"] for i in first["items"]}.isdisjoint({i["username"] for i in second["items"]})


# ── nosniff（AC-212）──────────────────────────────────────────────────────────


def test_every_kind_of_api_response_carries_nosniff(auth_client, admin_client) -> None:  # noqa: ANN001
    guest = TestClient(auth_client.app)
    responses = {
        "200": guest.get("/api/v1/health"),
        "401": guest.get(f"{ADMIN}/overview"),
        "404": guest.get("/api/v1/nope"),
        "422": guest.post(f"{AUTH}/login", json={}),
        "csv": admin_client.get(f"{ADMIN}/users/export.csv"),
    }
    assert [r.status_code for r in responses.values()] == [200, 401, 404, 422, 200]
    for name, response in responses.items():
        assert response.headers.get("x-content-type-options") == "nosniff", name


def test_a_500_carries_nosniff_from_the_unhandled_handler_itself(auth_client) -> None:  # noqa: ANN001
    """AC-212（RV-Y7）：500 由最外层 `ServerErrorMiddleware` 的处理器产生，用户中间件的头到不了它。"""
    app = auth_client.app

    async def boom() -> None:
        raise RuntimeError("kaboom")

    app.add_api_route("/__boom_for_test__", boom, methods=["GET"])
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/__boom_for_test__")
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/__boom_for_test__"]
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal"
    assert response.headers.get("x-content-type-options") == "nosniff"
    # 不是中间件加的：该路由不在 /api 下，但头依然来自处理器本身。
    assert "kaboom" not in response.text


# ── nginx（AC-242）────────────────────────────────────────────────────────────


NGINX = (REPO_ROOT / "deploy" / "nginx" / "aragon-trading.conf").read_text(encoding="utf-8").replace("\r\n", "\n")


def _block(text: str, header: str) -> str:
    """The `{ … }` body that follows `header` (brace-matched)."""
    start = text.index(header)
    open_at = text.index("{", start)
    depth = 0
    for index in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[open_at + 1 : index]
    raise AssertionError("unbalanced braces")


def _without_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_nginx_hsts_is_set_at_server_level_only() -> None:
    server = _block(NGINX, "server {")
    # server 级（不在任何 location 里）恰一条。
    server_level = re.sub(r"location[^{]*\{[^{}]*\}", "", _without_comments(server))
    hsts = re.findall(r'add_header\s+Strict-Transport-Security\s+"([^"]*)"\s+always;', server_level)
    assert hsts == ["max-age=15552000"]
    assert not re.search(r"Strict-Transport-Security[^\n;]*(includeSubDomains|preload)", _without_comments(NGINX))
    # /api/、/api/v1/reports 与 / 里没有 add_header（add_header 不继承：写了就会覆盖 server 级）。
    for header in ("location /api/ {", "location /api/v1/reports {", "location / {"):
        assert "add_header" not in _without_comments(_block(server, header)), header


def test_nginx_documents_why_and_leaves_the_static_locations_alone() -> None:
    comments = "\n".join(line for line in NGINX.splitlines() if line.strip().startswith("#"))
    assert "不继承" in comments and "certbot" in comments and "443" in comments
    server = _block(NGINX, "server {")
    static = _without_comments(_block(server, "location /_next/static/ {"))
    assert 'add_header Cache-Control "public, max-age=31536000, immutable";' in static
    assert "Strict-Transport-Security" not in static
    sw = _without_comments(_block(server, "location = /sw.js {"))
    assert 'add_header Cache-Control "no-cache, no-store, must-revalidate" always;' in sw
    assert 'add_header Service-Worker-Allowed "/" always;' in sw
    # 仓库里只有一个 server 块、且是 80（certbot 之后才有 443）。
    assert NGINX.count("server {") == 1 and "listen 80;" in NGINX
