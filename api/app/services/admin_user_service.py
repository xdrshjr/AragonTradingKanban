"""Admin actions on users: list, create, change, reset, unlock, kick, delete (spec §24.3.14, §24.5.3).

Division of labour (kept strict so each half is testable alone):

* `user_service` owns the **row mechanics** (insert a row, flip a status, tombstone a name);
* `security/objects.py::assert_can_manage_user` owns **who may do what to whom** (pure);
* this module is the **orchestration**: it asks the matrix first, then mutates, then records
  the audit row — all inside the caller's transaction. Nothing here commits; the router does,
  so "change role + audit" is one atomic unit (an action that is audited but not applied, or
  applied but not audited, would be worse than either failing).

The listing is the other half: a keyset-paginated page with **no N+1** — the per-row quota
comes from one `GROUP BY` over the page's ids, and the invite label from a `LEFT JOIN` in the
page query itself — so a list request costs a handful of statements regardless of page size
(AC-195 asserts the count).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso
from ..errors import AppError, InvalidCursor, NotFound, WeakPassword
from ..models import Instrument, InviteCode, QuotaLedger, Run, User
from ..security import clock
from ..security.objects import assert_can_create_user, assert_can_manage_user
from ..security.passwords import hash_password, password_rule_failures
from ..security.principal import Principal
from . import account_lifecycle, audit_service, quota_service, session_service, settings_service, user_service
from .audit_service import AuditTarget

# ── temporary passwords ──────────────────────────────────────────────────────

TEMP_PASSWORD_LENGTH = 12
#: No `0 O 1 I l` — an admin reads this aloud or pastes it into a chat.
_LOWER = "abcdefghjkmnpqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"


def generate_temp_password(username: str | None = None) -> str:
    """12 random characters with at least one of each class, accepted by the password policy.

    `secrets`, never `random`. The retry loop is for the (rare) draw that happens to contain the
    username or a keyboard run; the policy is the arbiter, so the result is guaranteed valid.
    """
    alphabet = _LOWER + _UPPER + _DIGITS
    while True:
        chars = [secrets.choice(_LOWER), secrets.choice(_UPPER), secrets.choice(_DIGITS)]
        chars += [secrets.choice(alphabet) for _ in range(TEMP_PASSWORD_LENGTH - len(chars))]
        secrets.SystemRandom().shuffle(chars)
        candidate = "".join(chars)
        if not password_rule_failures(candidate, username):
            return candidate


def _target(user: User) -> AuditTarget:
    return AuditTarget("user", user.id, user.username)


def get_or_404(db: Session, user_id: str) -> User:
    user = user_service.get_by_id(db, user_id)
    if user is None:
        raise NotFound("用户不存在")
    return user


# ── list ─────────────────────────────────────────────────────────────────────

_CURSOR = re.compile(r"^(?P<at>\d{4}-\d{2}-\d{2}T[^|]+Z)\|(?P<id>[0-9a-f]{32})$")
_Q_PATTERN = re.compile(r"^[a-z0-9_]{1,20}$")
MAX_PAGE = 200


def prefix_bounds(q: str) -> tuple[str, str] | None:
    """`(lo, hi)` for `username_lc >= lo AND username_lc < hi`, or `None` if `q` cannot match.

    Not `LIKE` (RV-8): SQLite's `LIKE` is case-insensitive while `ux_users_username_lc` is
    BINARY, so it would not use the index, and `%`/`_` in `q` would act as wildcards. A range
    over the lower-cased column does both jobs. `q` outside `[a-z0-9_]{1,20}` matches nothing.
    """
    q = (q or "").strip().lower()
    if not _Q_PATTERN.fullmatch(q):
        return None
    return q, q[:-1] + chr(ord(q[-1]) + 1)


def mask_invite(invite_code: str | None, note: str | None) -> str | None:
    if note:
        return note
    if invite_code:
        return f"{invite_code[:4]}-****"
    return None


def quota_lite(db: Session, users: list[User], now: datetime) -> dict[str, dict[str, int | None]]:
    """`{user_id: {limit, usedToday, bonus, remaining}}` for the `report` resource, for a page of
    users, from **one** `GROUP BY` (plus one settings read for the default)."""
    if not users:
        return {}
    today = clock.local_day_key(now, get_settings().quota_tz)
    rows = db.execute(
        select(
            QuotaLedger.user_id,
            func.coalesce(
                func.sum(
                    case(
                        (and_(QuotaLedger.source == "daily", QuotaLedger.day_key == today), -QuotaLedger.delta), else_=0
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(case((QuotaLedger.source == "bonus", QuotaLedger.delta), else_=0)), 0),
        )
        .where(QuotaLedger.resource == "report", QuotaLedger.user_id.in_([user.id for user in users]))
        .group_by(QuotaLedger.user_id)
    ).all()
    sums = {user_id: (int(used or 0), int(bonus or 0)) for user_id, used, bonus in rows}
    default = settings_service.default_daily(db, "report")
    out: dict[str, dict[str, int | None]] = {}
    for user in users:
        used, bonus = sums.get(user.id, (0, 0))
        limit = user.quota_daily_limit
        if limit is None:
            limit = -1 if user.role in ("admin", "root") else default
        if limit == -1:
            out[user.id] = {"limit": None, "usedToday": max(0, used), "bonus": max(0, bonus), "remaining": None}
        else:
            out[user.id] = {
                "limit": limit,
                "usedToday": max(0, used),
                "bonus": max(0, bonus),
                "remaining": max(0, limit - used) + max(0, bonus),
            }
    return out


def user_dict(user: User, quota: dict[str, int | None] | None, invited_by: str | None, now: datetime) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "role": user.role,
        "status": user.status,
        "createdAt": user.created_at,
        "lastLoginAt": user.last_login_at,
        "quota": {"report": quota or {"limit": None, "usedToday": 0, "bonus": 0, "remaining": None}},
        "locked": user_service.is_locked(user, now),
        "invitedBy": invited_by,
    }


def user_filters(
    *,
    q: str | None = None,
    role: str | None = None,
    status: str | None = None,
    locked: bool = False,
    now: datetime | None = None,
) -> list[Any] | None:
    """The one WHERE-clause builder for the user list **and** its CSV export (§25.3.5a).

    `None` = the filter can match nobody (a `q` made only of wildcard characters).
    `locked` = `status='active'` accounts whose lock has not expired (F-26): the `status`
    equality is served by `ix_users_status_role`, the time comparison is a residual filter.
    """
    moment = now or clock.now()
    filters: list[Any] = []
    if q is not None and q.strip():
        bounds = prefix_bounds(q)
        if bounds is None:
            return None
        filters += [User.username_lc >= bounds[0], User.username_lc < bounds[1]]
    if role:
        filters.append(User.role == role)
    if locked:
        if status and status != "active":
            return None
        filters += [User.status == "active", User.locked_until.is_not(None), User.locked_until > iso(moment)]
    elif status:
        filters.append(User.status == status)
    else:
        # Tombstones are noise in the everyday list; they are shown when asked for explicitly.
        filters.append(User.status != "deleted")
    return filters


def _user_page(
    db: Session, filters: list[Any], *, limit: int, before: str | None
) -> tuple[list[tuple[User, str | None, str | None]], bool]:
    """One keyset page `(rows, has_more)`, newest first on `(created_at, id)`; the invite label
    comes from a `LEFT JOIN` in the same statement (no second query)."""
    page_filters = list(filters)
    if before:
        match = _CURSOR.match(before)
        if match is None:
            raise InvalidCursor()
        at, row_id = match.group("at"), match.group("id")
        page_filters.append(or_(User.created_at < at, and_(User.created_at == at, User.id < row_id)))
    rows = db.execute(
        select(User, InviteCode.code, InviteCode.note)
        .join(InviteCode, InviteCode.id == User.invite_id, isouter=True)
        .where(*page_filters)
        .order_by(User.created_at.desc(), User.id.desc())
        .limit(limit + 1)
    ).all()
    return [(row[0], row[1], row[2]) for row in rows[:limit]], len(rows) > limit


def list_users(
    db: Session,
    *,
    q: str | None = None,
    role: str | None = None,
    status: str | None = None,
    locked: bool = False,
    limit: int = 50,
    before: str | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """`(items, total, next_cursor)`, newest first on `(created_at, id)`."""
    moment = now or clock.now()
    limit = max(1, min(int(limit), MAX_PAGE))
    filters = user_filters(q=q, role=role, status=status, locked=locked, now=moment)
    if filters is None:
        return [], 0, None

    total = count_users(db, filters)

    page, has_more = _user_page(db, filters, limit=limit, before=before)
    users = [row[0] for row in page]
    next_cursor = f"{users[-1].created_at}|{users[-1].id}" if has_more and users else None
    quotas = quota_lite(db, users, moment)
    items = [user_dict(user, quotas.get(user.id), mask_invite(code, note), moment) for user, code, note in page]
    return items, total, next_cursor


def count_users(db: Session, filters: list[Any]) -> int:
    return int(db.execute(select(func.count()).select_from(User).where(*filters)).scalar_one())


def export_rows(
    db: Session, filters: list[Any], *, size: int, before: str | None, now: datetime
) -> tuple[list[dict[str, Any]], str | None]:
    """One CSV chunk `(rows, cursor_of_last_row_or_None)` — **at most `size` (<= 500) users and
    exactly one `quota_lite` call** (its `IN (...)` must stay under SQLite's 999-variable ceiling;
    RV-Y9). Same filters, same order as `list_users`, so the export equals the list row for row."""
    page, _more = _user_page(db, filters, limit=size, before=before)
    users = [row[0] for row in page]
    quotas = quota_lite(db, users, now)
    rows: list[dict[str, Any]] = []
    for user, code, note in page:
        item = user_dict(user, quotas.get(user.id), mask_invite(code, note), now)
        item["hasRecoveryKey"] = bool(user.recovery_hash)
        rows.append(item)
    cursor = f"{users[-1].created_at}|{users[-1].id}" if users else None
    return rows, cursor


# ── detail ───────────────────────────────────────────────────────────────────


def detail(db: Session, user: User, now: datetime | None = None) -> dict[str, Any]:
    moment = now or clock.now()
    invite = db.get(InviteCode, user.invite_id) if user.invite_id else None
    base = user_dict(
        user,
        quota_lite(db, [user], moment).get(user.id),
        mask_invite(invite.code if invite else None, invite.note if invite else None),
        moment,
    )
    creator = user_service.get_by_id(db, user.created_by) if user.created_by else None
    runs = db.execute(
        select(Run.id, Run.instrument_id, Instrument.symbol, Run.status, Run.started_at, Run.finished_at, Run.cost_usd)
        .join(Instrument, Instrument.id == Run.instrument_id, isouter=True)
        .where(Run.created_by == user.id)
        .order_by(Run.started_at.desc())
        .limit(20)
    ).all()
    sessions = session_service.list_live(db, user.id, moment)
    # The full QuotaStatus comes from the engine (it also reconciles refunds first). A
    # tombstone has no quota worth showing.
    status = quota_service.status(db, user, now=moment) if user.status != "deleted" else None
    return {
        **base,
        "disabledReason": user.disabled_reason,
        "disabledAt": user.disabled_at,
        "mustChangePassword": bool(user.must_change_password),
        "passwordIsDefault": bool(user.password_is_default),
        "failedLogins": int(user.failed_logins or 0),
        "lockedUntil": user.locked_until if base["locked"] else None,
        "lastLoginIp": user.last_login_ip,
        "createdBy": creator.username if creator else None,
        "quotaStatus": status,
        "recentRuns": [
            {
                "id": run_id,
                "instrumentId": instrument_id,
                "symbol": symbol,
                "status": run_status,
                "startedAt": started,
                "finishedAt": finished,
                "costUsd": cost,
            }
            for run_id, instrument_id, symbol, run_status, started, finished, cost in runs
        ],
        "sessions": [
            {
                "id": row.id,
                "createdAt": row.created_at,
                "lastSeenAt": row.last_seen_at,
                "expiresAt": row.expires_at,
                "ip": row.ip,
                "userAgent": row.user_agent,
                "remember": bool(row.remember),
            }
            for row in reversed(sessions)
        ],
    }


# ── create ───────────────────────────────────────────────────────────────────


def create_user(
    db: Session,
    actor: Principal,
    *,
    username: str,
    role: str = "user",
    password: str | None = None,
    display_name: str | None = None,
    bonus_credits: int = 0,
    now: datetime | None = None,
) -> tuple[User, str | None]:
    """Create an account. Returns `(user, temporary_password_or_None)`.

    * `role='admin'` is root-only (`admin_protected`); nobody creates a second root.
    * No `password` → a 12-character temporary one is generated, shown **once** by the caller,
      A supplied password must pass the normal policy. Either way `must_change_password` is
      set (D-10): an admin who knows a user's password must not keep using it.
    """
    moment = now or clock.now()
    assert_can_create_user(actor, role)
    user_service.validate_username(username)
    temporary: str | None = None
    if password is None:
        temporary = generate_temp_password(username)
        password = temporary
    else:
        failures = password_rule_failures(password, username)
        if failures:
            raise WeakPassword(rules=failures)
    user = user_service.create_user(
        db,
        username=username,
        password_hash=hash_password(password),
        role=role,
        display_name=display_name,
        created_by=actor.user_id,
        # ★v16（D-10 / A-18）：管理员知道的密码不该是用户的长期密码——无论是否手填，首登必须改密。
        must_change_password=True,
        now=moment,
    )
    audit_service.record(
        db,
        actor,
        "user.create",
        _target(user),
        {"role": role, "generatedPassword": temporary is not None, "bonusCredits": bonus_credits},
        now=moment,
    )
    if bonus_credits > 0:
        quota_service.grant(
            db, actor=actor, target_user=user, resource="report", amount=bonus_credits, note="创建账号赠送"
        )
        audit_service.record(
            db,
            actor,
            "quota.grant",
            _target(user),
            {"resource": "report", "amount": bonus_credits, "via": "user.create"},
            now=moment,
        )
    return user, temporary


# ── change ───────────────────────────────────────────────────────────────────


def patch_user(
    db: Session,
    actor: Principal,
    target: User,
    *,
    fields: set[str],
    display_name: str | None = None,
    role: str | None = None,
    status: str | None = None,
    disabled_reason: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Apply `PATCH /admin/users/{id}`; returns the audit actions written.

    Two passes: **every** requested change is authorised against the matrix first, only then is
    anything written, so a request that is half-allowed fails whole instead of half-applying.
    """
    moment = now or clock.now()
    if target.status == "deleted":
        raise NotFound("用户已注销")
    if "displayName" in fields and display_name != target.display_name:
        assert_can_manage_user(actor, target, "update_profile")
    if role is not None and role != target.role:
        assert_can_manage_user(actor, target, "set_role", new_role=role)
    if status is not None and status != target.status:
        assert_can_manage_user(actor, target, "disable" if status == "disabled" else "enable")

    written: list[str] = []
    if "displayName" in fields and display_name != target.display_name:
        user_service.set_display_name(db, target, display_name)
    if role is not None and role != target.role:
        previous = target.role
        user_service.set_role(db, target, role, moment)
        audit_service.record(db, actor, "user.role.change", _target(target), {"from": previous, "to": role}, now=moment)
        written.append("user.role.change")
    if status is not None and status != target.status:
        if status == "disabled":
            user_service.disable(db, target, disabled_reason, moment)
            revoked = session_service.revoke_all_for_user(db, target.id, reason="disabled", now=moment)
            audit_service.record(
                db,
                actor,
                "user.disable",
                _target(target),
                {"reason": (disabled_reason or "")[:200], "revokedSessions": revoked},
                now=moment,
            )
            written.append("user.disable")
        else:
            user_service.enable(db, target, moment)
            audit_service.record(db, actor, "user.enable", _target(target), now=moment)
            written.append("user.enable")
    return written


