"""Role → permission expansion from `contracts/permissions.json` (spec §24.3.4).

The JSON file is the single source of truth shared with the TypeScript side
(`web/lib/permissions.ts`); this module only expands the `@role` inheritance into
`frozenset`s. `tests/test_contract_permissions.py` pins both directions.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

#: `app/security/permissions.py` → repo root is three parents up (`api/app/security`).
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "permissions.json"

API_PREFIX = "/api/v1"

#: The only three routes a `must_change_password` session may reach (spec D-2, §24.3.3).
#: Keys carry the `/api/v1` prefix, exactly like the policy table's.
PASSWORD_CHANGE_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", f"{API_PREFIX}/auth/password"),
        ("GET", f"{API_PREFIX}/auth/me"),
        ("POST", f"{API_PREFIX}/auth/logout"),
    }
)

#: A permission that **implies** another for the purpose of a route's coarse gate.
#:
#: `service` has `report.manage_any` but (by §24.3.4) not `report.manage_own`, while
#: `PATCH/DELETE /reports/{id}` are gated on `report.manage_own` (Appendix A). Without
#: this the service principal — whose whole point is "may curate reports" — would be
#: refused at the door. Recorded as a spec defect in the plan's last section.
IMPLIED_BY: dict[str, frozenset[str]] = {
    "report.manage_own": frozenset({"report.manage_any"}),
}


@lru_cache(maxsize=1)
def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _expand(name: str, roles: dict[str, list[str]], seen: tuple[str, ...] = ()) -> set[str]:
    if name in seen:  # pragma: no cover - the contract test would already be red
        raise ValueError(f"permissions.json: inheritance cycle through {name!r}")
    result: set[str] = set()
    for entry in roles[name]:
        if entry.startswith("@"):
            result |= _expand(entry[1:], roles, (*seen, name))
        else:
            result.add(entry)
    return result


@lru_cache(maxsize=1)
def _expanded() -> dict[str, frozenset[str]]:
    contract = load_contract()
    roles: dict[str, list[str]] = contract["roles"]
    table = {name: frozenset(_expand(name, roles)) for name in roles}
    service = contract["service"]
    table["service"] = frozenset(_expand(service["inherits"], roles) - set(service["minus"]))
    return table


def _all_permissions() -> frozenset[str]:
    merged: set[str] = set()
    for permissions in _expanded().values():
        merged |= permissions
    return frozenset(merged)


#: Every string in the vocabulary — `AUTH_MODE=open` and `system` hold all of it.
ALL_PERMISSIONS: frozenset[str] = _all_permissions()


def expand_role(role: str) -> frozenset[str]:
    """The permission set of `guest | user | admin | root | service`."""
    table = _expanded()
    if role in table:
        return table[role]
    if role == "system":
        return ALL_PERMISSIONS
    return frozenset()


def permission_satisfied(held: frozenset[str], required: str) -> bool:
    if required in held:
        return True
    return bool(IMPLIED_BY.get(required, frozenset()) & held)


GUEST_PERMISSIONS: frozenset[str] = expand_role("guest")
SERVICE_PERMISSIONS: frozenset[str] = expand_role("service")


def vocabulary() -> frozenset[str]:
    return ALL_PERMISSIONS
