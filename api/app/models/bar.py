"""Bar — OHLCV history, composite primary key (spec §8.1)."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class Bar(Base):
    __tablename__ = "bars"

    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True
    )
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True, default="1d")
    ts: Mapped[int] = mapped_column(Integer, primary_key=True)  # UTC epoch seconds
    adjust: Mapped[str] = mapped_column(String(8), primary_key=True, default="qfq", server_default="qfq")
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    amount: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), nullable=False)

    # The PK leads with timeframe, so a (instrument, timeframe, adjust, ts) range
    # scan — the only query the API ever issues — needs its own index (§11.2).
    __table_args__ = (Index("ix_bars_query", "instrument_id", "timeframe", "adjust", "ts"),)
