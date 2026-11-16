"""Run + RunEvent — generation attempts and their replayable event log (§8.1)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ACTIVE_STATUSES = ("queued", "running", "cancelling")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "timeout", "budget_exceeded")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    # ★P1-1: circular FK with reports.run_id. Written in this order —
    # run(report_id=NULL) -> report(run_id=run) -> UPDATE run.report_id —
    # and SET NULL on delete so removing a report never orphans the audit trail.
    report_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("reports.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    stage: Mapped[str | None] = mapped_column(String(16))
    pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pid: Mapped[int | None] = mapped_column(Integer)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(Text)
    aragon_session_id: Mapped[str | None] = mapped_column(String(64))
    workspace_path: Mapped[str | None] = mapped_column(Text)
    log_path: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_known: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reason: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        # One live run per instrument — including the `cancelling` hand-over state
        # (★P1-9), otherwise a second POST sneaks in during the kill window.
        Index(
            "ux_runs_active",
            "instrument_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running','cancelling')"),
            postgresql_where=text("status IN ('queued','running','cancelling')"),
        ),
        Index("ix_runs_instrument_started", "instrument_id", "started_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Run {self.id} {self.status} {self.stage} {self.pct}%>"


class RunEvent(Base):
    __tablename__ = "run_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_events_run", "run_id", "seq"),)

    @property
    def payload(self) -> dict[str, Any]:
        try:
            return json.loads(self.payload_json)
        except json.JSONDecodeError:  # pragma: no cover - defensive
            return {}
