"""`/instruments` — metadata, bars and quote (spec §7.1)."""

from __future__ import annotations

from datetime import UTC, datetime

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
from ..services.market_data.base import RESAMPLED_TIMEFRAMES, ProviderError, QuoteRow, resample_daily
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
    # ★I-1 (found while implementing §评审结论 A′): 周/月 are **derived** from the daily
    # series, so they must be derived *above* the cache, not inside the provider
    # chain. Resampling below the cache made 1w/1m their own stored series, each
    # walking the chain on its own — measured on this machine: `?timeframe=1d`
    # answered 262 bars from `synthetic` (1,753.06) while `?timeframe=1w` answered
    # 52 buckets from `akshare` (1,439.78) for the same window. That is exactly the
    # two-price seam this whole round is about, reintroduced one layer down. Reading
    # one daily series and bucketing it at the end makes "same session, same price"
    # structural: there is nothing else for a timeframe to disagree with.
    series_timeframe = "1d"
    rows, cached_source, read_reason = cache.read_bars_detail(
        db, instrument.id, timeframe=series_timeframe, adjust=adjust.value, start_ts=start_ts
    )
    # ★BUG-1 / BUG-7 (R-2a/b): a cache hit needs *both* properties. "Some row
    # exists" let the first range a user opened cap every wider one forever, and
    # "not stale" was never checked here at all. The key is what lets the freshness
    # rule count §9's TTL from the last refill instead of from the bar's own stamp
    # (see `cache._FETCHED_AT`), so a running session cannot leave the chart a whole
    # session behind `/quote`.
    # The key names the *stored* series (always daily), so a 日 read and a 周 read
    # share one refill stamp and cannot drift apart; and because no bucketed series
    # is ever stored, R-1's "a monthly window is never covered" cannot arise at all.
    bars_key = (instrument.id, series_timeframe, adjust.value)
    used_cache = (
        bool(rows)
        and not force
        and cache.bars_cover(rows, start_ts)
        and cache.is_bars_cache_fresh(rows, instrument.market, key=bars_key)
    )
    source = cached_source or "synthetic"
    warnings: list[str] = []
    # ★PCD-4 / §R-7: why we missed, on its own channel. `stale` may only ever claim
    # "the newest bar lags the session that is due"; conflating the two made a fully
    # current window read as 陈旧 just because a provider had something to say.
    reason_code = cache.REASON_OK

    if not used_cache:
        if read_reason == cache.REASON_MIXED_SOURCE:
            # ★R-5: the read-side gate is deliberately unchanged, so a spliced series
            # is repaired here instead — `write_bars` takes the whole series over.
            reason_code = cache.REASON_MIXED_SOURCE
        elif read_reason == cache.REASON_LEGACY_GENERATION:
            # A previous generator's rows were retired just now. That is an expected
            # one-time migration, not a dirty series — saying "mixed_source" here
            # would put a permanent accusation of corruption on a repair that only
            # ever happens once per instrument.
            reason_code = cache.REASON_LEGACY_GENERATION
        else:
            reason_code = cache.REASON_UNCOVERED
        try:
            chain = await get_registry().bars(
                instrument.symbol, timeframe=series_timeframe, start_ts=start_ts, adjust=adjust.value
            )
            rows = [bar for bar in chain.value.bars if bar.ts >= start_ts]
            source = chain.value.source
            warnings = chain.value.warnings
            cache.write_bars(
                db,
                instrument.id,
                timeframe=series_timeframe,
                adjust=adjust.value,
                bars=chain.value.bars,
                source=source,
            )
            db.commit()
            if warnings and reason_code == cache.REASON_UNCOVERED:
                # Only a plain miss may be relabelled "the chain degraded": a dirty
                # series and a retired generation already carry a sharper reason, and
                # overwriting it would hide the thing the reader most needs to know.
                reason_code = cache.REASON_PROVIDER_FALLBACK
        except ProviderError as exc:
            if not rows:
                raise Unavailable(str(exc)[:300], detail={"provider": "chain"}) from exc
            warnings = [str(exc)[:200]]
            reason_code = cache.REASON_PROVIDER_FALLBACK
    else:
        # Serve the requested window from cache; refresh in the background is
        # deliberately not done here — a cache hit is a cache hit (§9).
        rows = [bar for bar in rows if bar.ts >= start_ts]

    if timeframe.value in RESAMPLED_TIMEFRAMES:
        # The buckets carry the tail bar's ts and close, so the last weekly/monthly
        # candle == the last daily one, by construction (AC1). `market` is what makes
        # the bucket edges land on *sessions*: a provider that stamps a daily bar at
        # exchange-local midnight would otherwise put a 1st-of-month close in the
        # previous month (`base._SESSION_UTC_OFFSET_HOURS`).
        rows = resample_daily(rows, timeframe.value, market=instrument.market)

    # ★PCD-2 / AC3: `/quote` and `/bars` walk the chain independently, so a provider
    # flip can put two sources behind one screen. Pinning the winner was rejected (it
    # turns a divergence into a 503 on one endpoint, §R-6), so the divergence is
    # *said* instead of hidden: one readable warning, and a reason code a test can
    # assert without matching Chinese prose.
    quote_row = cache.read_quote(db, instrument.id)
    if quote_row is not None and quote_row.source and quote_row.source != source and cache.quote_is_fresh(quote_row):
        warnings.append(f"报价来源为 {quote_row.source}，与行情来源 {source} 不同，请注意口径。")
        reason_code = cache.REASON_DIVERGENT_SOURCE

    last_bar_date = datetime.fromtimestamp(rows[-1].ts, tz=UTC).date() if rows else None
    stale = bool(rows) and last_bar_date is not None and last_bar_date < cache.latest_session_due(instrument.market)

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
            stale=stale,
            reasonCode=reason_code,
            warnings=warnings,
        ),
    )


@router.get("/{instrument_id}/quote", response_model=QuoteOut)
async def quote(instrument_id: str, db: Session = Depends(get_db)) -> QuoteOut:
    instrument = _load(db, instrument_id)
    cached = cache.read_quote(db, instrument.id)
    if cached is not None and cache.quote_is_fresh(cached):
        return _quote_out(instrument.id, cached, cached=True)
    try:
        chain = await get_registry().quote(instrument.symbol)
    except ProviderError as exc:
        if cached is not None:
            # ★§6.1 #17: `source: "akshare (cached)"` smuggled a *state* into the
            # source field, so one string meant both "where did this come from" and
            # "how old is it" — and `dataQuality`/the report quote that string back.
            # `cached` is its own field now, like `BarsMeta.cached`.
            return _quote_out(instrument.id, cached, cached=True)
        raise Unavailable(str(exc)[:300], detail={"provider": "chain"}) from exc
    cache.write_quote(db, instrument.id, chain.value)
    db.commit()
    return _quote_out(instrument.id, chain.value, cached=False)


def _quote_out(instrument_id: str, row: QuoteRow, *, cached: bool) -> QuoteOut:
    return QuoteOut(
        instrumentId=instrument_id,
        last=row.last,
        changePct=row.change_pct,
        prevClose=row.prev_close,
        dayHigh=row.day_high,
        dayLow=row.day_low,
        volume=row.volume,
        updatedAt=row.updated_at,
        source=row.source,
        cached=cached,
    )
