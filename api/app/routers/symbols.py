"""`/symbols` — the typeahead and the resolve entry point (spec §3.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import InstrumentNotFound
from ..models import Instrument
from ..schemas.instrument import (
    InstrumentOut,
    ResolveRequest,
    ResolveResponse,
    SymbolSearchHit,
    SymbolSearchResponse,
)
from ..services import symbol_resolver

router = APIRouter(prefix="/symbols", tags=["symbols"])


def to_instrument_out(row: Instrument) -> InstrumentOut:
    return InstrumentOut(
        id=row.id,
        symbol=row.symbol,
        name=row.name,
        market=row.market,  # type: ignore[arg-type]
        exchange=row.exchange,
        currency=row.currency,
        sector=row.sector,
        tz=row.tz,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


@router.get("/search", response_model=SymbolSearchResponse)
async def search(
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=8, ge=1, le=20),
    db: Session = Depends(get_db),
) -> SymbolSearchResponse:
    if len(q.strip()) < 1:
        return SymbolSearchResponse(hits=[])
    hits = await symbol_resolver.search(db, q, limit)
    symbols = [hit.symbol for hit in hits]
    existing: set[str] = set()
    if symbols:
        existing = {
            row for row in db.execute(select(Instrument.symbol).where(Instrument.symbol.in_(symbols))).scalars()
        }
    return SymbolSearchResponse(
        hits=[
            SymbolSearchHit(
                symbol=hit.symbol,
                name=hit.name,
                market=hit.market,  # type: ignore[arg-type]
                currency=hit.currency,
                score=round(float(hit.score), 3),
                existing=hit.symbol in existing,
            )
            for hit in hits
        ]
    )


@router.post("/resolve", response_model=ResolveResponse)
async def resolve(payload: ResolveRequest, db: Session = Depends(get_db)) -> ResolveResponse:
    try:
        resolution = await symbol_resolver.resolve(db, payload.query)
    except InstrumentNotFound:
        db.rollback()
        raise
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        raise InstrumentNotFound(f"无法解析「{payload.query}」：{exc}"[:200]) from exc
    db.commit()
    return ResolveResponse(
        instrument=to_instrument_out(resolution.instrument),
        created=resolution.created,
        needsConfirm=False,
        candidates=[
            SymbolSearchHit(
                symbol=hit.symbol,
                name=hit.name,
                market=hit.market,  # type: ignore[arg-type]
                currency=hit.currency,
                score=round(float(hit.score), 3),
                existing=False,
            )
            for hit in resolution.candidates
        ],
    )
