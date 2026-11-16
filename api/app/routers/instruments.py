"""`/instruments` — metadata, bars and quote (spec §7.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import InstrumentNotFound, Unavailable
from ..models import Instrument
from ..schemas.bars import (
    Adjust,
    BarOut,
    BarsMeta,
    BarsResponse,
    IndicatorsOut,
    Range,
    Timeframe,
    range_to_epoch,
)
from ..schemas.instrument import InstrumentsResponse, QuoteOut
from ..services import indicators
from ..services.market_data import cache
from ..services.market_data.base import ProviderError
from ..services.market_data.registry import get_registry
from .symbols import to_instrument_out

router = APIRouter(prefix="/instruments", tags=["instruments"])


def _load(db: Session, instrument_id: str) -> Instrument:
    row = db.get(Instrument, instrument_id)
    if row is None:
        # Accept a bare symbol too, so links stay readable: /instruments/600519.SH/bars
        row = db.execute(select(Instrument).where(Instrument.symbol == instrument_id.upper())).scalar_one_or_none()
    if row is None:
        raise InstrumentNotFound(f"标的 {instrument_id} 不存在")
    return row


@router.get("", response_model=InstrumentsResponse)
def list_instruments(
    ids: str = Query(default="", max_length=512),
    db: Session = Depends(get_db),
) -> InstrumentsResponse:
    if not ids.strip():
        rows = db.execute(select(Instrument).order_by(Instrument.symbol)).scalars().all()
        return InstrumentsResponse(items=[to_instrument_out(row) for row in rows])
    wanted = [item.strip() for item in ids.split(",") if item.strip()]
    rows = db.execute(select(Instrument).where(Instrument.id.in_(wanted))).scalars().all()
    found = {row.id: row for row in rows}
    missing = [item for item in wanted if item not in found]
    if missing:
        raise InstrumentNotFound(f"标的 {missing[0]} 不存在", detail={"missing": missing})
    return InstrumentsResponse(items=[to_instrument_out(found[item]) for item in wanted])


@router.get("/{instrument_id}/bars", response_model=BarsResponse)
async def bars(
    instrument_id: str,
    timeframe: Timeframe = Query(default=Timeframe.d1),
    range: Range = Query(default=Range.y1),
    adjust: Adjust = Query(default=Adjust.qfq),
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> BarsResponse:
    instrument = _load(db, instrument_id)
    start_ts = range_to_epoch(range.value)
    rows, cached_source = cache.read_bars(
        db, instrument.id, timeframe=timeframe.value, adjust=adjust.value, start_ts=start_ts
    )
    used_cache = bool(rows) and not force
    source = cached_source or "synthetic"
    warnings: list[str] = []

    if not used_cache:
        try:
            chain = await get_registry().bars(
                instrument.symbol, timeframe=timeframe.value, start_ts=start_ts, adjust=adjust.value
            )
            rows = [bar for bar in chain.value.bars if bar.ts >= start_ts]
            source = chain.value.source
            warnings = chain.value.warnings
            cache.write_bars(
                db,
                instrument.id,
                timeframe=timeframe.value,
                adjust=adjust.value,
                bars=chain.value.bars,
                source=source,
            )
            db.commit()
        except ProviderError as exc:
            if not rows:
                raise Unavailable(str(exc)[:300], detail={"provider": "chain"}) from exc
            warnings = [str(exc)[:200]]
    else:
        # Serve the requested window from cache; refresh in the background is
        # deliberately not done here — a cache hit is a cache hit (§9).
        rows = [bar for bar in rows if bar.ts >= start_ts]

    closes = [bar.close for bar in rows]
    bundle = indicators.compute_indicator_bundle(closes) if closes else {}
    return BarsResponse(
        instrument=to_instrument_out(instrument),
        bars=[
            BarOut(ts=bar.ts, o=bar.open, h=bar.high, l=bar.low, c=bar.close, v=bar.volume, amount=bar.amount)
            for bar in rows
        ],
        indicators=IndicatorsOut(**bundle) if bundle else IndicatorsOut(),
        meta=BarsMeta(
            source=source,
            barsCount=len(rows),
            fromTs=rows[0].ts if rows else None,
            toTs=rows[-1].ts if rows else None,
            adjust=adjust.value,
            timeframe=timeframe.value,
            cached=used_cache,
            stale=bool(warnings),
            warnings=warnings,
        ),
    )


@router.get("/{instrument_id}/quote", response_model=QuoteOut)
async def quote(instrument_id: str, db: Session = Depends(get_db)) -> QuoteOut:
    instrument = _load(db, instrument_id)
    cached = cache.read_quote(db, instrument.id)
    if cached is not None and cache.quote_is_fresh(cached):
        return QuoteOut(
            instrumentId=instrument.id,
            last=cached.last,
            changePct=cached.change_pct,
            prevClose=cached.prev_close,
            dayHigh=cached.day_high,
            dayLow=cached.day_low,
            volume=cached.volume,
            updatedAt=cached.updated_at,
            source=cached.source,
        )
    try:
        chain = await get_registry().quote(instrument.symbol)
    except ProviderError as exc:
        if cached is not None:
            return QuoteOut(
                instrumentId=instrument.id,
                last=cached.last,
                changePct=cached.change_pct,
                prevClose=cached.prev_close,
                dayHigh=cached.day_high,
                dayLow=cached.day_low,
                volume=cached.volume,
                updatedAt=cached.updated_at,
                source=f"{cached.source} (cached)",
            )
        raise Unavailable(str(exc)[:300], detail={"provider": "chain"}) from exc
    cache.write_quote(db, instrument.id, chain.value)
    db.commit()
    fresh = chain.value
    return QuoteOut(
        instrumentId=instrument.id,
        last=fresh.last,
        changePct=fresh.change_pct,
        prevClose=fresh.prev_close,
        dayHigh=fresh.day_high,
        dayLow=fresh.day_low,
        volume=fresh.volume,
        updatedAt=fresh.updated_at,
        source=fresh.source,
    )
