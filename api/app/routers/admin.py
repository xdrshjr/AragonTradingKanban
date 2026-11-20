"""`/admin` — the management back-office API (spec §24.5.3, §24.3.14, Appendix A).

One file, one router, split by resource below (never "a router that imports a router").

**Where the guards live.** The route-level permission is the policy table's job
(`security/policy.py`, enforced by `deps.enforce` before any handler runs): a `user` gets 403,
a `guest` 401, the narrowed `service` principal 403 on everything that touches people, money or
the front door. Who may act on *which user* is `security/objects.py::assert_can_manage_user`,
called by the services in `admin_user_service` — the handlers here stay thin: parse, call,
commit, shape the response. The front end hides buttons the matrix forbids, but that is
courtesy; this is the control.

**Secrets.** A temporary password appears in exactly two responses (create-user without a
password, reset-password) and is never stored, logged or audited in clear. Every `/admin`
response carries `Cache-Control: private, no-store` (set centrally in `main.py`).

Handlers that hash a password are plain `def`, so they run in the thread pool (a scrypt is
60–100 ms of CPU and must not sit on the event loop).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import iso, parse_iso
from ..db.session import get_db
from ..errors import InvalidQuery, NotFound
from ..models import InviteCode, QuotaRequest, Run, User
from ..schemas.admin import (
    AdminUserDetail,
    AdminUsersOut,
    AuditOut,
    BulkQuotaOut,
    BulkQuotaRequest,
    CreateUserOut,
    CreateUserRequest,
    DeleteUserBody,
    FeaturedAddRequest,
    InviteCreateRequest,
    InviteOut,
    InvitesOut,
    OverviewOut,
    QuotaRequestRow,
    QuotaRequestsOut,
    ResolveQuotaRequest,
    RevokedOut,
    SettingsOut,
    TemporaryPasswordOut,
    UserPatch,
    UserQuotaRequest,
)
from ..security import clock, deps
from ..security.principal import Principal
from ..services import (
    admin_overview,
    admin_user_service,
    audit_service,
    export_service,
    featured_service,
    invite_service,
    quota_requests,
    settings_service,
)
from ..services.audit_service import AuditTarget

router = APIRouter(prefix="/admin", tags=["admin"])

PrincipalDep = Annotated[Principal, Depends(deps.get_principal)]
DbDep = Annotated[Session, Depends(get_db)]


# ── overview ─────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=OverviewOut)
def get_overview(db: DbDep) -> dict[str, Any]:
    return admin_overview.overview(db)


# ── users ────────────────────────────────────────────────────────────────────


@router.get("/users", response_model=AdminUsersOut)
def list_users(
    db: DbDep,
    q: str | None = Query(default=None, max_length=64),
    role: Literal["root", "admin", "user"] | None = None,
    status: Literal["active", "disabled", "deleted"] | None = None,
    locked: bool = Query(default=False, description="只看处于锁定期的 active 账号（F-26）"),
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    items, total, next_cursor = admin_user_service.list_users(
        db, q=q, role=role, status=status, locked=locked, limit=limit, before=before
    )
    return {"items": items, "total": total, "nextCursor": next_cursor}


# Static sub-paths of `/users` MUST be registered before `/users/{user_id}`: routes match in
# registration order, so "export.csv" would otherwise be read as a user id.


@router.get("/users/export.csv", response_class=StreamingResponse)
def export_users(
    db: DbDep,
    principal: PrincipalDep,
    q: str | None = Query(default=None, max_length=64),
    role: Literal["root", "admin", "user"] | None = None,
    status: Literal["active", "disabled", "deleted"] | None = None,
    locked: bool = Query(default=False),
) -> StreamingResponse:
    """CSV of the user list under the **same filters** (§25.3.5a). Rate-limited and audited
    (`admin.export`) before the first byte; at most 50 000 rows (`X-Export-Truncated: 1`)."""
    return export_service.users_csv(db, principal, q=q, role=role, status=status, locked=locked)


@router.post("/users/bulk-quota", response_model=BulkQuotaOut)
def bulk_quota(payload: BulkQuotaRequest, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    """Grant / take back bonus credits for up to 100 users, all-or-nothing (§25.3.5b)."""
    result = admin_user_service.bulk_grant(
        db, principal, user_ids=payload.userIds, grant=payload.grant, note=payload.note
    )
    db.commit()
    return result


@router.post("/users", response_model=CreateUserOut, status_code=201)
def create_user(payload: CreateUserRequest, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    user, temporary = admin_user_service.create_user(
        db,
        principal,
        username=payload.username,
        role=payload.role,
        password=payload.password,
        display_name=payload.displayName,
        bonus_credits=payload.bonusCredits,
    )
    db.commit()
    now = clock.now()
    quotas = admin_user_service.quota_lite(db, [user], now)
    return {
        "user": admin_user_service.user_dict(user, quotas.get(user.id), None, now),
        "temporaryPassword": temporary,
    }


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: str, db: DbDep) -> dict[str, Any]:
    return admin_user_service.detail(db, admin_user_service.get_or_404(db, user_id))


@router.patch("/users/{user_id}", response_model=AdminUserDetail)
def patch_user(user_id: str, payload: UserPatch, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    target = admin_user_service.get_or_404(db, user_id)
    admin_user_service.patch_user(
        db,
        principal,
        target,
        fields=set(payload.model_fields_set),
        display_name=payload.displayName,
        role=payload.role,
        status=payload.status,
        disabled_reason=payload.disabledReason,
    )
    db.commit()
    return admin_user_service.detail(db, target)


@router.post("/users/{user_id}/reset-password", response_model=TemporaryPasswordOut)
def reset_password(user_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, str]:
    target = admin_user_service.get_or_404(db, user_id)
    temporary = admin_user_service.reset_password(db, principal, target)
    db.commit()
    return {"temporaryPassword": temporary}


@router.post("/users/{user_id}/revoke-sessions", response_model=RevokedOut)
def revoke_sessions(user_id: str, db: DbDep, principal: PrincipalDep) -> dict[str, int]:
    target = admin_user_service.get_or_404(db, user_id)
    revoked = admin_user_service.revoke_sessions(db, principal, target)
    db.commit()
    return {"revoked": revoked}


@router.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
def revoke_one_session(user_id: str, session_id: str, db: DbDep, principal: PrincipalDep) -> Response:
    """Kick a single device of a user (B-19); the session must belong to that user."""
    target = admin_user_service.get_or_404(db, user_id)
    admin_user_service.revoke_one_session(db, principal, target, session_id)
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/unlock", status_code=204)
def unlock_user(user_id: str, db: DbDep, principal: PrincipalDep) -> Response:
    target = admin_user_service.get_or_404(db, user_id)
    admin_user_service.unlock(db, principal, target)
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/quota")
def adjust_quota(user_id: str, payload: UserQuotaRequest, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    target = admin_user_service.get_or_404(db, user_id)
    status = admin_user_service.set_quota(
        db,
        principal,
        target,
        resource=payload.resource,
        grant=payload.grant,
        daily_limit_set="dailyLimit" in payload.model_fields_set,
        daily_limit=payload.dailyLimit,
        note=payload.note,
    )
    db.commit()
    return status


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    db: DbDep,
    principal: PrincipalDep,
    confirm: str | None = Query(default=None, max_length=64),
    body: DeleteUserBody | None = Body(default=None),
) -> Response:
    """Delete = anonymise. `confirm` (query or body) must equal the target's username."""
    target = admin_user_service.get_or_404(db, user_id)
    admin_user_service.delete_user(db, principal, target, confirm=confirm or (body.confirm if body else None))
    db.commit()
    return Response(status_code=204)


