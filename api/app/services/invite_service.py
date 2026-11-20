"""Invite codes: generate, atomically redeem, revoke (spec §24.3.6 step 6, §24.5.3).

A code is `XXXX-XXXX` over the same ambiguity-free alphabet as the captcha. Redemption is
a single guarded `UPDATE … SET used_count = used_count + 1 WHERE used_count < max_uses AND
(expires_at IS NULL OR expires_at > :now) AND disabled_at IS NULL` — the **only** correct
way to stop N concurrent registrations from over-spending a code: the read-then-write
version lets all N through. `rowcount == 0` means "no longer redeemable".

An invite can never grant a role: users registered with one are always `role='user'`
(AC-177).
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.base import iso
from ..errors import InviteInvalid
from ..models import InviteCode
from ..security import clock

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_PATTERN = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")

MAX_USES_RANGE = (1, 100)
EXPIRES_DAYS_RANGE = (1, 90)
BONUS_RANGE = (0, 20)


def normalize_code(raw: str | None) -> str:
    """Upper-case, strip, and re-insert the hyphen if it was left out (`k7qmx2pd` → `K7QM-X2PD`)."""
    code = (raw or "").strip().upper().replace(" ", "")
    if len(code) == 8 and "-" not in code:
        code = f"{code[:4]}-{code[4:]}"
    return code


def looks_like_code(code: str) -> bool:
    return bool(_CODE_PATTERN.fullmatch(code))


def generate_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{body[:4]}-{body[4:]}"


def create_invite(
    db: Session,
    *,
    created_by: str | None,
    max_uses: int = 1,
    expires_in_days: int | None = None,
    bonus_credits: int = 0,
    note: str | None = None,
    now: datetime | None = None,
) -> InviteCode:
    """Insert and flush an invite (caller commits). Retries on the (astronomically unlikely)
    code collision."""
    moment = now or clock.now()
    expires_at = iso(moment + timedelta(days=expires_in_days)) if expires_in_days else None
    for _attempt in range(5):
        invite = InviteCode(
            id=uuid.uuid4().hex,
            code=generate_code(),
            note=(note or "")[:80] or None,
            max_uses=max_uses,
            used_count=0,
            bonus_credits=bonus_credits,
            expires_at=expires_at,
            created_by=created_by,
            created_at=iso(moment),
        )
        try:
            with db.begin_nested():
                db.add(invite)
                db.flush()
        except IntegrityError:
            continue
        return invite
    raise RuntimeError("could not generate a unique invite code")  # pragma: no cover


def find(db: Session, code: str) -> InviteCode | None:
    normalized = normalize_code(code)
    if not looks_like_code(normalized):
        return None
    return db.execute(select(InviteCode).where(InviteCode.code == normalized)).scalar_one_or_none()


def is_redeemable(invite: InviteCode, now: datetime | None = None) -> bool:
    stamp = iso(now or clock.now())
    return (
        invite.disabled_at is None
        and invite.used_count < invite.max_uses
        and (invite.expires_at is None or invite.expires_at > stamp)
    )


def redeem(db: Session, code: str, now: datetime | None = None) -> InviteCode:
    """Atomically consume one use. Raises `InviteInvalid` when the code does not exist, is
    expired, revoked or spent. The caller owns the transaction (so a later failure in the
    same registration rolls the use back)."""
    invite = find(db, code)
    if invite is None:
        raise InviteInvalid()
    stamp = iso(now or clock.now())
    result = db.execute(
        update(InviteCode)
        .where(
            InviteCode.id == invite.id,
            InviteCode.used_count < InviteCode.max_uses,
            InviteCode.disabled_at.is_(None),
            (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at > stamp),
        )
        .values(used_count=InviteCode.used_count + 1)
    )
    if not result.rowcount:
        raise InviteInvalid()
    db.refresh(invite)
    return invite


def revoke(db: Session, invite: InviteCode, now: datetime | None = None) -> None:
    if invite.disabled_at is None:
        invite.disabled_at = iso(now or clock.now())
        db.flush()


# ── admin listing ────────────────────────────────────────────────────────────


def status_of(invite: InviteCode, now: datetime | None = None) -> str:
    """`revoked | expired | exhausted | active` — derived once, server-side, for the admin table."""
    if invite.disabled_at is not None:
        return "revoked"
    if invite.expires_at is not None and invite.expires_at <= iso(now or clock.now()):
        return "expired"
    if invite.used_count >= invite.max_uses:
        return "exhausted"
    return "active"


def mask(code: str) -> str:
    """`K7QM-X2PD` → `K7QM-****` for audit rows (the full code is a registration credential)."""
    return f"{code[:4]}-****"


def list_all(db: Session, limit: int = 200) -> list[InviteCode]:
    statement = select(InviteCode).order_by(InviteCode.created_at.desc(), InviteCode.id.desc()).limit(limit)
    return list(db.execute(statement).scalars())
