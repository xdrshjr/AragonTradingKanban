"""Cookie sessions (spec §24.3.2): create, resolve, slide, revoke.

* The cookie holds a 256-bit random token; the database holds only `sha256(token)`
  (AC-155), so a leaked database is not a set of live sessions.
* `resolve()` joins `users` in the **same query**: a disabled/deleted user's sessions stop
  resolving on the very next request without anyone remembering to revoke them
  (AC-157), and a role change is visible immediately because the role is read fresh.
* `last_seen_at` is written at most once every 5 minutes (AC-162): under SQLite WAL a
  write per request would turn every page view into a writer-lock contender.
* At most `AUTH_MAX_SESSIONS_PER_USER` live sessions per user; creating one more evicts
  the oldest (`revoked_reason='evicted'`, AC-158).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso, parse_iso
from ..logging import get_logger
from ..models import User, UserSession
from ..security import clock

logger = get_logger("session_service")

#: `remember=false`: a browser-session cookie, and the server also expires it after 12 h.
SHORT_SESSION_HOURS = 12
#: Minimum gap between two `last_seen_at` writes for one session.
TOUCH_INTERVAL = timedelta(minutes=5)
USER_AGENT_MAX = 200

REVOKE_REASONS = ("logout", "password_change", "admin", "disabled", "evicted", "cli")


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    user: User
    session: UserSession


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_max_age_seconds(remember: bool) -> int | None:
    """`Max-Age` for the cookie; `None` (a browser-session cookie) when not remembering."""
    if not remember:
        return None
    return int(timedelta(days=get_settings().auth_session_abs_days).total_seconds())


def _expiries(remember: bool, moment: datetime) -> tuple[datetime, datetime]:
    """`(expires_at, idle_expires_at)` for a fresh session."""
    settings = get_settings()
    if remember:
        absolute = moment + timedelta(days=settings.auth_session_abs_days)
        idle = moment + timedelta(days=settings.auth_session_idle_days)
    else:
        absolute = moment + timedelta(hours=SHORT_SESSION_HOURS)
        idle = absolute
    return absolute, min(idle, absolute)


def live_sessions_query(user_id: str, moment: datetime):  # noqa: ANN201 - a SQLAlchemy `Select`
    stamp = iso(moment)
    return (
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > stamp,
            UserSession.idle_expires_at > stamp,
        )
        .order_by(UserSession.created_at, UserSession.id)
    )


def list_live(db: Session, user_id: str, now: datetime | None = None) -> list[UserSession]:
    return list(db.execute(live_sessions_query(user_id, now or clock.now())).scalars())


def create(
    db: Session,
    user: User,
    *,
    remember: bool,
    ip: str,
    user_agent: str | None,
    now: datetime | None = None,
) -> tuple[str, UserSession]:
    """Create a session, evicting the oldest ones over the per-user cap. Returns `(token, row)`.

    The caller commits. The raw token is returned **once**, to be put in the cookie.
    """
    moment = now or clock.now()
    cap = max(1, int(get_settings().auth_max_sessions_per_user))
    live = list_live(db, user.id, moment)
    # Make room for the one we are about to add.
    for victim in live[: max(0, len(live) - cap + 1)]:
        revoke(db, victim, "evicted", moment)
    absolute, idle = _expiries(remember, moment)
    token = new_token()
    row = UserSession(
        id=uuid.uuid4().hex,
        token_hash=hash_token(token),
        user_id=user.id,
        remember=int(remember),
        created_at=iso(moment),
        last_seen_at=iso(moment),
        idle_expires_at=iso(idle),
        expires_at=iso(absolute),
        ip=ip or None,
        user_agent=(user_agent or "")[:USER_AGENT_MAX] or None,
    )
    db.add(row)
    db.flush()
    return token, row


def resolve(db: Session, token: str, now: datetime | None = None) -> ResolvedSession | None:
    """Cookie token → `(user, session)`, or `None` for anything that is not a live session.

    Also performs the throttled sliding of `idle_expires_at` / `last_seen_at`.
    """
    if not token or len(token) > 128:
        return None
    moment = now or clock.now()
    stamp = iso(moment)
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == hash_token(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > stamp,
            UserSession.idle_expires_at > stamp,
            User.status == "active",
        )
    ).one_or_none()
    if row is None:
        return None
    session_row, user = row
    last_seen = parse_iso(session_row.last_seen_at) or moment
    if moment - last_seen >= TOUCH_INTERVAL:
        _touch(db, session_row, moment)
    return ResolvedSession(user=user, session=session_row)


def _touch(db: Session, session_row: UserSession, moment: datetime) -> None:
    settings = get_settings()
    absolute = parse_iso(session_row.expires_at) or moment
    window = (
        timedelta(days=settings.auth_session_idle_days)
        if session_row.remember
        else timedelta(hours=SHORT_SESSION_HOURS)
    )
    new_idle = min(moment + window, absolute)
    db.execute(
        update(UserSession)
        .where(UserSession.id == session_row.id)
        .values(last_seen_at=iso(moment), idle_expires_at=iso(new_idle))
    )
    session_row.last_seen_at = iso(moment)
    session_row.idle_expires_at = iso(new_idle)
    db.commit()


def revoke(db: Session, session_row: UserSession, reason: str, now: datetime | None = None) -> None:
    if session_row.revoked_at is None:
        session_row.revoked_at = iso(now or clock.now())
        session_row.revoked_reason = reason
        db.flush()


def revoke_by_token(db: Session, token: str, reason: str = "logout") -> UserSession | None:
    """Revoke the session a cookie token names (live or not). Returns it, or `None`."""
    if not token:
        return None
    row = db.execute(select(UserSession).where(UserSession.token_hash == hash_token(token))).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    revoke(db, row, reason)
    return row


def revoke_all_for_user(
    db: Session,
    user_id: str,
    *,
    reason: str,
    except_session_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Revoke every live session of a user (optionally sparing one). Returns the count."""
    moment = now or clock.now()
    count = 0
    for row in list_live(db, user_id, moment):
        if except_session_id and row.id == except_session_id:
            continue
        revoke(db, row, reason, moment)
        count += 1
    return count


