"""Short-lived HMAC signatures for SSE query strings (★V4-P1-4).

`EventSource` cannot set an `Authorization` header (★P1-3), so a stream validates
a 60-second `?token=` instead. The signature is bound to **whatever id the stream
belongs to** — a run id *or* an ask id — which is why the parameter is
`bound_id` and not `run_id`.

This module is the only place `sign`/`verify` exist. `routers/runs.py` and
`routers/asks.py` both call it, which also satisfies the §18.3.1 dependency rule
(a router must not import another router).

An empty `API_TOKEN` means local-first: verification is a no-op and the issued
token is a harmless placeholder so the client keeps one code path.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from ..config import get_settings

TOKEN_TTL_SEC = 60
OPEN_SUFFIX = "open"


def sign_sse_token(bound_id: str, expires_at: int) -> str:
    settings = get_settings()
    secret = settings.api_token.encode("utf-8")
    payload = f"{bound_id}.{expires_at}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def issue_sse_token(bound_id: str) -> tuple[str, int]:
    """`(token, expiresIn)` for `POST .../events-token`."""
    settings = get_settings()
    expires_at = int(time.time()) + TOKEN_TTL_SEC
    if not settings.api_token:
        return f"{expires_at}.{OPEN_SUFFIX}", TOKEN_TTL_SEC
    return f"{expires_at}.{sign_sse_token(bound_id, expires_at)}", TOKEN_TTL_SEC


def verify_sse_token(bound_id: str, token: str) -> bool:
    """`<expiresAt>.<signature>` — checked once, never stored."""
    settings = get_settings()
    if not settings.api_token:
        return True
    try:
        expires_raw, signature = token.split(".", 1)
        expires_at = int(expires_raw)
    except (ValueError, AttributeError):
        return False
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(signature, sign_sse_token(bound_id, expires_at))


__all__ = ["TOKEN_TTL_SEC", "issue_sse_token", "sign_sse_token", "verify_sse_token"]
