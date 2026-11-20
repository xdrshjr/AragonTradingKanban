"""Quota ledger and quota requests (spec §24.3.8, §24.6.1).

This milestone (M45) only **declares** the tables — the engine that writes them is M48.
The one writer that exists earlier is registration, which records the signup/invite
bonus as `reason='signup_bonus'|'invite_bonus'` rows.

`quota_ledger` is an *append-only ledger, not a counter*: every spend, refund and grant
is an immutable row, the balance is a `SUM`, and the partial unique index
`ux_quota_ref_reason` is what makes a refund idempotent (one deduction and at most one
refund per run/ask).

`ref_id` is `String(40)`, not the `VARCHAR(32)` the spec's DDL shows: ask ids are
`"ask_" + uuid4 hex` (36 chars). SQLite would not notice; Postgres would truncate or
refuse. Recorded in the plan's "实施过程发现的方案缺陷".
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

QUOTA_RESOURCES = ("report", "deep_ask")
QUOTA_SOURCES = ("daily", "bonus")
#: The reasons covered by `ux_quota_ref_reason` — one deduction and one refund per ref.
IDEMPOTENT_REASONS = ("run", "run_refund", "ask", "ask_refund")
LEDGER_REASONS = (
    "run",
    "run_refund",
    "ask",
    "ask_refund",
    "admin_grant",
    "admin_revoke",
    "request_approved",
    "signup_bonus",
    "invite_bonus",
)


class QuotaLedger(Base):
    __tablename__ = "quota_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: No FK: a user is anonymised, never deleted, and the ledger must outlive them.
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    resource: Mapped[str] = mapped_column(String(10), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(6), nullable=False)
    #: `YYYY-MM-DD` in `QUOTA_TZ`; bonus rows record the day they were granted.
    day_key: Mapped[str] = mapped_column(String(10), nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String(40))
    actor_id: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("resource IN ('report','deep_ask')", name="resource"),
        CheckConstraint("source IN ('daily','bonus')", name="source"),
        Index("ix_quota_user_daily", "user_id", "resource", "source", "day_key"),
        Index(
            "ux_quota_ref_reason",
            "ref_id",
            "reason",
            unique=True,
            sqlite_where=text("reason IN ('run','run_refund','ask','ask_refund')"),
            postgresql_where=text("reason IN ('run','run_refund','ask','ask_refund')"),
        ),
    )


class QuotaRequest(Base):
    __tablename__ = "quota_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    resource: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    granted: Mapped[int | None] = mapped_column(Integer)
    resolved_by: Mapped[str | None] = mapped_column(String(32))
    resolved_at: Mapped[str | None] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')", name="status"),
        # One pending request per user, enforced by the database.
        Index(
            "ux_quota_req_pending",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_quota_req_queue", "status", "created_at"),
    )
