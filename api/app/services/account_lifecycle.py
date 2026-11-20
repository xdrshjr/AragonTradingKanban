"""Account deletion — the one implementation of its **side effects** (spec §25.3.4, D-12).

Two callers, two different *authorisations*, one set of effects:

* admin / root: `admin_user_service.delete_user` runs `assert_can_manage_user(…, "delete")`
  (the matrix of §24.3.14) and then calls `delete_account(…, via="admin")`;
* the person themself: `auth_service.delete_own_account` runs `objects.assert_can_self_delete`
  (the matrix would answer `422 self_action_forbidden` for "delete myself", by design) and then
  calls `delete_account(…, via="self")`.

Authorisation is the **caller's** job; this function trusts it. Effects (A-4): revoke every
session, empty the personal watch-list, reject pending quota requests ("账号已注销" — otherwise
they would sit in the admin queue forever), tombstone the row (`anonymize`: the username is
free to register again, no password can match), forget the recovery key, and audit. The quota
ledger and the audit log are kept.

Does not commit; the caller does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..db.base import iso
from ..models import QuotaRequest, User, UserWatchlistItem
from ..security import clock
from ..security.principal import Principal
from . import audit_service, session_service, user_service
from .audit_service import AuditTarget

Via = Literal["self", "admin"]

#: `via` → (session `revoked_reason`, audit action). Self-deletes are told apart in the log.
_VIA: dict[str, tuple[str, str]] = {
    "admin": ("admin", "user.delete"),
    "self": ("self_delete", "user.self_delete"),
}


def delete_account(db: Session, actor: Principal, target: User, *, via: Via, now: datetime | None = None) -> int:
    """Tombstone `target` and clean up after it. Returns the number of sessions revoked."""
    moment = now or clock.now()
    revoked_reason, action = _VIA[via]
    # Snapshot the name for the audit row *before* the tombstone rewrites it.
    snapshot = AuditTarget("user", target.id, target.username)
    revoked = session_service.revoke_all_for_user(db, target.id, reason=revoked_reason, now=moment)
    db.execute(UserWatchlistItem.__table__.delete().where(UserWatchlistItem.user_id == target.id))
    db.execute(
        update(QuotaRequest)
        .where(QuotaRequest.user_id == target.id, QuotaRequest.status == "pending")
        .values(
            status="rejected",
            resolved_by=actor.user_id,
            resolved_at=iso(moment),
            resolution_note="账号已注销",
        )
    )
    user_service.anonymize(db, target, moment)
    target.recovery_hash = None
    target.recovery_set_at = None
    db.flush()
    audit_service.record(db, actor, action, snapshot, {"revokedSessions": revoked}, now=moment)
    return revoked
