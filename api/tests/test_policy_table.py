"""默认拒绝的策略表（spec §24.3.3、附录 A；AC-173、AC-174）。

三层断言：

1. **启动期集合校验**：构造一个漏登记的路由 → `RuntimeError`；反向（表里多出来的键）与
   `PENDING_ROUTES` 的"只许缩小"规则同样会炸。豁免规则是"不在 `/api/v1` 前缀下"，不是手写清单（RV-4）。
2. **对每个真实路由 × 5 个主体**（guest / user / admin / root / service）各打一次，
   状态码必须与表一致：无权限 → guest 401、已登录 403；有权限 → **不是** 401/403。
3. **对尚未实现的 `/me/*`、`/admin/*`（`PENDING_ROUTES`）**：真实应用里它们是 404，没有闸门可测，
   所以把它们挂到一个只含 `enforce` 的**桩应用**上，用同一套真实的主体解析与同一张策略表打一遍——
   M48/M50 落地时，它们从这里自动移到第 2 层。

世界（一个 enforced 应用 + 4 个已登录客户端）按**模块**建一次：180+ 个用例每个都建库太慢。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.security import deps, policy
from app.security.permissions import PASSWORD_CHANGE_ALLOWED, expand_role, permission_satisfied
from app.security.policy import PENDING_ROUTES, POLICIES, verify_against_app

PRINCIPALS = ("guest", "user", "admin", "root", "service")
LOGOUT = ("POST", "/api/v1/auth/logout")
REAL_KEYS = sorted(key for key in POLICIES if key not in PENDING_ROUTES and key != LOGOUT)
PENDING_KEYS = sorted(PENDING_ROUTES)


def _label(key: tuple[str, str]) -> str:
    if not isinstance(key, tuple):  # an empty PENDING_KEYS parametrisation hands over a NOTSET sentinel
        return "none"
    return f"{key[0]} {key[1]}"


def _held(principal: str) -> frozenset[str]:
    return expand_role("service" if principal == "service" else principal)


def expected_gate(principal: str, key: tuple[str, str]) -> str:
    """`allow | 401 | 403` — computed from the vocabulary, independently of `enforce()`."""
    permission = POLICIES[key].permission
    if permission == "public" or permission_satisfied(_held(principal), permission):
        return "allow"
    return "401" if principal == "guest" else "403"


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


# ── 世界 ────────────────────────────────────────────────────────────────────


@dataclass
class World:
    clients: dict[str, TestClient]
    sids: dict[str, str]
    app: FastAPI
    forced: TestClient


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    from conftest import DEFAULT_TEST_PASSWORD, _wire_app

    from app.config import get_settings
    from app.db.base import Base
    from app.db.session import build_engine
    from app.models import User
    from app.security.passwords import hash_password
    from app.services import user_service
    from app.services.search_index import ensure_fts_schema

    mp = pytest.MonkeyPatch()
    db_file = tmp_path_factory.mktemp("policy") / "policy.db"
    engine = build_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        ensure_fts_schema(connection)

    settings = get_settings()
    mp.setattr(settings, "auth_mode", "enforced", raising=False)
    mp.setattr(settings, "api_token", "service-token", raising=False)
    main_module = _wire_app(engine, mp)

    from app.services import run_manager as run_manager_module

    run_manager_module.reset_manager()
    with TestClient(main_module.app) as lifespan_client:  # runs the lifespan → bootstraps root
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        password_hash = hash_password(DEFAULT_TEST_PASSWORD)
        with factory() as db:
            root = user_service.get_root(db)
            assert root is not None
            root.password_hash, root.must_change_password, root.password_is_default = password_hash, 0, 0
            for name, role, must_change in (
                ("policy_user", "user", False),
                ("policy_admin", "admin", False),
                ("policy_forced", "user", True),
            ):
                user_service.create_user(
                    db, username=name, password_hash=password_hash, role=role, must_change_password=must_change
                )
            db.commit()
            assert db.query(User).count() == 4

        clients: dict[str, TestClient] = {"guest": TestClient(main_module.app)}
        sids: dict[str, str] = {}
        logins = {"user": "policy_user", "admin": "policy_admin", "root": "jdqqjr"}
        for principal, username in logins.items():
            client = TestClient(main_module.app)
            response = client.post("/api/v1/auth/login", json={"username": username, "password": DEFAULT_TEST_PASSWORD})
            assert response.status_code == 200, response.text
            clients[principal] = client
            sids[principal] = client.cookies.get("aragon_sid")
        service = TestClient(main_module.app)
        service.headers["Authorization"] = "Bearer service-token"
        clients["service"] = service

        forced = TestClient(main_module.app)
        assert (
            forced.post(
                "/api/v1/auth/login", json={"username": "policy_forced", "password": DEFAULT_TEST_PASSWORD}
            ).status_code
            == 200
        )
        sids["forced"] = forced.cookies.get("aragon_sid")
        assert lifespan_client is not None
        yield World(clients=clients, sids=sids, app=main_module.app, forced=forced)
    run_manager_module.reset_manager()
    mp.undo()
    engine.dispose()


def _stub_app() -> FastAPI:
    """An app whose only content is the not-yet-built routes, each behind the real `enforce`."""
    from app.errors import register_exception_handlers

    stub = FastAPI()
    register_exception_handlers(stub)

    def ok() -> dict[str, bool]:
        return {"ok": True}

    for method, path in PENDING_KEYS:
        stub.add_api_route(path, ok, methods=[method], dependencies=[Depends(deps.enforce)])
    return stub


@pytest.fixture(scope="module")
def stub_clients(world: World) -> dict[str, TestClient]:
    stub = _stub_app()
    clients: dict[str, TestClient] = {}
    for principal in PRINCIPALS:
        client = TestClient(stub)
        if principal == "service":
            client.headers["Authorization"] = "Bearer service-token"
        elif principal in world.sids:
            client.cookies.set("aragon_sid", world.sids[principal])
        clients[principal] = client
    return clients


# ── 第 2 层：真实路由 × 主体 ─────────────────────────────────────────────────


@pytest.mark.parametrize("principal", PRINCIPALS)
@pytest.mark.parametrize("key", REAL_KEYS, ids=_label)
def test_real_route_gate_matches_the_table(world: World, key: tuple[str, str], principal: str) -> None:
    method, path = key
    response = world.clients[principal].request(method, _fill(path))
    verdict = expected_gate(principal, key)
    if verdict == "allow":
        assert response.status_code not in (401, 403), (response.status_code, response.text)
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
            assert not (isinstance(body, dict) and body.get("error", {}).get("code") == "policy_missing")
    elif verdict == "401":
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "unauthorized"
        assert response.json()["error"]["detail"]["reason"] == "no_session"
    else:
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "forbidden"
        assert error["detail"]["permission"] == POLICIES[key].permission


# ── 第 3 层：尚未实现的路由（桩应用 + 真实 enforce）───────────────────────────


@pytest.mark.parametrize("principal", PRINCIPALS)
@pytest.mark.parametrize("key", PENDING_KEYS, ids=_label)
def test_pending_route_gate_matches_the_table(
    stub_clients: dict[str, TestClient], key: tuple[str, str], principal: str
) -> None:
    method, path = key
    response = stub_clients[principal].request(method, _fill(path))
    verdict = expected_gate(principal, key)
    expected_status = {"allow": 200, "401": 401, "403": 403}[verdict]
    assert response.status_code == expected_status, (response.status_code, response.text)


def test_pending_routes_are_only_the_me_and_admin_families() -> None:
    """M48 删 `/me/*`，M50 删 `/admin/*`；集合只许缩小，且只能是这两族。

    附录 A 的完整性用**不随实现进度变化**的总数钉住：45 条既有业务路由 + 11 条 `/auth` +
    4 条 `/me` + 22 条 `/admin`（含 B-19 的逐条吊销会话）= 82。路由从 PENDING 移到"真实存在"时总数不变，
    而误删表里一行会立刻红。
    """
    assert {path.split("/")[3] for _method, path in PENDING_ROUTES} <= {"me", "admin"}
    assert all(key in POLICIES for key in PENDING_ROUTES)
    # §25：+3 条 /auth（recover / recovery-key / account）+3 条 /admin（两个导出 + bulk-quota）= 88。
    assert len(POLICIES) == 88
    families = {"me": 4, "admin": 25, "auth": 14}
    for family, expected in families.items():
        assert len([key for key in POLICIES if key[1].startswith(f"/api/v1/{family}/")]) == expected, family


def test_the_matrix_says_what_the_spec_says() -> None:
    """AC-174 的几个锚点，直接断言词表而不是循环里的推导。"""
    assert expected_gate("guest", ("POST", "/api/v1/instruments/{instrument_id}/reports")) == "401"
    assert expected_gate("user", ("POST", "/api/v1/instruments/{instrument_id}/reports")) == "allow"
    assert expected_gate("user", ("GET", "/api/v1/cost/summary")) == "403"
    assert expected_gate("admin", ("POST", "/api/v1/cost/budget")) == "403", "改月度预算只给 root"
    assert expected_gate("root", ("POST", "/api/v1/cost/budget")) == "allow"
    assert expected_gate("guest", ("GET", "/api/v1/reports")) == "allow"
    assert expected_gate("user", ("GET", "/api/v1/admin/users")) == "403"
    # service 可以管精选/看审计，但不能碰人、钱、门（RV-1 / RV-17）。
    assert expected_gate("service", ("GET", "/api/v1/admin/audit")) == "allow"
    assert expected_gate("service", ("POST", "/api/v1/admin/featured-watchlist")) == "allow"
    assert expected_gate("service", ("POST", "/api/v1/admin/users")) == "403"
    assert expected_gate("service", ("PATCH", "/api/v1/admin/settings")) == "403"
    assert expected_gate("service", ("POST", "/api/v1/admin/invites")) == "403"
    assert expected_gate("service", ("POST", "/api/v1/admin/quota-requests/{request_id}/resolve")) == "403"
    assert expected_gate("service", ("POST", "/api/v1/cost/budget")) == "403"
    # 路由级 `report.manage_own`：service 靠 `report.manage_any` 通过（见 permissions.IMPLIED_BY）。
    assert expected_gate("service", ("PATCH", "/api/v1/reports/{report_id}")) == "allow"


def test_guest_gets_401_and_user_gets_403_never_404(world: World) -> None:
    """AC-174：user 对 admin 路由得 403（不是 401，也不是 404）。"""
    guest = world.clients["guest"].get("/api/v1/cost/summary")
    user = world.clients["user"].get("/api/v1/cost/summary")
    assert (guest.status_code, user.status_code) == (401, 403)
    assert user.json()["error"]["code"] == "forbidden"


def test_logout_is_open_to_every_principal(world: World) -> None:
    """`POST /auth/logout` 被排除在参数化之外（它会杀掉共享会话）——这里单独证明它对任何人都是 204。"""
    assert world.clients["guest"].post("/api/v1/auth/logout").status_code == 204
    assert world.clients["service"].post("/api/v1/auth/logout").status_code == 204
    assert TestClient(world.app).post("/api/v1/auth/logout").status_code == 204


# ── 强制改密态 ──────────────────────────────────────────────────────────────


def test_must_change_password_confines_the_session_to_three_routes(world: World) -> None:
    forced = world.forced
    assert forced.get("/api/v1/auth/me").status_code == 200
    assert forced.get("/api/v1/auth/me").json()["user"]["mustChangePassword"] is True
    for method, path in (("GET", "/api/v1/reports"), ("GET", "/api/v1/watchlist"), ("GET", "/api/v1/auth/sessions")):
        response = forced.request(method, path)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "password_change_required"
    # `/auth/password` 是被放行的写接口之一（此处缺 body → 422，而不是 403）。
    assert forced.post("/api/v1/auth/password", json={}).status_code == 422
    assert {
        ("POST", "/api/v1/auth/password"),
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/logout"),
    } == PASSWORD_CHANGE_ALLOWED


# ── 第 1 层：启动期集合校验 ─────────────────────────────────────────────────


def test_the_real_app_passes_its_own_startup_check(world: World) -> None:
    verify_against_app(world.app)


def test_an_unregistered_route_stops_the_process() -> None:
    from app.main import create_app

    app = create_app()
    app.add_api_route("/api/v1/rogue", lambda: {}, methods=["GET"])
    with pytest.raises(RuntimeError, match=r"GET /api/v1/rogue"):
        verify_against_app(app)


def test_a_dangling_policy_key_stops_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(POLICIES, ("GET", "/api/v1/ghost"), policy.Policy("public"))
    with pytest.raises(RuntimeError, match="策略表里有真实不存在的路由.*ghost"):
        verify_against_app(_fresh_app())


def test_a_stale_pending_entry_stops_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由实现了却忘了从 `PENDING_ROUTES` 删掉——豁免不许比理由活得久。"""
    monkeypatch.setattr(policy, "PENDING_ROUTES", PENDING_ROUTES | {("GET", "/api/v1/health")})
    with pytest.raises(RuntimeError, match="PENDING_ROUTES 里的路由已经实现.*health"):
        verify_against_app(_fresh_app())


