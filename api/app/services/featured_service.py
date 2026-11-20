"""The platform-featured watchlist (spec §24.3.9(d), §24.5.3 `/admin/featured-watchlist`).

The legacy `watchlist` table is, since M49, the **platform's** list: every visitor sees it
(guests read it as their rail, new users are seeded from it) and only admins edit it. These
three functions are that edit surface. They `flush` and write the audit row; the router
commits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import iso
from ..errors import InstrumentNotFound
from ..models import Instrument, WatchlistItem
from ..security import clock
from ..security.principal import Principal
from . import audit_service
from .audit_service import AuditTarget


def _instrument_dict(row: Instrument) -> dict[str, Any]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "name": row.name,
        "market": row.market,
        "exchange": row.exchange,
        "currency": row.currency,
        "sector": row.sector,
        "tz": row.tz,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def list_featured(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(WatchlistItem, Instrument)
        .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
        .order_by(WatchlistItem.pinned.desc(), WatchlistItem.sort_order.asc(), WatchlistItem.added_at.asc())
    ).all()
    return [
        {
            "instrument": _instrument_dict(instrument),
            "sortOrder": item.sort_order,
            "pinned": bool(item.pinned),
            "addedAt": item.added_at,
        }
        for item, instrument in rows
    ]


def _find_instrument(db: Session, ident: str) -> Instrument | None:
    instrument = db.get(Instrument, ident)
    if instrument is None:
        instrument = db.execute(select(Instrument).where(Instrument.symbol == ident.upper())).scalar_one_or_none()
    return instrument


def add_featured(db: Session, actor: Principal, instrument_id: str, now: datetime | None = None) -> bool:
    """Add to the featured list. Returns whether a row was added (re-adding is a quiet no-op)."""
    instrument = _find_instrument(db, instrument_id)
    if instrument is None:
        raise InstrumentNotFound()
    if db.get(WatchlistItem, instrument.id) is not None:
        return False
    moment = now or clock.now()
    top = db.execute(select(func.max(WatchlistItem.sort_order))).scalar_one()
    db.add(
        WatchlistItem(
            instrument_id=instrument.id,
            sort_order=(int(top) + 1) if top is not None else 0,
            pinned=0,
            added_at=iso(moment),
        )
    )
    db.flush()
    audit_service.record(
        db, actor, "featured.add", AuditTarget("instrument", instrument.id, instrument.symbol), now=moment
    )
    return True


def remove_featured(db: Session, actor: Principal, instrument_id: str, now: datetime | None = None) -> None:
    item = db.get(WatchlistItem, instrument_id)
    if item is None:
        raise InstrumentNotFound(f"{instrument_id} 不在平台精选列表")
    instrument = db.get(Instrument, instrument_id)
    db.delete(item)
    db.flush()
    audit_service.record(
        db,
        actor,
        "featured.remove",
        AuditTarget("instrument", instrument_id, instrument.symbol if instrument else None),
        now=now or clock.now(),
    )
