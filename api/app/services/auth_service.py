"""Register / login / logout / change-password orchestration (spec §24.3.5–§24.3.7).

This is the one place that strings together the captcha, the password KDF, lockout, the
session, the invite and the audit trail. Every function takes the SQLAlchemy `Session`
and a `ClientInfo` (already-resolved, already-truncated client facts) and returns plain
objects — no FastAPI types, so it is testable without HTTP. The routes in
`routers/auth.py` are thin: read the cookie, call a function here, set a cookie.

Anti-enumeration is the design constraint behind most of the code below (RV-3, AC-170):
a username that does not exist and one that does must be indistinguishable by status,
body shape, headers **and** work done (one scrypt each). That is why a missing user is
verified against `DUMMY_HASH`, why unknown names have a `ShadowThrottle`, and why both
paths write an audit row.

Nothing here caches settings: registration mode and the bonus are read from the database
per call, so an admin's change applies without a restart (AC-199).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso
from ..errors import (
    AccountDisabled,
    AccountLocked,
    AppError,
    CaptchaInvalid,
    CaptchaRequired,
    CaptchaUnavailable,
    InvalidCredentials,
    InviteInvalid,
    InviteRequired,
    RecoveryDisabled,
    RecoveryInvalid,
    RegistrationClosed,
    RootProtected,
    UsernameTaken,
    WeakPassword,
)
from ..logging import get_logger
from ..models import QuotaLedger, User, UserSession
from ..security import clock, ratelimit, recovery
from ..security.objects import assert_can_self_delete
from ..security.passwords import (
    DUMMY_HASH,
    MAX_LENGTH,
    MIN_CLASSES,
    MIN_LENGTH,
    hash_password,
    needs_rehash,
    password_rule_failures,
    verify_password,
)
from ..security.principal import GUEST, Principal, user_principal
from . import account_lifecycle, audit_service, invite_service, session_service, settings_service, user_service
from .audit_service import AuditTarget
from .challenge import ChallengeUnavailable, IssuedChallenge, get_provider

logger = get_logger("auth")

#: Captcha answers faster than this after the form was shown are scripts (AC-168).
MIN_FORM_SECONDS = 1.2


@dataclass(frozen=True, slots=True)
class ClientInfo:
    #: Truncated for storage (IPv4 /24, IPv6 /48).
    ip: str
    #: Rate-limit key (IPv4 exact, IPv6 /64).
    ip_key: str
    user_agent: str | None = None


@dataclass(slots=True)
class AuthResult:
    user: User
    token: str
    session: UserSession
    remember: bool
    principal: Principal
    #: Plaintext recovery key - set **only** by `register()` (shown once, spec 25.3.3); else `None`.
    recovery_key: str | None = None


def _guest(client: ClientInfo) -> Principal:
    return GUEST.with_ip(client.ip, client.ip_key)


def _principal_for(user: User, session_row: UserSession | None, client: ClientInfo) -> Principal:
    return user_principal(
        user_id=user.id,
        username=user.username,
        role=user.role,  # type: ignore[arg-type]
        session_id=session_row.id if session_row else None,
        must_change_password=bool(user.must_change_password),
        ip=client.ip,
        ip_key=client.ip_key,
    )


# ── config / me ──────────────────────────────────────────────────────────────


def public_config(db: Session) -> dict[str, Any]:
    settings = get_settings()
    mode = settings_service.registration_mode(db)
    captcha_ok = get_provider().available()
    return {
        "authMode": settings.auth_mode,
        "registrationMode": mode,
        # "Open" self-service registration needs the captcha; `invite` mode needs only a code.
        "registrationOpen": mode == "open" and captcha_ok,
        "inviteRegistrationOpen": mode in ("open", "invite"),
        "captchaAvailable": captcha_ok,
        "passwordPolicy": {"min": MIN_LENGTH, "max": MAX_LENGTH, "minClasses": MIN_CLASSES},
        "usernamePolicy": {
            "min": user_service.USERNAME_MIN,
            "max": user_service.USERNAME_MAX,
            "pattern": "^[A-Za-z0-9_]+$",
        },
        "captchaKind": "captcha",
        "forceRootPasswordChange": bool(settings.auth_force_root_password_change),
        "recoveryEnabled": settings_service.recovery_enabled(db),
    }


def _quota_snapshot(db: Session, principal: Principal) -> dict[str, Any] | None:
    """`Me.quota` (M48): the signed-in user's `QuotaStatus`, refunds reconciled first."""
    from . import quota_service  # local: quota_service imports settings_service, same layer

    return quota_service.snapshot(db, principal)


def serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "role": user.role,
        "status": user.status,
        "createdAt": user.created_at,
        "lastLoginAt": user.last_login_at,
        "mustChangePassword": bool(user.must_change_password),
        "passwordIsDefault": bool(user.password_is_default),
        "hasRecoveryKey": bool(user.recovery_hash),
    }


def build_me(db: Session, principal: Principal) -> dict[str, Any]:
    """The `Me` object (spec §24.5.1). Never raises for a guest."""
    user = user_service.get_by_id(db, principal.user_id) if principal.kind == "user" else None
    return {
        "authMode": get_settings().auth_mode,
        "authenticated": user is not None,
        "user": serialize_user(user) if user is not None else None,
        "permissions": sorted(principal.permissions),
        "quota": _quota_snapshot(db, principal) if user is not None else None,
    }


# ── captcha helpers ──────────────────────────────────────────────────────────


def issue_challenge(purpose: str, client: ClientInfo) -> IssuedChallenge:
    """Issue a captcha or raise `503 captcha_unavailable`."""
    try:
        return get_provider().issue(purpose, client.ip)  # type: ignore[arg-type]
    except ChallengeUnavailable as exc:
        logger.warning("captcha unavailable: %s", exc)
        raise CaptchaUnavailable() from exc


def _captcha_ok(captcha_id: str | None, answer: str | None, purpose: str, client: ClientInfo) -> bool:
    if not captcha_id or not answer:
        return False
    return get_provider().verify(captcha_id, answer, purpose, client.ip)


# ── login ────────────────────────────────────────────────────────────────────


def _throttle_state(user: User | None, username_lc: str) -> ratelimit.ThrottleState:
    """Lockout state: from the `users` row when the name exists, from the shadow throttle
    when it does not (RV-3). Everything after this treats both identically."""
    if user is not None:
        return ratelimit.ThrottleState(int(user.failed_logins or 0), user_service.locked_until_epoch(user))
    return ratelimit.shadow_throttle.state(username_lc)


def _record_failure(db: Session, user: User | None, username_lc: str) -> ratelimit.ThrottleState:
    if user is not None:
        user_service.record_login_failure(db, user)
        return _throttle_state(user, username_lc)
    return ratelimit.shadow_throttle.record_failure(username_lc)


def _locked_error(state: ratelimit.ThrottleState, now: float) -> AccountLocked:
    remaining_ms = int(max(0.0, (state.locked_until or now) - now) * 1000)
    return AccountLocked(retry_after_ms=remaining_ms)


def login(
    db: Session,
    *,
    username: str,
    password: str,
    remember: bool,
    captcha_id: str | None,
    captcha_answer: str | None,
    client: ClientInfo,
) -> AuthResult:
    """Spec §24.3.7. The order of the checks **is** the contract; see the module docstring."""
    username_lc = user_service.username_lc(username)[:64]
    ratelimit.check("auth.login.global", ratelimit.GLOBAL_KEY)
    ratelimit.check("auth.login.user", username_lc)

    user = user_service.get_by_username(db, username)
    if user is not None and user.status == "deleted":
        user = None

    state = _throttle_state(user, username_lc)
    now = clock.epoch()
    if state.locked(now):
        # No password comparison, and no further failure piled on top (AC-169).
        raise _locked_error(state, now)

    provider = get_provider()
    if state.captcha_required and provider.available():
        if not captcha_id or not captcha_answer:
            raise CaptchaRequired(detail={"challenge": issue_challenge("login", client).to_dict()})
        if not provider.verify(captcha_id, captcha_answer, "login", client.ip):
            raise CaptchaInvalid()

    # One scrypt on every path: a missing user is checked against the dummy hash.
    password_ok = verify_password(password, user.password_hash if user is not None else DUMMY_HASH)

    if user is None or not password_ok:
        was_locked = state.locked(now)
        new_state = _record_failure(db, user, username_lc)
        audit_service.record_login_failure(db, _guest(client), username, user_id=user.id if user else None)
        if new_state.locked(now) and not was_locked:
            audit_service.record(
                db,
                _guest(client),
                "auth.login.lockout",
                AuditTarget("user", user.id if user else None, username),
                {"failed": new_state.failed},
            )
        db.commit()
        if new_state.locked(now):
            raise _locked_error(new_state, now)
        raise InvalidCredentials(detail={"captchaRequired": True} if new_state.captcha_required else None)

    # Password is right. A disabled account is only revealed to someone who proves they own it.
    if user.status == "disabled":
        raise AccountDisabled(detail={"reason": user.disabled_reason} if user.disabled_reason else None)

    moment = clock.now()
    user_service.record_login_success(db, user, client.ip, moment)
    if needs_rehash(user.password_hash):
        user_service.set_password(
            db,
            user,
            hash_password(password),
            must_change=bool(user.must_change_password),
            is_default=bool(user.password_is_default),
            now=moment,
        )
    token, session_row = session_service.create(
        db, user, remember=remember, ip=client.ip, user_agent=client.user_agent, now=moment
    )
    principal = _principal_for(user, session_row, client)
    audit_service.record(
        db,
        principal,
        "auth.login.success",
        AuditTarget("user", user.id, user.username),
        {"remember": remember},
        now=moment,
    )
    db.commit()
    return AuthResult(user=user, token=token, session=session_row, remember=remember, principal=principal)