def _fresh_app() -> FastAPI:
    from app.main import create_app

    return create_app()


def test_the_exemption_is_a_prefix_rule_not_a_hand_written_list() -> None:
    """RV-4：非 `/api/v1` 前缀的路由一律豁免——哪怕是明天 FastAPI 新增的文档路由。"""
    app = _fresh_app()
    for extra in ("/docs/oauth2-redirect", "/some-future-docs-route", "/healthz"):
        app.add_api_route(extra, lambda: {}, methods=["GET"])
    verify_against_app(app)
    root_level = {route.path for route in app.routes if not route.path.startswith("/api/v1")}
    assert {"/", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"} <= root_level


def test_keys_carry_the_api_prefix_and_use_the_real_parameter_names() -> None:
    assert all(path.startswith("/api/v1/") for _method, path in POLICIES)
    assert ("GET", "/api/v1/reports/{report_id}") in POLICIES
    assert ("GET", "/api/v1/asks/{ask_id}/events") in POLICIES
    assert not any("{id}" in path for _method, path in POLICIES), "附录 A 的 {id} 是缩写，真实模板用各路由自己的名字"


def test_an_unregistered_route_behind_enforce_is_500_not_open(world: World) -> None:
    """默认拒绝：就算有人绕过了启动校验，未登记的路由得到的是 500，不是放行。"""
    from app.errors import register_exception_handlers

    stub = FastAPI()
    register_exception_handlers(stub)
    stub.add_api_route("/api/v1/unlisted", lambda: {"ok": True}, methods=["GET"], dependencies=[Depends(deps.enforce)])
    response = TestClient(stub).get("/api/v1/unlisted")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "policy_missing"
    assert response.json()["error"]["detail"]["route"] == "GET /api/v1/unlisted"


# ── e2e 钩子默认不存在 ───────────────────────────────────────────────────────


def test_the_captcha_answer_hook_is_a_404_by_default(world: World) -> None:
    response = world.clients["guest"].get("/api/v1/auth/_test/captcha-answer", params={"challengeId": "x"})
    assert response.status_code == 404
    assert "_test" not in " ".join(path for _method, path in POLICIES)


def test_the_hook_is_mounted_only_with_auth_test_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.services.captcha import get_captcha_provider

    monkeypatch.setattr(get_settings(), "auth_test_hooks", True, raising=False)
    app = _fresh_app()  # `/auth/_test/` is exempt from the table, so startup still passes
    issued = get_captcha_provider().issue("register", "203.0.113.5")
    expected = get_captcha_provider().debug_answer(issued.challenge_id)
    response = TestClient(app).get("/api/v1/auth/_test/captcha-answer", params={"challengeId": issued.challenge_id})
    assert response.status_code == 200 and response.json() == {"answer": expected}
