"""图表专用历史：单源日线预热、写前校验与周期一致裁切。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from numbers import Real

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..errors import Unavailable
from ..models import Bar, Instrument
from ..schemas.bars import (
    BarOut,
    BarsMeta,
    BarsResponse,
    BarStatsOut,
    ChartDetailsOut,
    IndicatorsOut,
    range_to_epoch,
)
from ..schemas.instrument import InstrumentOut
from .chart_indicators import compute_chart_indicator_bundle
from .market_data import cache
from .market_data.base import (
    RESAMPLED_TIMEFRAMES,
    BarRow,
    ProviderError,
    _bucket_key,
    _session_date,
    resample_daily,
)
from .market_data.registry import get_registry

CALCULATION_DAYS = 3653
MAX_DAILY_BARS = 4000


@dataclass(frozen=True, slots=True)
class ChartBarsRequest:
    """已由路由枚举校验的请求；所有口径都从同一日线源派生。"""

    timeframe: str = "1d"
    range: str = "1y"
    adjust: str = "qfq"
    force: bool = False


@dataclass(slots=True)
class ChartHistory:
    """一份来源和一个计算边界，不拼接 provider 与旧缓存。"""

    rows: list[BarRow]
    source: str
    warnings: list[str]
    reason: str
    cached: bool
    invalid_reason: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _invalid(reason: str = "invalid_chart_series") -> Unavailable:
    return Unavailable("图表历史数据不符合契约，请重试", detail={"reason": reason})


def _finite(value: object) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate_values(row: BarRow) -> None:
    prices = (row.open, row.high, row.low, row.close)
    if not all(_finite(value) for value in prices):
        raise _invalid()
    if not (
        row.low <= min(row.open, row.close)
        <= max(row.open, row.close) <= row.high
    ):
        raise _invalid()
    if not _finite(row.volume) or row.volume < 0:
        raise _invalid()
    if row.amount is not None and (not _finite(row.amount) or row.amount < 0):
        raise _invalid()


def _normalize_daily(
    rows: list[BarRow], start: int, now: datetime, market: str,
) -> list[BarRow]:
    selected: list[BarRow] = []
    for row in rows:
        try:
            if not isinstance(row.ts, int) or isinstance(row.ts, bool):
                raise _invalid()
            _session_date(row.ts, market)
        except (ValueError, OverflowError, OSError) as exc:
            raise _invalid() from exc
        if start <= row.ts <= now.timestamp():
            _validate_values(row)
            selected.append(row)
    unique: dict[int, BarRow] = {}
    dates: dict[date, int] = {}
    for row in sorted(selected, key=lambda item: item.ts):
        day = _session_date(row.ts, market)
        if row.ts in unique and unique[row.ts] != row:
            raise _invalid()
        if day in dates and dates[day] != row.ts:
            raise _invalid()
        unique[row.ts] = row
        dates[day] = row.ts
    if len(unique) > MAX_DAILY_BARS:
        raise _invalid("chart_history_limit")
    return list(unique.values())


def _read_cache(
    db: Session, instrument: Instrument, request: ChartBarsRequest, start: int,
) -> tuple[list[BarRow], str | None, str]:
    # read_bars_detail retires legacy rows during a read. Defer every mutation
    # until replacement passes aggregation and indicator validation.
    stored = list(db.scalars(select(Bar).where(
        Bar.instrument_id == instrument.id, Bar.timeframe == "1d",
        Bar.adjust == request.adjust, Bar.ts >= start,
    ).order_by(Bar.ts)))
    sources = {row.source for row in stored}
    if "synthetic" in sources:
        return [], None, cache.REASON_LEGACY_GENERATION
    if len(sources) > 1:
        return [], None, cache.REASON_MIXED_SOURCE
    if not stored:
        return [], None, cache.REASON_EMPTY
    rows = [BarRow(
        row.ts, row.open, row.high, row.low, row.close, row.volume, row.amount,
    ) for row in stored]
    return rows, cache._public_source(stored[0].source), cache.REASON_OK


def _cache_candidate(
    db: Session, instrument: Instrument, request: ChartBarsRequest,
    bounds: tuple[int, datetime],
) -> ChartHistory:
    start, now = bounds
    rows, source, reason = _read_cache(db, instrument, request, start)
    invalid_reason = None
    try:
        valid = _normalize_daily(rows, start, now, instrument.market)
    except Unavailable as exc:
        valid = []
        invalid_reason = exc.detail["reason"]
    return ChartHistory(valid, source or "", [], reason, True, invalid_reason)


async def _fetch_history(
    db: Session, instrument: Instrument, request: ChartBarsRequest,
    bounds: tuple[int, datetime],
) -> ChartHistory:
    start, now = bounds
    candidate = _cache_candidate(db, instrument, request, bounds)
    key = (instrument.id, "1d", request.adjust)
    if (
        candidate.rows and not request.force
        and cache.bars_cover(candidate.rows, start)
        and cache.is_bars_cache_fresh(
            candidate.rows, instrument.market, now, key=key,
        )
    ):
        return candidate
    reason = candidate.reason
    if reason not in (
        cache.REASON_MIXED_SOURCE, cache.REASON_LEGACY_GENERATION,
    ):
        reason = cache.REASON_UNCOVERED
    try:
        result = await get_registry().bars(
            instrument.symbol, timeframe="1d", start_ts=start,
            adjust=request.adjust,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        # The existing registry sorts ts before returning its DTO. A malformed
        # timestamp can fail there before our own validation sees the response.
        raise _invalid() from exc
    except ProviderError as exc:
        if not candidate.rows:
            if candidate.invalid_reason:
                raise _invalid(candidate.invalid_reason) from exc
            raise Unavailable(
                str(exc)[:300], detail={"provider": "chain"},
            ) from exc
        candidate.reason = cache.REASON_PROVIDER_FALLBACK
        candidate.warnings = [str(exc)[:200]]
        return candidate
    rows = _normalize_daily(result.value.bars, start, now, instrument.market)
    warnings = list(result.value.warnings)
    if warnings and reason == cache.REASON_UNCOVERED:
        reason = cache.REASON_PROVIDER_FALLBACK
    return ChartHistory(rows, result.value.source, warnings, reason, False)


def _periods(
    rows: list[BarRow], timeframe: str, market: str, start: int,
) -> tuple[list[BarRow], list[tuple[str, str]], bool]:
    if timeframe not in RESAMPLED_TIMEFRAMES:
        dates = [
            (_session_date(row.ts, market).isoformat(),) * 2 for row in rows
        ]
        return list(rows), dates, False
    groups: dict[tuple[int, int], list[BarRow]] = {}
    for row in rows:
        key = _bucket_key(_session_date(row.ts, market), timeframe)
        groups.setdefault(key, []).append(row)
    dates = [(
        _session_date(group[0].ts, market).isoformat(),
        _session_date(group[-1].ts, market).isoformat(),
    ) for group in groups.values()]
    leading_partial = bool(groups) and next(iter(groups)) == _bucket_key(
        _session_date(start, market), timeframe,
    )
    sampled = resample_daily(rows, timeframe, market=market)
    return sampled, dates, leading_partial


def _statistics(
    rows: list[BarRow], dates: list[tuple[str, str]], leading_partial: bool,
) -> list[BarStatsOut]:
    stats: list[BarStatsOut] = []
    previous: float | None = None
    for index, row in enumerate(rows):
        partial = leading_partial and index == 0
        valid_previous = previous is not None and previous > 0 and not partial
        change = row.close - previous if valid_previous else None
        stats.append(BarStatsOut(
            ts=row.ts, periodStart=dates[index][0], periodEnd=dates[index][1],
            leadingPartial=partial, previousClose=None if partial else previous,
            change=change,
            changePct=100 * change / previous if valid_previous else None,
            amplitudePct=(
                100 * (row.high - row.low) / previous
                if valid_previous else None
            ),
        ))
        if not partial:
            previous = row.close
    return stats


def _compute_period_data(
    history: ChartHistory, instrument: Instrument, request: ChartBarsRequest,
    start: int,
) -> tuple[list[BarRow], list[BarStatsOut], dict[str, list[float | None]]]:
    try:
        rows, dates, partial = _periods(
            history.rows, request.timeframe, instrument.market, start,
        )
        for row in rows:
            _validate_values(row)
        excluded = 1 if partial else 0
        bundle = compute_chart_indicator_bundle(rows[excluded:])
        if excluded:
            bundle = {name: [None] + values for name, values in bundle.items()}
        stats = _statistics(rows, dates, partial)
        for values in bundle.values():
            if any(v is not None and not _finite(v) for v in values):
                raise _invalid()
        for stat in stats:
            values = (
                stat.previousClose, stat.change,
                stat.changePct, stat.amplitudePct,
            )
            if any(v is not None and not _finite(v) for v in values):
                raise _invalid()
    except (OverflowError, ZeroDivisionError) as exc:
        raise _invalid() from exc
    return rows, stats, bundle


def _write_history(
    db: Session, instrument: Instrument, request: ChartBarsRequest,
    history: ChartHistory, bounds: tuple[int, datetime],
) -> None:
    if history.cached:
        return
    start, now = bounds
    key = (instrument.id, "1d", request.adjust)
    previous_stamp = cache._FETCHED_AT.get(key)
    try:
        db.execute(delete(Bar).where(
            Bar.instrument_id == instrument.id, Bar.timeframe == "1d",
            Bar.adjust == request.adjust, Bar.ts >= start,
            Bar.ts <= int(now.timestamp()),
        ))
        cache.write_bars(
            db, instrument.id, timeframe="1d", adjust=request.adjust,
            bars=history.rows, source=history.source,
        )
        if history.rows:
            cache._FETCHED_AT[key] = now.timestamp()
        db.commit()
    except Exception:
        db.rollback()
        if previous_stamp is None:
            cache._FETCHED_AT.pop(key, None)
        else:
            cache._FETCHED_AT[key] = previous_stamp
        raise


def _units(source: str, market: str) -> tuple[str, int | None]:
    if source == "akshare" and market == "CN":
        return "lot", 100
    return {"yfinance": ("share", 1), "synthetic": ("unit", None)}.get(
        source, ("unknown", None),
    )


def _effective_adjust(source: str, requested: str) -> str:
    if source == "synthetic":
        return "synthetic"
    if source == "yfinance" and requested != "none":
        return "auto"
    return requested


def _source_warnings(
    db: Session, instrument: Instrument, history: ChartHistory, now: datetime,
) -> None:
    quote = cache.read_quote(db, instrument.id)
    if (
        quote is not None and quote.source and quote.source != history.source
        and cache.quote_is_fresh(quote, now)
    ):
        history.warnings.append(
            f"报价来源为 {quote.source}，与行情来源 {history.source} 不同，请注意口径。"
        )
        history.reason = cache.REASON_DIVERGENT_SOURCE


def _bars_meta(
    visible: list[BarRow], instrument: Instrument, request: ChartBarsRequest,
    history: ChartHistory, now: datetime,
) -> BarsMeta:
    is_stale = bool(visible) and _session_date(
        visible[-1].ts, instrument.market,
    ) < cache.latest_session_due(instrument.market, now)
    return BarsMeta(
        source=history.source, barsCount=len(visible),
        fromTs=visible[0].ts if visible else None,
        toTs=visible[-1].ts if visible else None,
        adjust=request.adjust, timeframe=request.timeframe,
        cached=history.cached, stale=is_stale,
        reasonCode=history.reason, warnings=history.warnings,
    )


def _chart_details(
    instrument: Instrument, request: ChartBarsRequest, history: ChartHistory,
    statistics: tuple[list[BarStatsOut], int], start: int,
) -> ChartDetailsOut:
    selected_stats, count = statistics
    displayed_count = sum(not stat.leadingPartial for stat in selected_stats)
    unit, unit_size = _units(history.source, instrument.market)
    return ChartDetailsOut(
        stats=selected_stats, calculationBarsCount=count,
        warmupBarsCount=count - displayed_count,
        historyLimited=not cache.bars_cover(history.rows, start),
        volumeUnit=unit, volumeUnitSize=unit_size,
        amountCurrency=instrument.currency,
        effectiveAdjust=_effective_adjust(history.source, request.adjust),
    )


def _response(
    instrument: Instrument, request: ChartBarsRequest, history: ChartHistory,
    calculated: tuple[list[BarRow], list[BarStatsOut], dict],
    bounds: tuple[int, datetime],
) -> BarsResponse:
    rows, stats, bundle = calculated
    start, now = bounds
    display_start = _session_date(
        range_to_epoch(request.range, now=now), instrument.market,
    )
    indices = [i for i, stat in enumerate(stats)
               if date.fromisoformat(stat.periodEnd) >= display_start]
    visible = [rows[index] for index in indices]
    selected_stats = [stats[index] for index in indices]
    count = sum(not stat.leadingPartial for stat in stats)
    metadata = InstrumentOut.model_validate(instrument, from_attributes=True)
    metadata.createdAt = instrument.created_at
    metadata.updatedAt = instrument.updated_at
    return BarsResponse(
        instrument=metadata,
        bars=[BarOut(
            ts=r.ts, o=r.open, h=r.high, l=r.low, c=r.close,
            v=r.volume, amount=r.amount,
        ) for r in visible],
        indicators=IndicatorsOut(**{
            key: [values[i] for i in indices] for key, values in bundle.items()
        }),
        meta=_bars_meta(visible, instrument, request, history, now),
        chart=_chart_details(
            instrument, request, history, (selected_stats, count), start,
        ),
    )


async def load_chart_bars(
    db: Session, instrument: Instrument, request: ChartBarsRequest,
) -> BarsResponse:
    """验证并计算整段统一历史后再持久化；返回等索引裁切的图表包。"""
    now = _utc_now()
    start = int((now.replace(hour=0, minute=0, second=0, microsecond=0)
                 - timedelta(days=CALCULATION_DAYS)).timestamp())
    bounds = (start, now)
    history = await _fetch_history(db, instrument, request, bounds)
    calculated = _compute_period_data(history, instrument, request, start)
    _source_warnings(db, instrument, history, now)
    response = _response(instrument, request, history, calculated, bounds)
    _write_history(db, instrument, request, history, bounds)
    return response
