"""Object-level authorisation: pure functions, no HTTP, no database (spec §24.3.9, §24.3.14).

The route-level `enforce()` answers "may this *kind* of principal call this *kind* of
endpoint?". These answer the second question — "may this principal touch *this* report /
run / ask / user?" — and are called by the route (or service) **after** it has loaded the
object. They take plain attribute-bearing objects, so tests need no ORM.

`created_by / user_id IS NULL` means "historical or system data" and is **never**
"owned by whoever asks": only a principal with the `*.manage_any` / admin power may touch
it.
"""

from __future__ import annotations

from typing import Any, Literal

from ..errors import (
    AdminProtected,
    Forbidden,
    NotFound,
    ReportNotFound,
    RootProtected,
    SelfActionForbidden,
)
from .principal import Principal

UserAction = Literal[
    "view",
    "disable",
    "enable",
    "reset_password",
    "revoke_sessions",
    "quota",
    "unlock",
    "delete",
    "set_role",
    "update_profile",
]

#: Actions an actor may never aim at themselves (spec §24.3.14: "防自杀式误操作").
SELF_FORBIDDEN: frozenset[str] = frozenset({"disable", "delete", "set_role"})


def _owns(principal: Principal, owner_id: str | None) -> bool:
    return principal.user_id is not None and owner_id is not None and owner_id == principal.user_id


# ── reports / runs ───────────────────────────────────────────────────────────


def can_manage_report(principal: Principal, report: Any) -> bool:
    if principal.can("report.manage_any"):
        return True
    return principal.can("report.manage_own") and _owns(principal, getattr(report, "created_by", None))


def assert_can_manage_report(principal: Principal, report: Any) -> None:
    """Owner, or `report.manage_any`; `created_by IS NULL` is admin-only. Else 403."""
    if not can_manage_report(principal, report):
        raise Forbidden(permission="report.manage_any")


def can_view_report(principal: Principal, report: Any) -> bool:
    """A soft-deleted report is visible only to its owner and to admins (spec §24.3.9(c))."""
    if getattr(report, "deleted_at", None) is None:
        return True
    return can_manage_report(principal, report)


def assert_can_view_report(principal: Principal, report: Any) -> None:
    if not can_view_report(principal, report):
        raise ReportNotFound()


def assert_can_set_curation_flags(principal: Principal) -> None:
    """`pinned` / `setLatest` change what **every visitor** sees (RV-10): admin power only."""
    if not principal.can("report.manage_any"):
        raise Forbidden(permission="report.manage_any")


def can_manage_run(principal: Principal, run: Any) -> bool:
    if principal.can("report.manage_any"):
        return True
    return _owns(principal, getattr(run, "created_by", None))


def assert_can_manage_run(principal: Principal, run: Any) -> None:
    if not can_manage_run(principal, run):
        raise Forbidden(permission="report.manage_any")


# ── asks ─────────────────────────────────────────────────────────────────────


def can_access_ask(principal: Principal, ask: Any) -> bool:
    """Owner or admin. `user_id IS NULL` (historical) → admin only."""
    if principal.is_admin:
        return True
    return _owns(principal, getattr(ask, "user_id", None))


def assert_can_access_ask(principal: Principal, ask: Any) -> None:
    """**404, not 403**: the response must not reveal that the ask exists but is not yours."""
    if not can_access_ask(principal, ask):
        raise NotFound()


# ── users ────────────────────────────────────────────────────────────────────


def assert_can_manage_user(
    actor: Principal,
    target: Any,
    action: UserAction,
    *,
    new_role: str | None = None,
) -> None:
    """The admin/root matrix of §24.3.14.

    Order matters and is the specified one:

    1. a *service* principal never manages people (its permission set already says so; this
       is the second lock);
    2. acting on yourself — `disable / delete / set_role` → `422 self_action_forbidden`
       (this is checked **before** root protection, so root disabling root is the 422 the
       AC asks for, not a 403);
    3. the target is `root` → every write is `403 root_protected` (view is allowed);
    4. the target is `admin` → only root may write (`403 admin_protected`);
    5. `set_role`, or creating/promoting *to* `admin`, needs `admin.admins.manage` (root).
    """
    if actor.kind == "service" and not actor.open_mode and action != "view":
        raise Forbidden(permission="admin.users.manage")

    target_id = getattr(target, "id", None)
    target_role = getattr(target, "role", None)
    is_self = actor.user_id is not None and target_id == actor.user_id

    if is_self and action in SELF_FORBIDDEN:
        raise SelfActionForbidden(action=action)

    if action == "view":
        return

    if target_role == "root":
        raise RootProtected(action=action)

    if action == "set_role" or new_role == "admin":
        if not actor.can("admin.admins.manage"):
            raise AdminProtected(action=action)
        if new_role == "root":
            raise RootProtected(action=action)
        return

    if target_role == "admin" and not actor.can("admin.admins.manage"):
        raise AdminProtected(action=action)


def assert_can_self_delete(principal: Principal, user: Any) -> None:
    """Authorisation for `DELETE /auth/account` (spec §25.3.4, RV-Y2).

    **Not** `assert_can_manage_user(…, "delete")`: that matrix answers "may this admin delete
    that person", and for *yourself* it says `422 self_action_forbidden` by design (AC-197).
    Here the only questions are: is the caller a person (not the service token), and is it
    not the root account (`403 root_protected`, the existing code).
    """
    if principal.kind != "user":
        raise Forbidden(permission="account.self")
    if getattr(user, "role", None) == "root":
        raise RootProtected(action="self_delete")


def assert_can_create_user(actor: Principal, role: str) -> None:
    """Admins create `user`s; only root creates an `admin`; nobody creates a second root."""
    if role == "root":
        raise RootProtected(action="create")
    if role == "admin" and not actor.can("admin.admins.manage"):
        raise AdminProtected(action="create")
