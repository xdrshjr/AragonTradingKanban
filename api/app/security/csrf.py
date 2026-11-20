"""Origin check for state-changing requests (spec §24.3.2(b), RV-2).

`SameSite=Lax` stops cross-*site* POSTs, but `localhost:3000` and `localhost:8000` are the
same *site* (the port is not part of it), so another local page could ride the cookie.
Hence an Origin check on POST / PUT / PATCH / DELETE:

1. `Origin` **absent** → allow (curl, server-side scripts, old same-origin browsers are
   not a CSRF vector). `Origin: null` (sandboxed iframe, `file://`) → **reject**.
2. Otherwise allow when **any** of: the full origin is in `ALLOW_ORIGINS`; the origin's
   netloc equals the request's `Host`; with `TRUST_PROXY=1`, its netloc equals the first
   `X-Forwarded-Host`.
3. **The scheme is never compared** (RV-2, a P0): production is browser `https://…` → nginx
   (TLS terminates) → uvicorn plain `http`, so the app sees `scheme=http` while `Origin`
   is `https://…`, and `ALLOW_ORIGINS` is empty (same origin). Comparing full origins
   would 403 *every* production write, login included. Dropping the scheme costs no
   protection: an attacker page's Origin has a different **host**.
4. A request carrying the correct service Bearer skips the check — it does not use the
   cookie, so it cannot be a CSRF victim.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from ..errors import CsrfRejected

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _netloc(origin: str) -> str:
    """`https://Example.com:443` → `example.com` (default ports dropped, lower-cased)."""
    parts = urlsplit(origin if "://" in origin else f"//{origin}")
    host = (parts.hostname or "").lower()
    port = parts.port
    if parts.hostname and ":" in parts.hostname:  # IPv6 literal
        host = f"[{host}]"
    if port is None or str(port) == _DEFAULT_PORTS.get(parts.scheme or "", ""):
        return host
    return f"{host}:{port}"


def _normalise_host_header(value: str) -> str:
    return _netloc(value.strip())


def check_origin(
    method: str,
    headers: Mapping[str, str],
    *,
    allowed_origins: list[str],
    trust_proxy: bool,
    service_bearer_ok: bool = False,
) -> None:
    """Raise `CsrfRejected` for an untrusted browser write; return silently otherwise."""
    if method.upper() not in UNSAFE_METHODS or service_bearer_ok:
        return
    origin = headers.get("origin")
    if origin is None:
        return
    origin = origin.strip()
    if origin == "" or origin.lower() == "null":
        raise CsrfRejected(origin=origin or "null")

    if origin.rstrip("/") in {item.rstrip("/") for item in allowed_origins}:
        return

    origin_netloc = _netloc(origin)
    host = headers.get("host")
    if host and origin_netloc == _normalise_host_header(host):
        return
    if trust_proxy:
        forwarded = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
        if forwarded and origin_netloc == _normalise_host_header(forwarded):
            return
    raise CsrfRejected(origin=origin[:200])