def reset_password(db: Session, actor: Principal, target: User, *, now: datetime | None = None) -> str:
    """New temporary password for `target`; every session is revoked; the user must change it.

    Also clears a lockout: the usual reason for a reset is a forgotten password, and the same
    person is then locked out of the account they were just given a way into.
    """
    moment = now or clock.now()
    assert_can_manage_user(actor, target, "reset_password")
    if target.status == "deleted":
        raise NotFound("用户已注销")
    temporary = generate_temp_password(target.username)
    user_service.set_password(db, target, hash_password(temporary), must_change=True, is_default=False, now=moment)
    user_service.unlock(db, target, moment)
    revoked = session_service.revoke_all_for_user(db, target.id, reason="admin", now=moment)
    audit_service.record(db, actor, "auth.password.reset", _target(target), {"revokedSessions": revoked}, now=moment)
    return temporary


def revoke_sessions(db: Session, actor: Principal, target: User, *, now: datetime | None = None) -> int:
    moment = now or clock.now()
    assert_can_manage_user(actor, target, "revoke_sessions")
    revoked = session_service.revoke_all_for_user(db, target.id, reason="admin", now=moment)
    audit_service.record(
        db, actor, "auth.session.revoke", _target(target), {"count": revoked, "scope": "admin"}, now=moment
    )
    return revoked


