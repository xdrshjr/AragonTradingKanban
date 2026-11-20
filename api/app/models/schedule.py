"""Schedule — one cron trigger per instrument, with an in-DB lease (§18.6.1).

The lease columns (`claimed_by` / `claimed_at`) are what make `tick_claim()`
idempotent: `UPDATE … WHERE claimed_by IS NULL` is database-level mutual
exclusion, which an `asyncio.Lock` could never provide from inside `to_thread`
(★V4-P0-2).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # "sch_" + uuid4 hex
    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    cron: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    min_age_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=12, server_default="12")
    next_run_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("runs.id", ondelete="SET NULL"))
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[str | None] = mapped_column(Text)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    #: ★v15（0007_users_auth / §24.6.2）：创建者；ticker 触发的 run 以它作 `created_by`。
    owner_id: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        # "at most one enabled schedule per instrument" is a DB fact, not a hope.
        Index(
            "ux_schedules_inst_enabled",
            "instrument_id",
            unique=True,
            sqlite_where=text("enabled = 1"),
            postgresql_where=text("enabled = 1"),
        ),
        Index("ix_schedules_due", "next_run_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Schedule {self.id} {self.instrument_id} {self.cron} enabled={self.enabled}>"
