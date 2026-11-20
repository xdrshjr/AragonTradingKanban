"""FastAPI glue: resolve the principal, enforce the policy table (spec §24.3.3).

This is the **only** module in `security/` allowed to import `services/` — it is the HTTP
adapter (cookie → session lookup), the counterpart of what routers do, which is why only
routers and `main.py` import it. Everything else in `security/` stays importable from
`services/` without a cycle.

Resolution order (`get_principal`):

1. `AUTH_MODE=open` → a `service` principal holding the **whole** vocabulary (the old
   local-first behaviour). Read per request, never cached at import time (D-8).
2. A correct `Authorization: Bearer <API_TOKEN>` → `service` (narrowed by §24.3.4). A
   missing *or wrong* Bearer is **not** an error: it simply falls through to the cookie,
   so a stale token never turns a public page into a 401 and never masks a real session.
3. The `aragon_sid` cookie → one joined query (`sessions` ⋈ `users`) → `user`. A dead
   cookie is cleared on the way out and the request continues as `guest`.
4. Otherwise `guest`.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.session import get_db
from ..errors import Forbidden, PasswordChangeRequired, PolicyMissing, Unauthorized
from ..logging import get_logger
from ..services import session_service
from . import ipaddr, ratelimit
from .permissions import PASSWORD_CHANGE_ALLOWED
from .policy import policy_for
from .principal import GUEST, Principal, service_principal, user_principal

logger = get_logger("security")

SESSION_COOKIE = "aragon_sid"

_forwarded_warning_emitted = False


def reset_warning_state() -> None:
    """Test hygiene for the one-shot `X-Forwarded-For` warning."""
    global _forwarded_warning_emitted
    _forwarded_warning_emitted = False


# ── client address ───────────────────────────────────────────────────────────


def note_forwarded_header(request: Request) -> None:
    """RV-14: `TRUST_PROXY=0` yet a proxy header shows up → warn **once** per process.

    Behind nginx with the setting forgotten, every visitor is `127.0.0.1` and shares one
    login budget; the 11th person in a minute is locked out and nothing says why.
    """
    global _forwarded_warning_emitted
    if _forwarded_warning_emitted or get_settings().trust_proxy:
        return
    if request.headers.get("x-forwarded-for"):
        _forwarded_warning_emitted = True
        logger.warning(
            "检测到反向代理头但 TRUST_PROXY=0：所有访客将共用 127.0.0.1 的限速桶，请在 systemd 单元里设置 TRUST_PROXY=1"
        )


def raw_client_ip(request: Request) -> str:
    """The peer address, or — only when `TRUST_PROXY=1` — the **rightmost** `X-Forwarded-For`.

    nginx's `$proxy_add_x_forwarded_for` appends the real peer on the right; the leftmost
    entries are whatever the client claimed, so they are never trusted.
    """
    peer = request.client.host if request.client else "unknown"
    if not get_settings().trust_proxy:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    for candidate in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        if ipaddr.is_ip(candidate):
            return candidate
    return peer


def client_ip(request: Request) -> tuple[str, str]:
    """`(stored, rate_key)` — truncated for the database, exact (/64 for v6) for limiters."""
    raw = raw_client_ip(request)
    return ipaddr.stored_prefix(raw), ipaddr.rate_key(raw)


# ── Bearer / cookie helpers ──────────────────────────────────────────────────


def has_valid_service_bearer(request: Request) -> bool:
    token = get_settings().api_token
    if not token:
        return False
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(supplied.strip().encode("utf-8"), token.encode("utf-8"))


def use_secure_cookie(request: Request) -> bool:
    """`COOKIE_SECURE=1|0|auto` (spec §24.3.2). `auto`: https, or trusted-proxy https."""
    mode = (get_settings().cookie_secure or "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    if request.url.scheme == "https":
        return True
    return bool(get_settings().trust_proxy and request.headers.get("x-forwarded-proto", "").lower() == "https")


def set_session_cookie(response: Response, request: Request, token: str, remember: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=session_service.cookie_max_age_seconds(remember),
        httponly=True,
        samesite="lax",
        path="/",
        secure=use_secure_cookie(request),
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=use_secure_cookie(request),
    )


# ── principal ────────────────────────────────────────────────────────────────


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    settings = get_settings()
    ip, ip_key = client_ip(request)
    request.state.session_state = "none"

    if settings.auth_mode == "open":
        principal = service_principal(ip=ip, ip_key=ip_key, open_mode=True)
    elif has_valid_service_bearer(request):
        principal = service_principal(ip=ip, ip_key=ip_key)
    else:
        principal = GUEST.with_ip(ip, ip_key)
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            resolved = session_service.resolve(db, token)
            if resolved is None:
                # A dead cookie: tell the browser to drop it, carry on as a guest.
                request.state.clear_session_cookie = True
                request.state.session_state = "expired"
            else:
                user = resolved.user
                principal = user_principal(
                    user_id=user.id,
                    username=user.username,
                    role=user.role,  # type: ignore[arg-type]
                    session_id=resolved.session.id,
                    must_change_password=bool(user.must_change_password),
                    ip=ip,
                    ip_key=ip_key,
                )
                request.state.session_state = "active"

    request.state.principal = principal
    return principal


def current_path_format(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path_format", None) or getattr(route, "path", None) or request.url.path


def enforce(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
    """Route-level gate, mounted on every router via `include_router(dependencies=[…])`."""
    method = request.method.upper()
    path_format = current_path_format(request)
    policy = policy_for(method, path_format)
    if policy is None:
        raise PolicyMissing(route=f"{method} {path_format}")

    if principal.must_change_password and (method, path_format) not in PASSWORD_CHANGE_ALLOWED:
        raise PasswordChangeRequired()

    if not principal.can(policy.permission):
        if principal.kind == "guest":
            expired = getattr(request.state, "session_state", "none") == "expired"
            raise Unauthorized(reason="expired" if expired else "no_session")
        raise Forbidden(permission=policy.permission)

    if policy.rate and not (policy.rate.startswith("guest.") and principal.kind != "guest"):
        ratelimit.check(policy.rate, principal.ip_key or "unknown")
    return principal


def require(permission: str) -> Callable[..., Principal]:
    """A route-level dependency for the *second* check some routes need (e.g. `ask.deep`)."""

    def _dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(permission):
            if principal.kind == "guest":
                raise Unauthorized(reason="no_session")
            raise Forbidden(permission=permission)
        return principal

    return _dependency
