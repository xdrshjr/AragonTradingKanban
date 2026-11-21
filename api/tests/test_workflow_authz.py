"""研究流程管理面的权限（AC-W1）：`admin.workflow.manage` 只属于 admin 与 root。

`user` → 403、`guest` → 401、窄化的 `service` → 403（流程影响全站产出，与 `admin.settings.manage`
同级，所以 `service` 的 `minus` 里有它）；`admin` 与 `root` 通过。穷举 `/admin/workflows*` 的**每一条**
策略表路由——新增路由漏测会在这里立刻现形。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.security.permissions import SERVICE_PERMISSIONS, expand_role
from app.security.policy import POLICIES
from app.services import user_service

PERMISSION = "admin.workflow.manage"
ROUTES = sorted(key for key in POLICIES if key[1].startswith("/api/v1/admin/workflows"))


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.fixture()
def clients(auth_client, make_user, login_as, engine, monkeypatch):  # noqa: ANN001
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "api_token", "service-token", raising=False)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with maker() as db:
        root = user_service.get_root(db)
        root.must_change_password = 0
        db.commit()
    out: dict[str, TestClient] = {"guest": TestClient(auth_client.app)}
    for role in ("user", "admin"):
        client = TestClient(auth_client.app)
        login_as(client, make_user(f"wf_{role}", role=role))
        out[role] = client
    root_client = TestClient(auth_client.app)
    response = root_client.post("/api/v1/auth/login", json={"username": "jdqqjr", "password": "13519126390"})
    assert response.status_code == 200, response.text
    out["root"] = root_client
    service = TestClient(auth_client.app)
    service.headers["Authorization"] = "Bearer service-token"
    out["service"] = service
    return out


def test_the_permission_belongs_to_admin_and_root_but_not_user_or_service() -> None:
    assert PERMISSION in expand_role("admin") and PERMISSION in expand_role("root")
    assert PERMISSION not in expand_role("user") and PERMISSION not in expand_role("guest")
    assert PERMISSION not in SERVICE_PERMISSIONS


def test_every_workflow_route_is_in_the_policy_table_with_the_permission() -> None:
    assert len(ROUTES) >= 20
    assert {POLICIES[key].permission for key in ROUTES} == {PERMISSION}


@pytest.mark.parametrize("key", ROUTES, ids=lambda key: f"{key[0]} {key[1]}")
def test_the_four_principals_against_every_route(clients, key) -> None:  # noqa: ANN001
    method, path = key
    url = _fill(path)
    guest = clients["guest"].request(method, url)
    assert guest.status_code == 401, (guest.status_code, guest.text)
    for principal in ("user", "service"):
        response = clients[principal].request(method, url)
        assert response.status_code == 403, (principal, response.status_code, response.text)
        assert response.json()["error"]["detail"]["permission"] == PERMISSION
    for principal in ("admin", "root"):
        response = clients[principal].request(method, url)
        assert response.status_code not in (401, 403), (principal, response.status_code, response.text)
