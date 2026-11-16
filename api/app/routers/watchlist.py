"""`/watchlist` — the left rail's saved list and its ordering (spec §7.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import iso
from ..db.session import get_db
from ..errors import InstrumentNotFound
from ..models import Instrument, WatchlistItem
from ..schemas.instrument import InstrumentOut
from .symbols import to_instrument_out

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistEntry(BaseModel):
    instrument: InstrumentOut
    sortOrder: int = 0
    pinned: bool = False
    addedAt: str = ""


class WatchlistResponse(BaseModel):
    items: list[WatchlistEntry] = Field(default_factory=list)


class AddWatchlistRequest(BaseModel):
    instrumentId: str


class ReorderRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


def _entries(db: Session) -> list[WatchlistEntry]:
    rows = db.execute(
        select(WatchlistItem, Instrument)
        .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
        .order_by(WatchlistItem.pinned.desc(), WatchlistItem.sort_order.asc(), WatchlistItem.added_at.asc())
    ).all()
    return [
        WatchlistEntry(
            instrument=to_instrument_out(instrument),
            sortOrder=item.sort_order,
            pinned=bool(item.pinned),
            addedAt=item.added_at,
        )
        for item, instrument in rows
    ]


@router.get("", response_model=WatchlistResponse)
def list_watchlist(db: Session = Depends(get_db)) -> WatchlistResponse:
    return WatchlistResponse(items=_entries(db))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WatchlistResponse)
def add_watchlist(payload: AddWatchlistRequest, db: Session = Depends(get_db)) -> WatchlistResponse:
    instrument = db.get(Instrument, payload.instrumentId)
    if instrument is None:
        instrument = db.execute(
            select(Instrument).where(Instrument.symbol == payload.instrumentId.upper())
        ).scalar_one_or_none()
    if instrument is None:
        raise InstrumentNotFound()
    existing = db.get(WatchlistItem, instrument.id)
    if existing is None:
        max_order = db.execute(select(WatchlistItem.sort_order)).scalars().all()
        db.add(
            WatchlistItem(
                instrument_id=instrument.id,
                sort_order=(max(max_order) + 1) if max_order else 0,
                pinned=0,
                added_at=iso(),
            )
        )
        db.commit()
    return WatchlistResponse(items=_entries(db))


@router.delete("/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist(instrument_id: str, db: Session = Depends(get_db)) -> Response:
    item = db.get(WatchlistItem, instrument_id)
    if item is None:
        raise InstrumentNotFound(f"{instrument_id} 不在自选列表")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/reorder", response_model=WatchlistResponse)
def reorder(payload: ReorderRequest, db: Session = Depends(get_db)) -> WatchlistResponse:
    for order, instrument_id in enumerate(payload.ids):
        item = db.get(WatchlistItem, instrument_id)
        if item is None:
            raise InstrumentNotFound(f"{instrument_id} 不在自选列表")
        item.sort_order = order
    db.commit()
    return WatchlistResponse(items=_entries(db))
