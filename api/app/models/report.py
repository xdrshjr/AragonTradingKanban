"""Report — one immutable version of a research document (spec §8.1)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex
    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ready|degraded|provisional|superseded
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    stance: Mapped[str | None] = mapped_column(String(16))  # buy|hold|sell|watch
    as_of: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[str] = mapped_column(Text, nullable=False)
    doc_json: Mapped[str] = mapped_column(Text, nullable=False)
    doc_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    grounded_ratio: Mapped[float | None] = mapped_column(Float)
    quality_json: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("runs.id", ondelete="SET NULL"))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_known: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    is_latest: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pinned: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    deleted_at: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("instrument_id", "version", name="uq_reports_instrument_id_version"),
        # "exactly one latest per instrument" is enforced by the database, not by
        # hope: the writer clears the flag and sets it again in one transaction.
        Index(
            "ux_reports_latest",
            "instrument_id",
            unique=True,
            sqlite_where=text("is_latest = 1"),
            postgresql_where=text("is_latest = 1"),
        ),
        Index("ix_reports_inst_ver", "instrument_id", "version"),
    )

    @property
    def doc(self) -> dict[str, Any]:
        return json.loads(self.doc_json)

    @property
    def quality(self) -> dict[str, Any]:
        return json.loads(self.quality_json) if self.quality_json else {}

    @property
    def provisional(self) -> bool:
        return self.status == "provisional"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Report {self.id} v{self.version} {self.status}>"