# ── register ─────────────────────────────────────────────────────────────────


def _grant_bonus(
    db: Session, user_id: str, amount: int, reason: str, moment: datetime, note: str | None = None
) -> None:
    """A `bonus`-source ledger row (the only ledger writer that exists before M48)."""
    if amount <= 0:
        return
    db.add(
        QuotaLedger(
            user_id=user_id,
            resource="report",
            delta=amount,
            reason=reason,
            source="bonus",
            day_key=clock.local_day_key(moment, get_settings().quota_tz),
            ref_id=None,
            actor_id=None,
            note=note,
            created_at=iso(moment),
        )
    )
    db.flush()


def _too_fast(issued_at_ms: float | None) -> bool:
    if issued_at_ms is None:
        return False
    # Tolerate both epoch-seconds and epoch-milliseconds.
    seconds = issued_at_ms / 1000.0 if issued_at_ms > 1e11 else float(issued_at_ms)
    elapsed = clock.epoch() - seconds
    # A *negative* elapsed time means the visitor's clock runs ahead of ours. That is a
    # fact about their machine, not evidence of a script, and refusing it would lock every
    # visitor with a fast clock out of registration. The server-side measurement (the
    # challenge's own issue time inside `CaptchaProvider.verify`) is the authoritative one.
    return 0 <= elapsed < MIN_FORM_SECONDS


def register(
    db: Session,
    *,
    username: str,
    password: str,
    captcha_id: str | None,
    captcha_answer: str | None,
    invite_code: str | None,
    display_name: str | None,
    honeypot: str | None,
    issued_at: float | None,
    client: ClientInfo,
) -> AuthResult:
    """Spec §24.3.6 "注册流程". One transaction: user + invite use + bonus + session + audit."""
    ratelimit.check("auth.register.global", ratelimit.GLOBAL_KEY)

    mode = settings_service.registration_mode(db)
    if mode == "closed":
        raise RegistrationClosed()
    code = invite_service.normalize_code(invite_code)
    if mode == "invite" and not code:
        raise InviteRequired()

    # Honeypot and speed trap: refused exactly like a wrong captcha (AC-168).
    if honeypot or _too_fast(issued_at):
        raise CaptchaInvalid()

    invite = None
    if code:
        # Invite registration waives the captcha (§24.3.6) but is itself rate-limited: every
        # *wrong* code spends from a 5-per-10-minutes-per-IP budget.
        ratelimit.guard("auth.invite_probe", client.ip_key)
        invite = invite_service.find(db, code)
        if invite is None or not invite_service.is_redeemable(invite):
            ratelimit.record("auth.invite_probe", client.ip_key)
            raise InviteInvalid()
    else:
        provider = get_provider()
        if not provider.available():
            raise CaptchaUnavailable()
        if not _captcha_ok(captcha_id, captcha_answer, "register", client):
            raise CaptchaInvalid()

    user_service.validate_username(username)
    if user_service.get_by_username(db, username) is not None:
        raise UsernameTaken()
    failures = password_rule_failures(password, username)
    if failures:
        raise WeakPassword(rules=failures)

    moment = clock.now()
    recovery_key: str | None = None
    try:
        user = user_service.create_user(
            db,
            username=username,
            password_hash=hash_password(password),
            role="user",  # a registration can never produce anything else (AC-177)
            display_name=(display_name or "").strip() or None,
            invite_id=invite.id if invite is not None else None,
            now=moment,
        )
        bonus = settings_service.signup_bonus(db)
        if invite is not None:
            invite_service.redeem(db, code, moment)  # raises InviteInvalid when it was just spent
        if settings_service.recovery_enabled(db):
            # 25.3.3: only the digest is stored; the plaintext rides back in this one response.
            recovery_key = recovery.generate_key()
            user.recovery_hash = recovery.hash_key(recovery_key)
            user.recovery_set_at = iso(moment)
        _grant_bonus(db, user.id, bonus, "signup_bonus", moment)
        if invite is not None:
            _grant_bonus(db, user.id, int(invite.bonus_credits or 0), "invite_bonus", moment, f"invite {invite.id[:8]}")
        token, session_row = session_service.create(
            db, user, remember=False, ip=client.ip, user_agent=client.user_agent, now=moment
        )
        principal = _principal_for(user, session_row, client)
        audit_service.record(
            db,
            principal,
            "auth.register",
            AuditTarget("user", user.id, user.username),
            {"viaInvite": invite is not None, "bonus": bonus + (int(invite.bonus_credits or 0) if invite else 0)},
            now=moment,
        )
        user_service.record_login_success(db, user, client.ip, moment)
        db.commit()
    except IntegrityError as exc:  # pragma: no cover - the unique index lost a race we did not pre-check
        db.rollback()
        raise UsernameTaken() from exc
    except (InviteInvalid, UsernameTaken):
        db.rollback()
        raise
    return AuthResult(
        user=user, token=token, session=session_row, remember=False, principal=principal, recovery_key=recovery_key
    )


