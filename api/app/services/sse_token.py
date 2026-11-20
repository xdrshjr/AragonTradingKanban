"""Short-lived HMAC signatures for SSE query strings (★V4-P1-4).

`EventSource` cannot set an `Authorization` header (★P1-3), so a stream validates
a 60-second `?token=` instead. The signature is bound to **whatever id the stream
belongs to** — a run id *or* an ask id — which is why the parameter is
`bound_id` and not `run_id`.

This module is the only place `sign`/`verify` exist. `routers/runs.py` and
`routers/asks.py` both call it, which also satisfies the §18.3.1 dependency rule
(a router must not import another router).

★v15（§24.3.11）：多用户之后 `deep` 追问的事件流含用户私有内容，所以令牌**永远真校验**
——除了 `AUTH_MODE=open`（旧"本地优先"行为：占位令牌、校验恒真）。变化只有两处：

* 签名密钥从 `API_TOKEN` 换成 `signing_secret()`（`SESSION_SECRET`，或首次启动生成并落库
  `settings["auth.session_secret"]` 的随机值）。`API_TOKEN` 不再参与签名——它现在是
  服务主体凭据，不该同时是 HMAC 密钥。
* `verify` 不再因 `API_TOKEN` 为空而恒真；`AUTH_MODE` 在**请求时**读取。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from sqlalchemy.orm import Session

from ..config import get_settings
from . import settings_service

TOKEN_TTL_SEC = 60
OPEN_SUFFIX = "open"

#: Filled by `load_or_create_secret()` at startup. When nothing ever called it (a bare
#: unit test), a per-process random value is used — still unguessable, just not stable
#: across restarts, which only matters for tokens that live 60 seconds anyway.
_loaded_secret: str | None = None
_ephemeral_secret: str = secrets.token_hex(32)


def load_or_create_secret(db: Session) -> str:
    """Resolve the signing secret at startup: env first, else the persisted one (created
    on first use). The caller commits."""
    global _loaded_secret
    configured = get_settings().session_secret.strip()
    if configured:
        _loaded_secret = configured
        return configured
    stored = settings_service.get_str(db, settings_service.KEY_SESSION_SECRET, default="")
    if not stored:
        stored = secrets.token_hex(32)
        inserted = settings_service.insert_if_absent(db, settings_service.KEY_SESSION_SECRET, stored)
        if not inserted:  # lost a race with another starter: use whatever won
            stored = settings_service.get_str(db, settings_service.KEY_SESSION_SECRET, default=stored) or stored
    _loaded_secret = stored
    return stored


def reset_secret_cache() -> None:
    """Test hygiene."""
    global _loaded_secret
    _loaded_secret = None


def signing_secret() -> str:
    configured = get_settings().session_secret.strip()
    return configured or _loaded_secret or _ephemeral_secret


def _is_open() -> bool:
    return get_settings().auth_mode == "open"


def sign_sse_token(bound_id: str, expires_at: int) -> str:
    payload = f"{bound_id}.{expires_at}".encode()
    return hmac.new(signing_secret().encode("utf-8"), payload, hashlib.sha256).hexdigest()


def issue_sse_token(bound_id: str) -> tuple[str, int]:
    """`(token, expiresIn)` for `POST .../events-token`."""
    expires_at = int(time.time()) + TOKEN_TTL_SEC
    if _is_open():
        return f"{expires_at}.{OPEN_SUFFIX}", TOKEN_TTL_SEC
    return f"{expires_at}.{sign_sse_token(bound_id, expires_at)}", TOKEN_TTL_SEC


def verify_sse_token(bound_id: str, token: str) -> bool:
    """`<expiresAt>.<signature>` — checked once, never stored."""
    if _is_open():
        return True
    try:
        expires_raw, signature = token.split(".", 1)
        expires_at = int(expires_raw)
    except (ValueError, AttributeError):
        return False
    if expires_at < int(time.time()):
        return False
    # Bytes, not `str`: `compare_digest` raises `TypeError` on a non-ASCII `str`, which
    # would turn a garbage `?token=签名` into a 500 instead of a 401.
    return hmac.compare_digest(signature.encode("utf-8"), sign_sse_token(bound_id, expires_at).encode("utf-8"))


__all__ = [
    "TOKEN_TTL_SEC",
    "issue_sse_token",
    "load_or_create_secret",
    "sign_sse_token",
    "signing_secret",
    "verify_sse_token",
]
