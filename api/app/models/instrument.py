"""Instrument — one tradable symbol in one venue (spec §8.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base

if TYPE_CHECKING:  # pragma: no cover
    from .alias import SymbolAlias


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # "CN:600519.SH"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # CN | HK | US
    exchange: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(64))
    tz: Mapped[str] = mapped_column(String(48), nullable=False, default="Asia/Shanghai", server_default="Asia/Shanghai")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    aliases: Mapped[list[SymbolAlias]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
        # `selectin` would add a second SELECT to every Instrument load in the
        # API, and no router reads `.aliases` — the resolver queries the alias
        # table directly. Keep it lazy and explicit.
        lazy="select",
    )

    __table_args__ = (Index("ix_instruments_market_symbol", "market", "symbol"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Instrument {self.id} {self.name}>"