def revoke_one_session(
    db: Session, actor: Principal, target: User, session_id: str, *, now: datetime | None = None
) -> None:
    """Kick one device (B-19). A session that is not this user's, or is already dead, is a 404."""
    moment = now or clock.now()
    assert_can_manage_user(actor, target, "revoke_sessions")
    row = session_service.get_owned(db, session_id, target.id)
    if row is None:
        raise NotFound("会话不存在")
    session_service.revoke(db, row, "admin", moment)
    audit_service.record(
        db, actor, "auth.session.revoke", _target(target), {"sessionId": row.id, "scope": "admin"}, now=moment
    )


def unlock(db: Session, actor: Principal, target: User, *, now: datetime | None = None) -> None:
    moment = now or clock.now()
    assert_can_manage_user(actor, target, "unlock")
    user_service.unlock(db, target, moment)
    audit_service.record(db, actor, "user.unlock", _target(target), now=moment)


def _apply_grant(
    db: Session,
    actor: Principal,
    target: User,
    *,
    resource: str,
    grant: int,
    note: str | None,
    moment: datetime,
    audit_extra: dict[str, Any] | None = None,
) -> bool:
    """The `grant` branch of `set_quota`; `True` iff a ledger row was actually written."""
    row = quota_service.grant(db, actor=actor, target_user=target, resource=resource, amount=grant, note=note)
    # A take-back is clamped to the bonus balance; clamped to nothing = nothing happened,
    # so nothing is audited (the audit trail records effects, not intentions).
    if row is None:
        return False
    audit_service.record(
        db,
        actor,
        "quota.grant",
        _target(target),
        {"resource": resource, "amount": row.delta, "requested": grant, "note": note, **(audit_extra or {})},
        now=moment,
    )
    return True