# ── password / logout / sessions ─────────────────────────────────────────────


def change_password(db: Session, principal: Principal, current_password: str, new_password: str) -> None:
    """Spec §24.5.1 `POST /auth/password`. Clears the forced-change flags and signs every
    *other* device out."""
    user = user_service.get_by_id(db, principal.user_id)
    if user is None:  # pragma: no cover - the route requires a signed-in user
        raise InvalidCredentials()
    ratelimit.check("auth.password", user.id)
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentials("当前密码不正确")
    failures = password_rule_failures(new_password, user.username)
    if failures:
        raise WeakPassword(rules=failures)
    moment = clock.now()
    user_service.set_password(db, user, hash_password(new_password), must_change=False, is_default=False, now=moment)
    session_service.revoke_all_for_user(
        db, user.id, reason="password_change", except_session_id=principal.session_id, now=moment
    )
    audit_service.record(db, principal, "auth.password.change", AuditTarget("user", user.id, user.username), now=moment)
    db.commit()


def logout(db: Session, principal: Principal, token: str | None) -> bool:
    """Revoke the session the cookie names. Idempotent; audits only when a live session was
    actually ended. Returns whether one was."""
    if not token:
        return False
    revoked = session_service.revoke_by_token(db, token, "logout")
    if revoked is None:
        db.rollback()
        return False
    audit_service.record(
        db,
        principal,
        "auth.logout",
        AuditTarget("user", revoked.user_id, principal.username),
    )
    db.commit()
    return True


# -- recovery key (spec 25.3.3) -----------------------------------------------

#: Test seam for AC-240: called after the key has been verified and before the compare-and-swap.
#: A test installs a callable that runs a *second* `recover` here, which is exactly the
#: double-click interleaving the CAS exists for. Always `None` in production.
_after_recovery_verify: Callable[[], None] | None = None


def _recovery_failure(db: Session, client: ClientInfo, username: str) -> RecoveryInvalid:
    """Audit (merged per `(claimed username, ip)`, **no** user id) and build the one error.

    The claimed name is recorded whether or not it exists, so the row's shape reveals nothing
    (AC-241). Rolls back first: nothing the failed attempt touched may survive.
    """
    db.rollback()
    audit_service.record_login_failure(db, _guest(client), username, action="auth.recovery.failure")
    db.commit()
    return RecoveryInvalid()


