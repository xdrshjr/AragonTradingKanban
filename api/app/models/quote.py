"""Quote — the latest price snapshot, one row per instrument (spec §8.1)."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Quote(Base):
    __tablename__ = "quotes"

    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True
    )
    last: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    day_high: Mapped[float | None] = mapped_column(Float)
    day_low: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(24))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
