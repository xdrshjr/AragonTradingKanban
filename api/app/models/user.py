"""Users, sessions, invite codes, the audit log and per-user watchlists (spec §24.6.1).

Conventions kept from the rest of the schema: ids are `uuid4().hex`, every time column
is `Text` holding `db.base.iso()`, booleans are `Integer` + `server_default="0"`.

Two things differ from a textbook design and are deliberate:

* **`username_lc` is the unique key**, not `username` — `username` keeps the casing the
  person typed, and an anonymised (deleted) user's `username_lc` becomes
  ``deleted:<id>`` so the name is free to register again.
* **There are no foreign keys from `invite_id` / `created_by`** (and the five columns the
  migration adds to old tables have none either): users are *anonymised*, never deleted,
  so a dangling reference cannot arise, and SQLite cannot add an FK to an existing table
  without the full-table rebuild the migration discipline forbids (spec §24.6.2).
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

USER_ROLES = ("root", "admin", "user")
USER_STATUSES = ("active", "disabled", "deleted")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(20), nullable=False)
    username_lc: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(40))
    #: `scrypt$15$8$1$salt$hash`; the tombstone of a deleted user is the single character `!`.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active", server_default="active")
    disabled_reason: Mapped[str | None] = mapped_column(String(200))
    disabled_at: Mapped[str | None] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    password_is_default: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    password_changed_at: Mapped[str | None] = mapped_column(Text)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[str | None] = mapped_column(Text)
    #: Already truncated (IPv4 /24, IPv6 /48).
    last_login_ip: Mapped[str | None] = mapped_column(String(48))
    #: `NULL` follows the role default; `-1` unlimited; `0` disabled; `N` per day.
    quota_daily_limit: Mapped[int | None] = mapped_column(Integer)
    quota_deep_daily_limit: Mapped[int | None] = mapped_column(Integer)
    invite_id: Mapped[str | None] = mapped_column(String(32))
    #: `NULL` = self-registered / bootstrapped; otherwise the creating admin's id.
    created_by: Mapped[str | None] = mapped_column(String(32))
    #: `sha256(normalize(recovery key))` hex; `NULL` = no key set (spec §25.3.3). Plaintext never stored.
    recovery_hash: Mapped[str | None] = mapped_column(String(64))
    recovery_set_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('root','admin','user')", name="role"),
        CheckConstraint("status IN ('active','disabled','deleted')", name="status"),
        Index("ux_users_username_lc", "username_lc", unique=True),
        # "At most one root" is a database fact (spec D-4), not a service-layer hope.
        Index(
            "ux_users_root",
            "role",
            unique=True,
            sqlite_where=text("role = 'root'"),
            postgresql_where=text("role = 'root'"),
        ),
        Index("ix_users_list", "created_at", "id"),
        Index("ix_users_status_role", "status", "role"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} {self.role} {self.status}>"


class UserSession(Base):
    __tablename__ = "sessions"

    #: The public "device handle" (listed in the account page) — **not** the cookie value.
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: `sha256(token)` hex. The raw token only ever exists in the browser's cookie.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    remember: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    idle_expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(48))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    revoked_at: Mapped[str | None] = mapped_column(Text)
    #: logout | password_change | admin | disabled | evicted | cli
    revoked_reason: Mapped[str | None] = mapped_column(String(24))

    __table_args__ = (
        Index("ux_sessions_token", "token_hash", unique=True),
        Index(
            "ix_sessions_user_live",
            "user_id",
            "created_at",
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # 0008: supports the two `purge_dead` sub-queries (spec §25.3.1b).
        Index(
            "ix_sessions_revoked",
            "revoked_at",
            sqlite_where=text("revoked_at IS NOT NULL"),
            postgresql_where=text("revoked_at IS NOT NULL"),
        ),
        Index("ix_sessions_idle", "idle_expires_at"),
    )


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: `XXXX-XXXX`, upper case, ambiguity-free alphabet.
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(80))
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bonus_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[str | None] = mapped_column(Text)
    disabled_at: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ux_invite_code", "code", unique=True),)


class AuditLog(Base):
    """Append-only (spec §24.3.10). Nothing in the HTTP surface updates or deletes a row."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(32))
    #: Snapshots: a rename or anonymisation later must not rewrite history.
    actor_name: Mapped[str | None] = mapped_column(String(40))
    actor_role: Mapped[str | None] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(16))
    target_id: Mapped[str | None] = mapped_column(String(32))
    target_name: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(48))
    request_id: Mapped[str | None] = mapped_column(String(16))
    detail_json: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_audit_at", "at", "id"),
        Index("ix_audit_action", "action", "at"),
        Index("ix_audit_actor", "actor_id", "at"),
    )


class UserWatchlistItem(Base):
    __tablename__ = "user_watchlist"

    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pinned: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    added_at: Mapped[str] = mapped_column(Text, nullable=False)
