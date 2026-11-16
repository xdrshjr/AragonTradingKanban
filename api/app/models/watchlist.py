"""Watchlist and settings key/value tables (spec §8.1)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pinned: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    added_at: Mapped[str] = mapped_column(Text, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
