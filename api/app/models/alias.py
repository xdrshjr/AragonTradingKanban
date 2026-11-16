"""SymbolAlias — every extra name a symbol answers to (spec §8.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from .instrument import Instrument


class SymbolAlias(Base):
    __tablename__ = "symbol_aliases"

    alias: Mapped[str] = mapped_column(String(64), primary_key=True)  # stored lowercased
    instrument_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )

    instrument: Mapped[Instrument] = relationship(back_populates="aliases")

    __table_args__ = (Index("ix_alias_instrument", "instrument_id"),)
