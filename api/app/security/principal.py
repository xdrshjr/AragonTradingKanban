"""`Principal` — who is making this request (spec §24.3.1).

A plain frozen dataclass with **no HTTP dependency**, so `services/` can take one as
a parameter (quota reservation, audit) without importing FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from .permissions import (
    ALL_PERMISSIONS,
    GUEST_PERMISSIONS,
    SERVICE_PERMISSIONS,
    expand_role,
    permission_satisfied,
)

PrincipalKind = Literal["guest", "user", "service", "system"]
Role = Literal["guest", "user", "admin", "root", "service", "system"]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    user_id: str | None
    username: str | None
    role: Role
    permissions: frozenset[str]
    session_id: str | None = None
    must_change_password: bool = False
    #: Already truncated for storage (IPv4 /24, IPv6 /48). Never the raw address.
    ip: str = ""
    #: Rate-limit bucket key (IPv4 exact / IPv6 /64). Kept apart from `ip` (RV-13).
    ip_key: str = field(default="", compare=False)
    #: `AUTH_MODE=open`: a `service` principal with the *full* vocabulary (spec §24.3.3
    #: step 1) — the equivalent of the old "local-first" behaviour, not the narrowed one.
    open_mode: bool = field(default=False, compare=False)

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "root", "service")

    @property
    def is_root(self) -> bool:
        return self.role == "root"

    def can(self, permission: str) -> bool:
        if permission == "public":
            return True
        return permission_satisfied(self.permissions, permission)

    def with_ip(self, ip: str, ip_key: str) -> Principal:
        return replace(self, ip=ip, ip_key=ip_key)


GUEST = Principal(
    kind="guest",
    user_id=None,
    username=None,
    role="guest",
    permissions=GUEST_PERMISSIONS,
)

#: Scheduler / sweeper actor: no request, no quota, `created_by` comes from the schedule owner.
SYSTEM = Principal(
    kind="system",
    user_id=None,
    username=None,
    role="system",
    permissions=ALL_PERMISSIONS,
)


def user_principal(
    *,
    user_id: str,
    username: str,
    role: Literal["user", "admin", "root"],
    session_id: str | None,
    must_change_password: bool,
    ip: str = "",
    ip_key: str = "",
) -> Principal:
    return Principal(
        kind="user",
        user_id=user_id,
        username=username,
        role=role,
        permissions=expand_role(role),
        session_id=session_id,
        must_change_password=must_change_password,
        ip=ip,
        ip_key=ip_key,
    )


def service_principal(*, ip: str = "", ip_key: str = "", open_mode: bool = False) -> Principal:
    """The API-token principal. `open_mode` widens it to the whole vocabulary."""
    return Principal(
        kind="service",
        user_id=None,
        username="service",
        role="service",
        permissions=ALL_PERMISSIONS if open_mode else SERVICE_PERMISSIONS,
        ip=ip,
        ip_key=ip_key,
        open_mode=open_mode,
    )