def get_owned(db: Session, session_id: str, user_id: str) -> UserSession | None:
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return None
    return row


#: `purge_dead` 的批量参数（spec §25.3.1b）：每批一次 commit，单轮至多 `PURGE_BATCH * PURGE_MAX_BATCHES` 行。
PURGE_BATCH = 2000
#: 单轮（一次调用）最多 5 批，两条谓词共享这个预算。
PURGE_MAX_BATCHES = 5


def purge_dead(db: Session, now: datetime | None = None) -> int:
    """Delete sessions that have been dead for longer than `AUTH_SESSION_RETAIN_DAYS` (§25.3.1b).

    Two predicates, both index-backed (`ix_sessions_revoked` / `ix_sessions_idle`):

    * revoked more than the retention ago;
    * idle-expired more than the retention ago. `idle_expires_at <= expires_at` always holds
      (`_expiries`/`_touch` both take `min(..)`), so this also covers absolute expiry.

    Live sessions and *recently* dead ones (so "why was I signed out yesterday" is still
    answerable) are kept. `sessions` has no child table, so deleting is safe. Each batch is
    its own commit — the write lock is never held across batches. Returns the rows deleted.
    """
    moment = now or clock.now()
    cutoff = iso(moment - timedelta(days=max(1, get_settings().auth_session_retain_days)))
    total = 0
    batches = 0
    predicates = (
        (UserSession.revoked_at.is_not(None), UserSession.revoked_at < cutoff),
        (UserSession.idle_expires_at < cutoff,),
    )
    for conditions in predicates:
        while batches < PURGE_MAX_BATCHES:
            ids = select(UserSession.id).where(*conditions).limit(PURGE_BATCH)
            removed = db.execute(delete(UserSession).where(UserSession.id.in_(ids))).rowcount or 0
            db.commit()
            if removed:  # an empty probe does not spend the per-call budget
                batches += 1
            total += removed
            if removed < PURGE_BATCH:
                break
    if total:
        logger.info("会话清理：%s 条过期会话已删除", total)
    return total
