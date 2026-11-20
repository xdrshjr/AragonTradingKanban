"""对象级授权纯函数（spec §24.3.9、§24.3.14；AC-176/177/178/179 的纯函数半边）。

路由怎么调用它们属于 M49（既有能力改造）/ M50（后台）；这里钉的是函数本身的判定表——
它们不碰 HTTP 也不碰数据库，所以用 `SimpleNamespace` 就够了。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import (
    AdminProtected,
    Forbidden,
    NotFound,
    ReportNotFound,
    RootProtected,
    SelfActionForbidden,
)
from app.security import objects
from app.security.principal import GUEST, SYSTEM, service_principal, user_principal


def person(role: str, uid: str | None = None):  # noqa: ANN201
    uid = uid or f"{role}-1"
    return user_principal(user_id=uid, username=uid, role=role, session_id="s", must_change_password=False)  # type: ignore[arg-type]


USER, OTHER, ADMIN, ROOT = person("user", "u1"), person("user", "u2"), person("admin", "a1"), person("root", "r1")
SERVICE = service_principal()


def report(created_by: str | None, deleted: bool = False):  # noqa: ANN201
    return SimpleNamespace(created_by=created_by, deleted_at="2026-11-01T00:00:00Z" if deleted else None)


# ── 报告 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("principal", "owner", "allowed"),
    [
        (USER, "u1", True),  # 拥有者
        (USER, "u2", False),  # 别人的
        (USER, None, False),  # 历史/系统数据：不是"谁来问就归谁"
        (ADMIN, "u1", True),
        (ADMIN, None, True),
        (ROOT, "u2", True),
        (SERVICE, "u1", True),  # service 走 manage_any
        (SERVICE, None, True),
        (GUEST, "u1", False),
        (GUEST, None, False),
    ],
)
def test_manage_report(principal, owner, allowed: bool) -> None:  # noqa: ANN001
    assert objects.can_manage_report(principal, report(owner)) is allowed
    if allowed:
        objects.assert_can_manage_report(principal, report(owner))
    else:
        with pytest.raises(Forbidden) as caught:
            objects.assert_can_manage_report(principal, report(owner))
        assert caught.value.status_code == 403 and caught.value.detail == {"permission": "report.manage_any"}


def test_a_soft_deleted_report_is_a_404_for_everyone_but_its_owner_and_admins() -> None:
    gone = report("u1", deleted=True)
    assert objects.can_view_report(USER, gone)
    assert objects.can_view_report(ADMIN, gone)
    assert not objects.can_view_report(OTHER, gone)
    assert not objects.can_view_report(GUEST, gone)
    with pytest.raises(ReportNotFound):
        objects.assert_can_view_report(OTHER, gone)
    assert objects.can_view_report(GUEST, report("u1")), "未删除的报告对所有人可读"


def test_pinned_and_set_latest_are_admin_power_even_on_your_own_report() -> None:
    """RV-10：这两个旗标改变**所有访客**看到的置顶与最新版本。"""
    objects.assert_can_set_curation_flags(ADMIN)
    objects.assert_can_set_curation_flags(SERVICE)
    for principal in (USER, GUEST):
        with pytest.raises(Forbidden) as caught:
            objects.assert_can_set_curation_flags(principal)
        assert caught.value.detail == {"permission": "report.manage_any"}


# ── run ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("principal", "owner", "allowed"),
    [
        (USER, "u1", True),
        (USER, "u2", False),
        (USER, None, False),
        (ADMIN, "u2", True),
        (SERVICE, None, True),
        (GUEST, None, False),
    ],
)
def test_manage_run(principal, owner, allowed: bool) -> None:  # noqa: ANN001
    run = SimpleNamespace(created_by=owner)
    assert objects.can_manage_run(principal, run) is allowed


# ── 追问：他人是 404，不是 403 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("principal", "owner", "allowed"),
    [
        (USER, "u1", True),
        (OTHER, "u1", False),
        (USER, None, False),
        (ADMIN, "u1", True),
        (ADMIN, None, True),
        (GUEST, "u1", False),
    ],
)
def test_ask_access(principal, owner, allowed: bool) -> None:  # noqa: ANN001
    ask = SimpleNamespace(user_id=owner)
    assert objects.can_access_ask(principal, ask) is allowed
    if not allowed:
        with pytest.raises(NotFound) as caught:
            objects.assert_can_access_ask(principal, ask)
        assert caught.value.status_code == 404, "不泄露“存在但不是你的”"


# ── 用户矩阵（§24.3.14）────────────────────────────────────────────────────


def target(role: str, uid: str = "t1"):  # noqa: ANN201
    return SimpleNamespace(id=uid, role=role)


ACTIONS_ON_OTHERS = ("disable", "enable", "reset_password", "revoke_sessions", "quota", "unlock", "delete")


@pytest.mark.parametrize("action", ACTIONS_ON_OTHERS)
def test_admin_may_manage_plain_users(action: str) -> None:
    objects.assert_can_manage_user(ADMIN, target("user"), action)  # type: ignore[arg-type]


@pytest.mark.parametrize("action", ACTIONS_ON_OTHERS)
def test_admin_may_not_write_to_another_admin(action: str) -> None:
    with pytest.raises(AdminProtected):
        objects.assert_can_manage_user(ADMIN, target("admin"), action)  # type: ignore[arg-type]


@pytest.mark.parametrize("actor", [ADMIN, ROOT, SERVICE])
def test_everyone_may_view_everyone(actor) -> None:  # noqa: ANN001
    for role in ("user", "admin", "root"):
        objects.assert_can_manage_user(actor, target(role), "view")


@pytest.mark.parametrize("action", [*ACTIONS_ON_OTHERS, "set_role"])
@pytest.mark.parametrize("actor", [ADMIN, SERVICE])
def test_nobody_but_root_itself_can_even_touch_root(actor, action: str) -> None:  # noqa: ANN001
    """AC-176：任何人对 root 的写操作一律 `root_protected`（service 先被自己的权限集拦成 forbidden）。"""
    expected = Forbidden if actor is SERVICE else RootProtected
    with pytest.raises(expected):
        objects.assert_can_manage_user(actor, target("root", "r1"), action)  # type: ignore[arg-type]


@pytest.mark.parametrize("action", ["disable", "delete", "set_role"])
def test_root_acting_on_root_is_a_self_action_422(action: str) -> None:
    """AC-176：root 对自己停用/删除/降级 → `422 self_action_forbidden`（先于 root 保护判定）。"""
    with pytest.raises(SelfActionForbidden) as caught:
        objects.assert_can_manage_user(ROOT, target("root", "r1"), action)  # type: ignore[arg-type]
    assert caught.value.status_code == 422 and caught.value.detail == {"action": action}


def test_an_admin_cannot_disable_or_delete_themselves() -> None:
    for action in ("disable", "delete"):
        with pytest.raises(SelfActionForbidden):
            objects.assert_can_manage_user(ADMIN, target("admin", "a1"), action)  # type: ignore[arg-type]


@pytest.mark.parametrize("action", ACTIONS_ON_OTHERS)
@pytest.mark.parametrize("role", ["user", "admin"])
def test_root_may_manage_users_and_admins(action: str, role: str) -> None:
    objects.assert_can_manage_user(ROOT, target(role), action)  # type: ignore[arg-type]


def test_only_root_may_promote_or_demote() -> None:
    """AC-177：任命/降级只有 root；admin 即便对 `user` 也不行。"""
    objects.assert_can_manage_user(ROOT, target("user"), "set_role", new_role="admin")
    objects.assert_can_manage_user(ROOT, target("admin"), "set_role", new_role="user")
    for actor in (ADMIN,):
        with pytest.raises(AdminProtected):
            objects.assert_can_manage_user(actor, target("user"), "set_role", new_role="admin")
        with pytest.raises(AdminProtected):
            objects.assert_can_manage_user(actor, target("admin"), "set_role", new_role="user")


def test_nobody_can_mint_a_second_root_by_promotion() -> None:
    with pytest.raises(RootProtected):
        objects.assert_can_manage_user(ROOT, target("user"), "set_role", new_role="root")


def test_creating_accounts() -> None:
    objects.assert_can_create_user(ADMIN, "user")
    objects.assert_can_create_user(ROOT, "user")
    objects.assert_can_create_user(ROOT, "admin")
    with pytest.raises(AdminProtected):
        objects.assert_can_create_user(ADMIN, "admin")
    for actor in (ADMIN, ROOT):
        with pytest.raises(RootProtected):
            objects.assert_can_create_user(actor, "root")


def test_open_mode_service_is_not_second_guessed_for_user_management() -> None:
    open_service = service_principal(open_mode=True)
    objects.assert_can_manage_user(open_service, target("user"), "disable")  # type: ignore[arg-type]


def test_system_principal_has_no_user_id_so_it_can_never_be_a_self_action() -> None:
    objects.assert_can_manage_user(SYSTEM, target("user"), "unlock")  # type: ignore[arg-type]
