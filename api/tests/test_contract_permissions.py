"""`contracts/permissions.json` 的契约（spec §24.3.4；AC-175）。

JSON 是角色→权限的**单一真源**；Python 侧（`security/permissions.py`）与 TS 侧
（`web/lib/permissions.ts` + `web/tests/permissions.test.ts`）各自展开。这里钉 Python 一侧：
展开结果与 JSON 逐角色相等（用**另一份独立写的展开代码**对拍，不是调用被测函数再比自己），
`policy.py` 里用到的每个权限串都在词表里（防拼写漂移，同 E-12），以及严格包含链。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.security import policy
from app.security.permissions import (
    ALL_PERMISSIONS,
    CONTRACT_PATH,
    GUEST_PERMISSIONS,
    IMPLIED_BY,
    SERVICE_PERMISSIONS,
    expand_role,
    permission_satisfied,
)

RAW = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "permissions.json").read_text(encoding="utf-8"))


def independent_expand(role: str) -> set[str]:
    """A second, deliberately naive implementation (iterate to a fixpoint)."""
    out: set[str] = set()
    pending = list(RAW["roles"][role])
    while pending:
        item = pending.pop()
        if item.startswith("@"):
            pending.extend(RAW["roles"][item[1:]])
        else:
            out.add(item)
    return out


def test_the_contract_file_is_where_python_reads_it() -> None:
    assert Path(__file__).resolve().parents[2] / "contracts" / "permissions.json" == CONTRACT_PATH
    assert RAW["version"] == 1
    assert set(RAW["roles"]) == {"guest", "user", "admin", "root"}


def test_python_expansion_equals_the_json_for_every_role() -> None:
    for role in RAW["roles"]:
        assert expand_role(role) == frozenset(independent_expand(role)), role
    expected_service = independent_expand(RAW["service"]["inherits"]) - set(RAW["service"]["minus"])
    assert expand_role("service") == frozenset(expected_service)
    assert frozenset(expected_service) == SERVICE_PERMISSIONS
    assert frozenset(independent_expand("guest")) == GUEST_PERMISSIONS


def test_roles_nest_strictly_root_over_admin_over_user_over_guest() -> None:
    guest, user, admin, root = (expand_role(role) for role in ("guest", "user", "admin", "root"))
    assert guest < user < admin < root, "严格真包含"
    assert root - admin == {"admin.admins.manage", "cost.manage"}


def test_service_is_admin_minus_everything_that_changes_people_money_or_doors() -> None:
    """RV-1 / RV-17：令牌泄露的最坏后果是触发生成/改精选/读审计，不是接管账号体系。"""
    admin, service = expand_role("admin"), expand_role("service")
    assert service < admin
    assert admin - service == set(RAW["service"]["minus"]) & admin
    for forbidden in (
        "admin.users.manage",
        "admin.quota.manage",
        "admin.invites.manage",
        "admin.settings.manage",
        "admin.admins.manage",
        "cost.manage",
        "account.self",
        "quota.request",
        "watchlist.own",
        "report.manage_own",
    ):
        assert forbidden not in service
    for kept in (
        "report.generate",
        "report.manage_any",
        "schedule.manage",
        "cost.read",
        "engine.probe",
        "admin.overview.read",
        "admin.users.read",
        "admin.audit.read",
        "admin.featured.manage",
    ):
        assert kept in service


def test_every_permission_the_policy_table_uses_is_in_the_vocabulary() -> None:
    used = {entry.permission for entry in policy.POLICIES.values()} - {"public"}
    assert used <= ALL_PERMISSIONS, f"策略表里有词表里没有的权限串：{sorted(used - ALL_PERMISSIONS)}"


def test_every_vocabulary_entry_is_used_by_some_route_or_is_a_known_capability_flag() -> None:
    """词表里不该有"没人用的权限"：它们要么挂在某条路由上，要么是前端/对象级专用的旗标。"""
    used = {entry.permission for entry in policy.POLICIES.values()}
    # 前端按它们裁剪界面（`can()`），或由路由内的对象级/二次判定使用，而不是路由级闸门。
    capability_flags = {
        "report.read",
        "market.read",
        "ask.deep",  # 路由内对 mode=deep 的二次判定
        "report.manage_any",  # 对象级 + PATCH 的 pinned/setLatest
        "admin.admins.manage",  # 对象级：`assert_can_manage_user`（任命/降级/动 admin）
    }
    assert ALL_PERMISSIONS - used <= capability_flags, sorted(ALL_PERMISSIONS - used - capability_flags)


def test_open_mode_principal_holds_the_whole_vocabulary_unlike_the_api_token_service() -> None:
    from app.security.principal import service_principal

    open_service = service_principal(open_mode=True)
    token_service = service_principal()
    assert open_service.permissions == ALL_PERMISSIONS
    assert token_service.permissions == SERVICE_PERMISSIONS != ALL_PERMISSIONS
    assert open_service.can("admin.users.manage") and not token_service.can("admin.users.manage")


def test_manage_any_implies_manage_own_for_the_route_gate() -> None:
    """spec 缺陷修正：service 有 `manage_any`、无 `manage_own`，而 PATCH/DELETE 报告的路由级权限是后者。"""
    assert {"report.manage_own": frozenset({"report.manage_any"})} == IMPLIED_BY
    assert "report.manage_own" not in SERVICE_PERMISSIONS
    assert permission_satisfied(SERVICE_PERMISSIONS, "report.manage_own")
    assert not permission_satisfied(GUEST_PERMISSIONS, "report.manage_own")
    assert not permission_satisfied(expand_role("user") - {"report.manage_own"}, "report.manage_own")


def test_the_typescript_side_reads_the_same_file() -> None:
    """TS 侧的逐角色相等断言在 `web/tests/permissions.test.ts`；这里只确认它指向同一个文件。"""
    ts = Path(__file__).resolve().parents[2] / "web" / "lib" / "permissions.ts"
    if not ts.exists():  # 前端尚未落地（并行开发中）
        return
    source = ts.read_text(encoding="utf-8")
    assert "permissions.json" in source
