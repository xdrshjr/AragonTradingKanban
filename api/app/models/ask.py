"""ReportAsk + AskEvent — one question about a report and its replayable log (§18.6.1).

An ask is deliberately **not** a run (★V4-P1-5 / C-12): `run_id` stays NULL, the
event log is `ask_events`, and the live task lives in `AskRegistry`. Creating a
fake `runs` row would pollute the generation audit trail and collide with
`ux_runs_active`.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ..logging import get_logger

logger = get_logger("ask_model")

#: `queued` exists only for the instant between INSERT and the task starting.
ASK_ACTIVE_STATUSES = ("queued", "running", "cancelling")
ASK_TERMINAL_STATUSES = ("done", "failed", "cancelled")

ANSWER_MODES = ("cite", "deep")


class ReportAsk(Base):
    __tablename__ = "report_asks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # "ask_" + uuid4 hex
    report_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # cite | deep
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    answer_json: Mapped[str | None] = mapped_column(Text)
    grounded: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confidence: Mapped[float | None] = mapped_column(Float)
    citations_dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Reserved by §18.6.1 and always NULL in practice — kept so the column shape
    #: matches the design; `AskOut` does not expose it (V4-P1-5).
    run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("runs.id", ondelete="SET NULL"))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_known: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    finished_at: Mapped[str | None] = mapped_column(Text)
    #: ★v15（0007_users_auth / §24.6.2）：提问者。`NULL` = 历史追问，仅管理员可见。
    user_id: Mapped[str | None] = mapped_column(String(32))
    context_ask_ids_json: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Covers both the version list and the "24h deep quota" query
        # (`WHERE report_id=? AND mode='deep' AND created_at>=?`) — the separate
        # `ix_asks_mode_created` of v3 was redundant (V4-P2-9).
        Index("ix_asks_report_created", "report_id", "created_at"),
        Index("ix_asks_user", "user_id", "report_id"),
    )

    @property
    def context_ask_ids(self) -> list[str]:
        """Read legacy NULL as empty; log malformed context without its content."""
        if self.context_ask_ids_json is None:
            return []
        try:
            ids = json.loads(self.context_ask_ids_json)
            valid = isinstance(ids, list) and len(ids) <= 6
            valid = valid and all(
                isinstance(item, str) and item.strip() and len(item) <= 64
                for item in ids
            )
            if valid and len(set(ids)) == len(ids):
                return ids
        except (json.JSONDecodeError, TypeError):
            pass
        logger.warning("ask_context_invalid id=%s", self.id)
        return []

    @property
    def answer(self) -> dict[str, Any]:
        if not self.answer_json:
            return {}
        try:
            payload = json.loads(self.answer_json)
        except json.JSONDecodeError:  # pragma: no cover - defensive
            return {}
        return payload if isinstance(payload, dict) else {}

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ReportAsk {self.id} {self.mode} {self.status}>"


class AskEvent(Base):
    __tablename__ = "ask_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ask_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("report_asks.id", ondelete="CASCADE"), nullable=False
    )
    #: Per-ask sequence, mirroring `run_events.seq` (§18.5.2).
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ux_ask_events_seq", "ask_id", "seq", unique=True),)

    @property
    def payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError:  # pragma: no cover - defensive
            return {}
        return payload if isinstance(payload, dict) else {}