def recover(
    db: Session,
    *,
    username: str,
    recovery_key: str,
    new_password: str,
    captcha_id: str | None,
    captcha_answer: str | None,
    client: ClientInfo,
) -> dict[str, str]:
    """`POST /auth/recover`. **The order of the checks is the security design** (25.3.3):

    switch -> limits (IP by the route policy; site + username here) -> captcha -> new-password
    policy -> key -> compare-and-swap + everything else in one transaction. Every "this key /
    this user" failure is the same `401 recovery_invalid`.
    """
    if not settings_service.recovery_enabled(db):
        raise RecoveryDisabled()
    username_lc = user_service.username_lc(username)[:64]
    ratelimit.check("auth.recover.global", ratelimit.GLOBAL_KEY)
    # Peek only: counting before the captcha would let anyone lock a victim's recovery for an
    # hour without solving a single picture (RV-Y11).
    ratelimit.guard("auth.recover.user", username_lc)

    if not _captcha_ok(captcha_id, captcha_answer, "recover", client):
        raise CaptchaInvalid()
    # Counted from here on, for existing and non-existing names alike (RV-3's shape).
    ratelimit.record("auth.recover.user", username_lc)

    # The policy only depends on the *claimed* name, so it cannot be an oracle for the key and
    # a rejected password does not burn the key.
    failures = password_rule_failures(new_password, username)
    if failures:
        raise WeakPassword(rules=failures)

    user = user_service.get_by_username(db, username)
    eligible = user is not None and user.status == "active" and user.role != "root" and bool(user.recovery_hash)
    stored = user.recovery_hash if eligible and user is not None else recovery.DUMMY_HASH
    key_ok = recovery.verify(recovery_key, stored)
    if not (eligible and key_ok) or user is None:
        raise _recovery_failure(db, client, username)

    if _after_recovery_verify is not None:
        _after_recovery_verify()

    moment = clock.now()
    old_hash = user.recovery_hash
    new_key = recovery.generate_key()
    # Compare-and-swap: only the request that still holds the *current* hash may rotate it.
    swapped = db.execute(
        update(User)
        .where(User.id == user.id, User.recovery_hash == old_hash)
        .values(recovery_hash=recovery.hash_key(new_key), recovery_set_at=iso(moment))
    )
    if swapped.rowcount != 1:
        raise _recovery_failure(db, client, username)
    db.expire(user, ["recovery_hash", "recovery_set_at"])

    user_service.set_password(db, user, hash_password(new_password), must_change=False, is_default=False, now=moment)
    user.failed_logins = 0
    user.locked_until = None
    revoked = session_service.revoke_all_for_user(db, user.id, reason="password_recover", now=moment)
    audit_service.record(
        db,
        _principal_for(user, None, client),
        "auth.password.recover",
        AuditTarget("user", user.id, user.username),
        {"revokedSessions": revoked},
        now=moment,
    )
    db.commit()
    return {"recoveryKey": new_key, "setAt": iso(moment)}


def rotate_recovery_key(db: Session, principal: Principal, current_password: str) -> dict[str, str]:
    """`POST /auth/recovery-key`: first key for an older account, or a fresh one. Needs the
    current password. The route is single-flight on the front end; there is no old key to
    compare-and-swap against, so the per-user limit is the server-side guard (RV-Y17)."""
    user = user_service.get_by_id(db, principal.user_id)
    if user is None:  # pragma: no cover - the route requires a signed-in user
        raise InvalidCredentials()
    ratelimit.check("auth.recovery_rotate", user.id)
    ratelimit.check("auth.password", user.id)
    if user.role == "root":
        raise RootProtected(action="recovery_key")
    if not settings_service.recovery_enabled(db):
        raise RecoveryDisabled()
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentials("当前密码不正确")
    moment = clock.now()
    new_key = recovery.generate_key()
    user.recovery_hash = recovery.hash_key(new_key)
    user.recovery_set_at = iso(moment)
    user.updated_at = iso(moment)
    audit_service.record(db, principal, "auth.recovery.rotate", AuditTarget("user", user.id, user.username), now=moment)
    db.commit()
    return {"recoveryKey": new_key, "setAt": iso(moment)}


# -- self-service deletion (spec 25.3.4) --------------------------------------


def delete_own_account(db: Session, principal: Principal, password: str, confirm: str) -> None:
    """`DELETE /auth/account`. Authorisation is `assert_can_self_delete` - **not** the admin
    matrix (that one refuses "delete yourself" with `422 self_action_forbidden`)."""
    user = user_service.get_by_id(db, principal.user_id)
    if user is None or user.status == "deleted":  # pragma: no cover - the route requires a live user
        raise InvalidCredentials()
    assert_can_self_delete(principal, user)
    ratelimit.check("auth.password", user.id)
    if not verify_password(password, user.password_hash):
        raise InvalidCredentials("当前密码不正确")
    if (confirm or "").strip() != user.username:
        raise AppError(
            "确认的用户名与当前账号不一致",
            code="validation_error",
            status_code=422,
            detail={"field": "confirm"},
        )
    account_lifecycle.delete_account(db, principal, user, via="self")
    db.commit()
