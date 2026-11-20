"""Users: create, look up, change state, anonymise (spec §24.3.1, §24.3.13, §24.3.14).

This module owns the **row-level mechanics** and the username rules. Who is *allowed* to
do what to whom is `security/objects.py::assert_can_manage_user` (pure, HTTP-free); the
admin orchestration that combines the two arrives with M50.

Nothing here commits — callers own the transaction, so "create the user + redeem the
invite + write the bonus + audit" is one atomic unit (spec §24.3.6 step 6).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.base import iso, parse_iso
from ..errors import InvalidUsername, UsernameTaken
from ..models import User
from ..security import clock

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
USERNAME_MIN, USERNAME_MAX = 3, 20

#: Reserved words, **and** any of them followed only by digits (`admin01`, `root2`).
RESERVED_WORDS: tuple[str, ...] = (
    "admin",
    "root",
    "system",
    "aragon",
    "support",
    "official",
    "api",
    "null",
    "undefined",
    "jdqqjr",
)
_RESERVED_PATTERN = re.compile(r"^(?:" + "|".join(RESERVED_WORDS) + r")\d*$")


def new_id() -> str:
    return uuid.uuid4().hex


def username_lc(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str, *, allow_reserved: bool = False) -> None:
    """Raise `422 invalid_username(detail.rule)` unless the name is registrable.

    `allow_reserved` is for the bootstrap root only: its name (`jdqqjr`) is itself on the
    reserved list *because* it is taken.
    """
    if not USERNAME_PATTERN.fullmatch(username):
        rule = "length" if not (USERNAME_MIN <= len(username) <= USERNAME_MAX) else "charset"
        raise InvalidUsername(detail={"rule": rule})
    if not allow_reserved and _RESERVED_PATTERN.fullmatch(username.lower()):
        raise InvalidUsername(detail={"rule": "reserved"})


def get_by_id(db: Session, user_id: str | None) -> User | None:
    if not user_id:
        return None
    return db.get(User, user_id)


def get_by_username(db: Session, username: str) -> User | None:
    return db.execute(select(User).where(User.username_lc == username_lc(username))).scalar_one_or_none()


def get_root(db: Session) -> User | None:
    return db.execute(select(User).where(User.role == "root")).scalar_one_or_none()


def create_user(
    db: Session,
    *,
    username: str,
    password_hash: str,
    role: str = "user",
    display_name: str | None = None,
    created_by: str | None = None,
    invite_id: str | None = None,
    must_change_password: bool = False,
    password_is_default: bool = False,
    allow_reserved: bool = False,
    now: datetime | None = None,
) -> User:
    """Insert and `flush` a user. Raises `InvalidUsername` / `UsernameTaken`.

    The pre-check gives the common case a clean error; the unique index is still the
    arbiter for the race (two registrations of the same name), which surfaces as an
    `IntegrityError` on flush.
    """
    validate_username(username, allow_reserved=allow_reserved)
    if get_by_username(db, username) is not None:
        raise UsernameTaken()
    stamp = iso(now or clock.now())
    user = User(
        id=new_id(),
        username=username,
        username_lc=username_lc(username),
        display_name=display_name,
        password_hash=password_hash,
        role=role,
        status="active",
        must_change_password=int(must_change_password),
        password_is_default=int(password_is_default),
        password_changed_at=stamp,
        failed_logins=0,
        invite_id=invite_id,
        created_by=created_by,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if "username_lc" in str(exc.orig):
            raise UsernameTaken() from exc
        raise
    return user


# ── login bookkeeping ────────────────────────────────────────────────────────

LOCK_THRESHOLD = 5
LOCK_MINUTES = 15


def is_locked(user: User, now: datetime | None = None) -> bool:
    until = parse_iso(user.locked_until)
    return until is not None and until > (now or clock.now())


def locked_until_epoch(user: User) -> float | None:
    until = parse_iso(user.locked_until)
    return until.timestamp() if until else None


def record_login_failure(db: Session, user: User, now: datetime | None = None) -> None:
    """`failed_logins += 1`; the failure that reaches the threshold sets `locked_until`."""
    moment = now or clock.now()
    user.failed_logins = int(user.failed_logins or 0) + 1
    if user.failed_logins >= LOCK_THRESHOLD:
        user.locked_until = iso(moment + timedelta(minutes=LOCK_MINUTES))
    user.updated_at = iso(moment)
    db.flush()


def record_login_success(db: Session, user: User, ip: str, now: datetime | None = None) -> None:
    moment = now or clock.now()
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = iso(moment)
    user.last_login_ip = ip or None
    user.updated_at = iso(moment)
    db.flush()


def unlock(db: Session, user: User, now: datetime | None = None) -> None:
    user.failed_logins = 0
    user.locked_until = None
    user.updated_at = iso(now or clock.now())
    db.flush()


# ── state changes ────────────────────────────────────────────────────────────


def set_password(
    db: Session,
    user: User,
    password_hash: str,
    *,
    must_change: bool = False,
    is_default: bool = False,
    now: datetime | None = None,
) -> None:
    stamp = iso(now or clock.now())
    user.password_hash = password_hash
    user.must_change_password = int(must_change)
    user.password_is_default = int(is_default)
    user.password_changed_at = stamp
    user.updated_at = stamp
    db.flush()


def set_display_name(db: Session, user: User, display_name: str | None) -> None:
    user.display_name = display_name
    user.updated_at = iso(clock.now())
    db.flush()


def disable(db: Session, user: User, reason: str | None = None, now: datetime | None = None) -> None:
    stamp = iso(now or clock.now())
    user.status = "disabled"
    user.disabled_reason = (reason or "")[:200] or None
    user.disabled_at = stamp
    user.updated_at = stamp
    db.flush()


def enable(db: Session, user: User, now: datetime | None = None) -> None:
    user.status = "active"
    user.disabled_reason = None
    user.disabled_at = None
    user.updated_at = iso(now or clock.now())
    db.flush()


def set_role(db: Session, user: User, role: str, now: datetime | None = None) -> None:
    user.role = role
    user.updated_at = iso(now or clock.now())
    db.flush()


def anonymize(db: Session, user: User, now: datetime | None = None) -> None:
    """Delete = tombstone (spec §24.3.1): the id lives on so reports, ledger rows and audit
    entries keep a valid referent, but the name is free to register again and no password
    can ever match (`!` is not a parseable hash)."""
    stamp = iso(now or clock.now())
    user.username = f"deleted_{user.id[:8]}"
    user.username_lc = f"deleted:{user.id}"
    user.display_name = None
    user.password_hash = "!"
    user.status = "deleted"
    user.must_change_password = 0
    user.password_is_default = 0
    user.failed_logins = 0
    user.locked_until = None
    user.updated_at = stamp
    db.flush()
