"""`/auth` — sessions, registration, captcha, password (spec §24.5.1).

Three rules that are easy to break and each has a test:

* `login`, `register` and `change_password` are **plain `def`**, so FastAPI runs them in the
  thread pool. A scrypt is 60–100 ms of CPU; on the event loop it would freeze every SSE
  stream (the same class of bug `test_event_loop_guard.py` guards).
* `GET /auth/me` and `GET /auth/config` never answer 401/403; `POST /auth/logout` is
  idempotent. The front end initialises itself from them.
* The cookie is `HttpOnly; SameSite=Lax; Path=/`, `Secure` per `COOKIE_SECURE` (see
  `security/deps.py`); `remember=false` sets no `Max-Age` (a browser-session cookie).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.session import get_db
from ..errors import Forbidden, NotFound, Unauthorized
from ..schemas.auth import (
    AuthConfigOut,
    CaptchaOut,
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    MeOut,
    ProfilePatch,
    RecoverRequest,
    RecoveryKeyOut,
    RegisterOut,
    RegisterRequest,
    RevokeOthersOut,
    RotateRecoveryKeyRequest,
    SessionOut,
    SessionsOut,
)
from ..security import deps
from ..security.principal import Principal
from ..services import audit_service, auth_service, session_service, user_service
from ..services.audit_service import AuditTarget
from ..services.auth_service import ClientInfo
from ..services.captcha import get_captcha_provider

router = APIRouter(prefix="/auth", tags=["auth"])

#: Mounted by `create_app()` only when `AUTH_TEST_HOOKS=1` (never in the policy table).
test_hooks_router = APIRouter(prefix="/auth/_test", tags=["auth-test"], include_in_schema=False)


def _client(request: Request) -> ClientInfo:
    ip, ip_key = deps.client_ip(request)
    return ClientInfo(ip=ip, ip_key=ip_key, user_agent=request.headers.get("user-agent"))


def _me(db: Session, principal: Principal) -> MeOut:
    return MeOut.model_validate(auth_service.build_me(db, principal))


def _require_user(principal: Principal) -> Principal:
    """Account routes act on a *person*. The API-token principal has no account."""
    if principal.kind == "user":
        return principal
    if principal.kind == "guest":
        raise Unauthorized(reason="no_session")
    raise Forbidden(permission="account.self")


# ── public configuration ─────────────────────────────────────────────────────


@router.get("/config", response_model=AuthConfigOut)
def auth_config(db: Session = Depends(get_db)) -> AuthConfigOut:
    return AuthConfigOut.model_validate(auth_service.public_config(db))


@router.get("/captcha", response_model=CaptchaOut)
def captcha(
    request: Request,
    response: Response,
    purpose: Literal["register", "login", "recover"] = Query(default="register"),
) -> CaptchaOut:
    challenge = auth_service.issue_challenge(purpose, _client(request))
    response.headers["Cache-Control"] = "no-store"
    return CaptchaOut.model_validate(challenge.to_dict())


# ── register / login / logout ────────────────────────────────────────────────


@router.post("/register", response_model=RegisterOut, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> RegisterOut:
    result = auth_service.register(
        db,
        username=payload.username,
        password=payload.password,
        captcha_id=payload.captchaId,
        captcha_answer=payload.captchaAnswer,
        invite_code=payload.inviteCode,
        display_name=payload.displayName,
        honeypot=payload.website,
        issued_at=payload.issuedAt,
        client=_client(request),
    )
    deps.set_session_cookie(response, request, result.token, result.remember)
    # The recovery key is in this body and nowhere else, ever again (spec 25.3.3).
    return RegisterOut(**_me(db, result.principal).model_dump(), recoveryKey=result.recovery_key)


@router.post("/login", response_model=MeOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> MeOut:
    result = auth_service.login(
        db,
        username=payload.username,
        password=payload.password,
        remember=payload.remember,
        captcha_id=payload.captchaId,
        captcha_answer=payload.captchaAnswer,
        client=_client(request),
    )
    deps.set_session_cookie(response, request, result.token, result.remember)
    return _me(db, result.principal)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> Response:
    """Idempotent: no cookie, a dead cookie, or a live one all answer 204 and clear the cookie."""
    auth_service.logout(db, principal, request.cookies.get(deps.SESSION_COOKIE))
    response = Response(status_code=204)
    deps.clear_session_cookie(response, request)
    return response


@router.get("/me", response_model=MeOut)
def me(db: Session = Depends(get_db), principal: Principal = Depends(deps.get_principal)) -> MeOut:
    return _me(db, principal)


# ── password / profile / sessions (signed-in) ────────────────────────────────


@router.post("/password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> Response:
    auth_service.change_password(db, _require_user(principal), payload.currentPassword, payload.newPassword)
    return Response(status_code=204)


@router.post("/recover", response_model=RecoveryKeyOut)
def recover(payload: RecoverRequest, request: Request, db: Session = Depends(get_db)) -> RecoveryKeyOut:
    """Public. Signs **nobody** in: the front end shows the new key, then sends the person to
    `/login`. Plain `def` (a scrypt runs on success)."""
    result = auth_service.recover(
        db,
        username=payload.username,
        recovery_key=payload.recoveryKey,
        new_password=payload.newPassword,
        captcha_id=payload.captchaId,
        captcha_answer=payload.captchaAnswer,
        client=_client(request),
    )
    return RecoveryKeyOut.model_validate(result)


@router.post("/recovery-key", response_model=RecoveryKeyOut)
def rotate_recovery_key(
    payload: RotateRecoveryKeyRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> RecoveryKeyOut:
    result = auth_service.rotate_recovery_key(db, _require_user(principal), payload.currentPassword)
    return RecoveryKeyOut.model_validate(result)


@router.delete("/account", status_code=204)
def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> Response:
    auth_service.delete_own_account(db, _require_user(principal), payload.password, payload.confirm)
    response = Response(status_code=204)
    deps.clear_session_cookie(response, request)
    return response


@router.patch("/profile", response_model=MeOut)
def update_profile(
    payload: ProfilePatch,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> MeOut:
    _require_user(principal)
    user = user_service.get_by_id(db, principal.user_id)
    if user is not None and "displayName" in payload.model_fields_set:
        user_service.set_display_name(db, user, payload.displayName)
        db.commit()
    return _me(db, principal)


@router.get("/sessions", response_model=SessionsOut)
def list_sessions(db: Session = Depends(get_db), principal: Principal = Depends(deps.get_principal)) -> SessionsOut:
    _require_user(principal)
    rows = session_service.list_live(db, principal.user_id or "")
    return SessionsOut(
        items=[
            SessionOut(
                id=row.id,
                current=row.id == principal.session_id,
                createdAt=row.created_at,
                lastSeenAt=row.last_seen_at,
                ip=row.ip,
                userAgent=row.user_agent,
            )
            for row in reversed(rows)
        ]
    )


@router.post("/sessions/revoke-others", response_model=RevokeOthersOut)
def revoke_other_sessions(
    db: Session = Depends(get_db), principal: Principal = Depends(deps.get_principal)
) -> RevokeOthersOut:
    _require_user(principal)
    revoked = session_service.revoke_all_for_user(
        db, principal.user_id or "", reason="logout", except_session_id=principal.session_id
    )
    if revoked:
        audit_service.record(
            db,
            principal,
            "auth.session.revoke",
            AuditTarget("user", principal.user_id, principal.username),
            {"count": revoked, "scope": "others"},
        )
    db.commit()
    return RevokeOthersOut(revoked=revoked)


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> Response:
    """Only your own sessions (someone else's id is a 404, not a 403). Revoking the current
    one is a logout."""
    _require_user(principal)
    row = session_service.get_owned(db, session_id, principal.user_id or "")
    if row is None:
        raise NotFound()
    session_service.revoke(db, row, "logout")
    is_current = row.id == principal.session_id
    audit_service.record(
        db,
        principal,
        "auth.logout" if is_current else "auth.session.revoke",
        AuditTarget("user", principal.user_id, principal.username),
        None if is_current else {"sessionId": row.id},
    )
    db.commit()
    response = Response(status_code=204)
    if is_current:
        deps.clear_session_cookie(response, request)
    return response


# ── e2e hook ─────────────────────────────────────────────────────────────────


@test_hooks_router.get("/captcha-answer")
def captcha_answer(challengeId: str = Query(min_length=1, max_length=64)) -> dict[str, str]:  # noqa: N803
    """Playwright reads the answer here instead of OCR-ing the picture. Mounted **only** when
    `AUTH_TEST_HOOKS=1` — it hands out captcha answers, so it must never exist in production."""
    answer = get_captcha_provider().debug_answer(challengeId)
    if answer is None or not get_settings().auth_test_hooks:
        raise NotFound()
    return {"answer": answer}