def set_quota(
    db: Session,
    actor: Principal,
    target: User,
    *,
    resource: str,
    grant: int | None,
    daily_limit_set: bool,
    daily_limit: int | None,
    note: str | None,
    now: datetime | None = None,
    audit_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grant/take back credits and/or change the daily-limit override; returns `QuotaStatus`.

    `audit_extra` is merged into the `quota.grant` audit detail (bulk operations tag theirs
    with `{bulk: true, batch: <id>}`)."""
    moment = now or clock.now()
    assert_can_manage_user(actor, target, "quota")
    if target.status == "deleted":
        raise NotFound("用户已注销")
    if grant:
        _apply_grant(
            db, actor, target, resource=resource, grant=grant, note=note, moment=moment, audit_extra=audit_extra
        )
    if daily_limit_set:
        quota_service.set_limit(db, actor=actor, target_user=target, resource=resource, limit=daily_limit)
        audit_service.record(
            db,
            actor,
            "quota.limit.set",
            _target(target),
            {"resource": resource, "limit": daily_limit},
            now=moment,
        )
    db.flush()
    return quota_service.status(db, target, now=moment)


def bulk_grant(
    db: Session,
    actor: Principal,
    *,
    user_ids: list[str],
    grant: int,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Give (or take back) `grant` bonus credits to many users, all-or-nothing (§25.3.5b).

    Two passes, so a refusal anywhere writes nothing. Priority: shape (`422`, the schema) ->
    existence (`404`) -> authority (`403`).

    1. dedupe; one `SELECT ... IN`; any unknown or tombstoned id -> `404 not_found`; then every
       target through the management matrix (an admin may only touch `role='user'`).
    2. one `_apply_grant` each, in the caller's transaction (the router commits once; any
       exception rolls the whole batch back).

    `applied` counts users whose ledger actually changed; a take-back clamped to nothing is
    `skipped` (it writes no ledger row and no audit row — same semantics as a single grant).
    """
    moment = now or clock.now()
    if grant == 0 or not (quota_service.GRANT_MIN <= grant <= quota_service.GRANT_MAX):
        raise AppError(
            "额度调整量超出范围",
            code="validation_error",
            status_code=422,
            detail={"field": "grant", "min": quota_service.GRANT_MIN, "max": quota_service.GRANT_MAX},
        )
    ids = list(dict.fromkeys(user_ids))
    found = {user.id: user for user in db.execute(select(User).where(User.id.in_(ids))).scalars()}
    targets: list[User] = []
    for user_id in ids:
        user = found.get(user_id)
        if user is None or user.status == "deleted":
            raise NotFound("部分用户不存在或已注销")
        targets.append(user)
    for user in targets:
        assert_can_manage_user(actor, user, "quota")

    batch_id = secrets.token_hex(6)
    applied = 0
    for user in targets:
        if _apply_grant(
            db,
            actor,
            user,
            resource="report",
            grant=grant,
            note=note,
            moment=moment,
            audit_extra={"bulk": True, "batch": batch_id},
        ):
            applied += 1
    db.flush()
    return {"applied": applied, "skipped": len(targets) - applied, "batchId": batch_id}


def delete_user(
    db: Session, actor: Principal, target: User, *, confirm: str | None, now: datetime | None = None
) -> None:
    """Delete = anonymise (§24.3.1). The caller must type the target's username (checked here —
    the front end's type-to-confirm is a courtesy, not the control)."""
    moment = now or clock.now()
    assert_can_manage_user(actor, target, "delete")
    if target.status == "deleted":
        raise NotFound("用户已注销")
    if (confirm or "").strip() != target.username:
        raise AppError(
            "确认的用户名与目标不一致",
            code="validation_error",
            status_code=422,
            detail={"field": "confirm"},
        )
    account_lifecycle.delete_account(db, actor, target, via="admin", now=moment)
