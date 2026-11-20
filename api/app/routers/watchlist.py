"""`/watchlist` — the left rail's saved list and its ordering (spec §7.1)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import InstrumentNotFound
from ..models import Instrument
from ..schemas.instrument import InstrumentOut
from ..security import deps
from ..security.principal import Principal
from ..services import watchlist_service
from .symbols import to_instrument_out

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistEntry(BaseModel):
    instrument: InstrumentOut
    sortOrder: int = 0
    pinned: bool = False
    addedAt: str = ""


class WatchlistResponse(BaseModel):
    items: list[WatchlistEntry] = Field(default_factory=list)
    #: ★v15（§24.5.5）：`personal` = 登录用户自己的；`featured` = 平台精选（游客 / 无账号主体，只读）。
    scope: Literal["personal", "featured"] = "featured"


class AddWatchlistRequest(BaseModel):
    instrumentId: str


class ReorderRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


def _response(db: Session, principal: Principal) -> WatchlistResponse:
    scope, rows = watchlist_service.list_for(db, principal)
    return WatchlistResponse(
        scope=scope,
        items=[
            WatchlistEntry(
                instrument=to_instrument_out(instrument),
                sortOrder=sort_order,
                pinned=pinned,
                addedAt=added_at,
            )
            for sort_order, pinned, added_at, instrument in rows
        ],
    )


@router.get("", response_model=WatchlistResponse)
def list_watchlist(
    response: Response,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> WatchlistResponse:
    # 视角相关（个人 / 精选）且带写副作用（首次播种）——绝不能被任何缓存复用（RV-15）。
    response.headers["Cache-Control"] = "private, no-store"
    return _response(db, principal)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WatchlistResponse)
def add_watchlist(
    payload: AddWatchlistRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> WatchlistResponse:
    instrument = db.get(Instrument, payload.instrumentId)
    if instrument is None:
        instrument = db.execute(
            select(Instrument).where(Instrument.symbol == payload.instrumentId.upper())
        ).scalar_one_or_none()
    if instrument is None:
        raise InstrumentNotFound()
    watchlist_service.add(db, principal, instrument)
    return _response(db, principal)


@router.delete("/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist(
    instrument_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> Response:
    if not watchlist_service.remove(db, principal, instrument_id):
        raise InstrumentNotFound(f"{instrument_id} 不在自选列表")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/reorder", response_model=WatchlistResponse)
def reorder(
    payload: ReorderRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> WatchlistResponse:
    missing = watchlist_service.reorder(db, principal, payload.ids)
    if missing is not None:
        raise InstrumentNotFound(f"{missing} 不在自选列表")
    return _response(db, principal)