# ── invites ──────────────────────────────────────────────────────────────────


def _invite_out(db: Session, invite: InviteCode, creators: dict[str, str]) -> dict[str, Any]:
    return {
        "id": invite.id,
        "code": invite.code,
        "note": invite.note,
        "maxUses": invite.max_uses,
        "usedCount": invite.used_count,
        "bonusCredits": invite.bonus_credits,
        "expiresAt": invite.expires_at,
        "disabledAt": invite.disabled_at,
        "createdAt": invite.created_at,
        "createdBy": creators.get(invite.created_by or ""),
        "status": invite_service.status_of(invite),
    }


def _creator_names(db: Session, invites: list[InviteCode]) -> dict[str, str]:
    ids = {invite.created_by for invite in invites if invite.created_by}
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.username).where(User.id.in_(ids))).all()
    return {user_id: username for user_id, username in rows}


@router.get("/invites", response_model=InvitesOut)
def list_invites(db: DbDep) -> dict[str, Any]:
    invites = invite_service.list_all(db)
    creators = _creator_names(db, invites)
    return {"items": [_invite_out(db, invite, creators) for invite in invites]}


@router.post("/invites", response_model=InviteOut, status_code=201)
def create_invite(payload: InviteCreateRequest, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    invite = invite_service.create_invite(
        db,
        created_by=principal.user_id,
        max_uses=payload.maxUses,
        expires_in_days=payload.expiresInDays,
        bonus_credits=payload.bonusCredits,
        note=payload.note,
    )
    audit_service.record(
        db,
        principal,
        "invite.create",
        AuditTarget("invite", invite.id, invite_service.mask(invite.code)),
        {"maxUses": payload.maxUses, "expiresInDays": payload.expiresInDays, "bonusCredits": payload.bonusCredits},
    )
    db.commit()
    return _invite_out(db, invite, {principal.user_id: principal.username or ""} if principal.user_id else {})


@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(invite_id: str, db: DbDep, principal: PrincipalDep) -> Response:
    """Revoke = stop future use. People who already registered with it are untouched."""
    invite = db.get(InviteCode, invite_id)
    if invite is None:
        raise NotFound("邀请码不存在")
    if invite.disabled_at is None:
        invite_service.revoke(db, invite)
        audit_service.record(
            db, principal, "invite.revoke", AuditTarget("invite", invite.id, invite_service.mask(invite.code))
        )
    db.commit()
    return Response(status_code=204)


# ── quota requests ───────────────────────────────────────────────────────────


def _request_rows(db: Session, rows: list[QuotaRequest], now: datetime) -> list[dict[str, Any]]:
    """Requester + 7-day run count for a page of requests: two statements, not two per row."""
    if not rows:
        return []
    ids = list({row.user_id for row in rows})
    people = {user.id: user for user in db.execute(select(User).where(User.id.in_(ids))).scalars()}
    week_ago = iso(now - timedelta(days=7))
    counts = dict(
        db.execute(
            select(Run.created_by, func.count())
            .where(Run.created_by.in_(ids), Run.started_at >= week_ago)
            .group_by(Run.created_by)
        ).all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        user = people.get(row.user_id)
        out.append(
            {
                "id": row.id,
                "user": {"id": user.id, "username": user.username, "displayName": user.display_name} if user else None,
                "resource": row.resource,
                "amount": row.amount,
                "reason": row.reason,
                "status": row.status,
                "createdAt": row.created_at,
                "userRuns7d": int(counts.get(row.user_id, 0)),
                "granted": row.granted,
                "resolvedBy": row.resolved_by,
                "resolvedAt": row.resolved_at,
                "resolutionNote": row.resolution_note,
            }
        )
    return out


@router.get("/quota-requests", response_model=QuotaRequestsOut)
def list_quota_requests(
    db: DbDep,
    status: Literal["pending", "approved", "rejected"] = "pending",
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    rows, total, next_cursor = quota_requests.list_queue(db, status=status, limit=limit, before=before)
    return {"items": _request_rows(db, rows, clock.now()), "total": total, "nextCursor": next_cursor}


@router.post("/quota-requests/{request_id}/resolve", response_model=QuotaRequestRow)
def resolve_quota_request(
    request_id: str, payload: ResolveQuotaRequest, db: DbDep, principal: PrincipalDep
) -> dict[str, Any]:
    row = quota_requests.resolve(
        db,
        actor=principal,
        request_id=request_id,
        decision=payload.decision,
        grant=payload.grant,
        note=payload.note,
    )
    requester = db.get(User, row.user_id)
    audit_service.record(
        db,
        principal,
        "quota.request.approve" if payload.decision == "approve" else "quota.request.reject",
        AuditTarget("quota_request", row.id, requester.username if requester else None),
        {
            "userId": row.user_id,
            "resource": row.resource,
            "requested": row.amount,
            "granted": row.granted,
            "note": payload.note,
        },
    )
    db.commit()
    return _request_rows(db, [row], clock.now())[0]


# ── audit (read-only) ────────────────────────────────────────────────────────


def _iso_bound(name: str, value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    moment = parse_iso(value.strip())
    if moment is None:
        raise InvalidQuery(f"{name} 不是合法的 ISO 时间", detail={"field": name})
    return iso(moment)


@router.get("/audit/export.csv", response_class=StreamingResponse)
def export_audit(
    db: DbDep,
    principal: PrincipalDep,
    action: str | None = Query(default=None, max_length=32),
    actor: str | None = Query(default=None, max_length=64),
    target: str | None = Query(default=None, max_length=64),
    since: str | None = Query(default=None, alias="from", max_length=40),
    until: str | None = Query(default=None, alias="to", max_length=40),
) -> StreamingResponse:
    """CSV of the audit log under the same filters as `GET /admin/audit` (§25.3.5a)."""
    if action and action not in audit_service.ACTIONS:
        raise InvalidQuery("未知的审计动作", detail={"field": "action"})
    return export_service.audit_csv(
        db,
        principal,
        action=action or None,
        actor=actor or None,
        target=target or None,
        since=_iso_bound("from", since),
        until=_iso_bound("to", until),
    )


@router.get("/audit", response_model=AuditOut)
def list_audit(
    db: DbDep,
    action: str | None = Query(default=None, max_length=32),
    actor: str | None = Query(default=None, max_length=64),
    target: str | None = Query(default=None, max_length=64),
    since: str | None = Query(default=None, alias="from", max_length=40),
    until: str | None = Query(default=None, alias="to", max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, max_length=96),
) -> dict[str, Any]:
    if action and action not in audit_service.ACTIONS:
        raise InvalidQuery("未知的审计动作", detail={"field": "action"})
    rows, next_cursor = audit_service.query(
        db,
        action=action or None,
        actor=actor or None,
        target=target or None,
        since=_iso_bound("from", since),
        until=_iso_bound("to", until),
        limit=limit,
        before=before,
    )
    return {"items": [admin_overview.audit_row_dict(row) for row in rows], "nextCursor": next_cursor}


# ── settings ─────────────────────────────────────────────────────────────────


@router.get("/settings", response_model=SettingsOut)
def get_settings_view(db: DbDep) -> dict[str, object]:
    return settings_service.get_all(db)


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(
    db: DbDep,
    principal: PrincipalDep,
    payload: dict[str, Any] = Body(...),
) -> dict[str, object]:
    """Each field is validated on its own; any bad one (`422 invalid_setting`, `detail.field`)
    rejects the whole request. Read per request everywhere, so a change needs no restart (AC-199)."""
    changes = settings_service.update(db, payload)
    if changes:
        audit_service.record(
            db, principal, "settings.update", AuditTarget("settings", None, None), {"changes": changes}
        )
    db.commit()
    return settings_service.get_all(db)


# ── featured watchlist ───────────────────────────────────────────────────────


@router.get("/featured-watchlist")
def list_featured(db: DbDep) -> dict[str, Any]:
    return {"items": featured_service.list_featured(db)}


@router.post("/featured-watchlist", status_code=201)
def add_featured(payload: FeaturedAddRequest, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    featured_service.add_featured(db, principal, payload.instrumentId)
    db.commit()
    return {"items": featured_service.list_featured(db)}


@router.delete("/featured-watchlist/{instrument_id}", status_code=204)
def remove_featured(instrument_id: str, db: DbDep, principal: PrincipalDep) -> Response:
    featured_service.remove_featured(db, principal, instrument_id)
    db.commit()
    return Response(status_code=204)
