"""Symbol resolution: the single entry point from "a thing a user typed" to an
`Instrument` row (spec §6.2 / §3.1).

Resolution order, cheapest first:
1. exact id (``CN:600519.SH``) — no network, no guess;
2. normalised alias / name / symbol match inside the DB (``difflib`` fuzzy,
   ``rapidfuzz`` when installed);
3. code shape rules (``600519`` → ``600519.SH``, ``00700`` → ``0700.HK``,
   ``AAPL`` → ``AAPL.US``);
4. the provider chain's ``search``, which may create the instrument.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import InstrumentNotFound, Unavailable
from ..logging import get_logger
from ..models import Instrument, SymbolAlias
from .market_data.base import ProviderError, SymbolHit
from .market_data.registry import get_registry
from .market_data.synthetic_provider import KNOWN_SYMBOLS, SECTOR_BY_SYMBOL, TZ_BY_MARKET

logger = get_logger("symbol_resolver")

MARKET_BY_SUFFIX = {"SH": "CN", "SZ": "CN", "HK": "HK", "US": "US"}
CURRENCY_BY_MARKET = {"CN": "CNY", "HK": "HKD", "US": "USD"}
EXCHANGE_BY_SUFFIX = {"SH": "SSE", "SZ": "SZSE", "HK": "HKEX", "US": "NASDAQ"}

_CN_CODE = re.compile(r"^(?P<code>\d{6})(?:\.(?P<suffix>SH|SZ|SS))?$")
_HK_CODE = re.compile(r"^(?P<code>\d{1,5})(?:\.HK)?$")
_TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,7}(?:\.US)?$")
_ID_SHAPE = re.compile(r"^(?P<market>CN|HK|US):(?P<symbol>[A-Z0-9.\-]+)$")


def normalize_text(value: str) -> str:
    """Fold whitespace, case and full-width forms before any comparison."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", normalized).strip().lower()


def instrument_id(symbol: str, market: str) -> str:
    return f"{market}:{symbol}"


def infer_market_from_symbol(symbol: str) -> str | None:
    upper = symbol.upper()
    if upper.endswith((".SH", ".SZ")):
        return "CN"
    if upper.endswith(".HK"):
        return "HK"
    if upper.endswith(".US"):
        return "US"
    if re.fullmatch(r"\d{6}", upper):
        return "CN"
    if re.fullmatch(r"[A-Z][A-Z.\-]{0,7}", upper):
        return "US"
    return None


def canonicalize(value: str) -> str | None:
    """Rule-based symbol canonicalisation, no IO.

    ``600519`` → ``600519.SH`` · ``600519.SS`` → ``600519.SH`` ·
    ``00700``/``0700.HK`` → ``0700.HK`` · ``aapl``/``AAPL.US`` → ``AAPL``.
    Returns ``None`` when the shape says nothing usable.
    """
    raw = (value or "").strip().upper()
    if not raw:
        return None
    if _ID_SHAPE.match(raw):
        return _ID_SHAPE.match(raw)["symbol"]  # type: ignore[index]

    cn = _CN_CODE.match(raw)
    if cn:
        code = cn.group("code")
        suffix = cn.group("suffix")
        if suffix in ("SH", "SS"):
            return f"{code}.SH"
        if suffix == "SZ":
            return f"{code}.SZ"
        # No suffix: exchange follows the code prefix (SSE for 6/9, SZSE otherwise).
        return f"{code}.SH" if code[0] in ("6", "9") else f"{code}.SZ"

    hk = _HK_CODE.match(raw)
    if hk and (raw.endswith(".HK") or len(raw) <= 5):
        # HK tickers are 4 digits: 00700 / 0700 / 700 all mean 0700.HK.
        digits = hk.group("code").lstrip("0") or "0"
        return f"{digits.zfill(4)}.HK"

    ticker = _TICKER.match(raw)
    if ticker:
        return raw[: -len(".US")] if raw.endswith(".US") else raw
    return None


@dataclass(slots=True)
class Resolution:
    instrument: Instrument
    created: bool = False
    candidates: list[SymbolHit] = field(default_factory=list)


def _row_to_instrument(session: Session, hit: SymbolHit) -> tuple[Instrument, bool]:
    existing = session.execute(select(Instrument).where(Instrument.symbol == hit.symbol)).scalar_one_or_none()
    if existing is not None:
        return existing, False
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record = Instrument(
        id=instrument_id(hit.symbol, hit.market),
        symbol=hit.symbol,
        name=hit.name,
        market=hit.market,
        exchange=EXCHANGE_BY_SUFFIX.get(hit.symbol.rsplit(".", 1)[-1]),
        currency=hit.currency or CURRENCY_BY_MARKET.get(hit.market, "CNY"),
        sector=SECTOR_BY_SYMBOL.get(hit.symbol),
        tz=TZ_BY_MARKET.get(hit.market, "Asia/Shanghai"),
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.flush()
    return record, True


def add_alias(session: Session, instrument_row: Instrument, alias: str) -> None:
    key = normalize_text(alias)
    if not key:
        return
    existing = session.get(SymbolAlias, key)
    if existing is not None:
        return
    session.add(SymbolAlias(alias=key, instrument_id=instrument_row.id))
    session.flush()


def _score(query: str, candidate: str) -> float:
    if not candidate:
        return 0.0
    if candidate == query:
        return 1.0
    if candidate.startswith(query):
        return 0.92
    if query in candidate:
        return 0.85
    return SequenceMatcher(None, query, candidate).ratio()


def search_local(session: Session, query: str, limit: int = 8) -> list[SymbolHit]:
    """DB-only search over symbol, name and aliases — no provider, never raises."""
    needle = normalize_text(query)
    if not needle:
        return []
    hits: dict[str, SymbolHit] = {}
    for row in session.execute(select(Instrument)).scalars():
        score = max(_score(needle, normalize_text(row.symbol)), _score(needle, normalize_text(row.name)))
        if score >= 0.45:
            hits[row.symbol] = SymbolHit(row.symbol, row.name, row.market, row.currency, round(score, 3))
    for alias in session.execute(
        select(SymbolAlias).where(SymbolAlias.alias.like(f"%{needle}%")).limit(limit * 3)
    ).scalars():
        instrument_row = session.get(Instrument, alias.instrument_id)
        if instrument_row is None:
            continue
        score = max(_score(needle, alias.alias), 0.8)
        current = hits.get(instrument_row.symbol)
        if current is None or current.score < score:
            hits[instrument_row.symbol] = SymbolHit(
                instrument_row.symbol,
                instrument_row.name,
                instrument_row.market,
                instrument_row.currency,
                round(score, 3),
            )
    return sorted(hits.values(), key=lambda hit: hit.score, reverse=True)[:limit]


def _synthetic_search(query: str, limit: int) -> list[SymbolHit]:
    """Offline fallback so a cold database still resolves the demo symbols."""
    needle = normalize_text(query)
    out: list[SymbolHit] = []
    for symbol, name, market, currency, _exchange in KNOWN_SYMBOLS:
        if needle and (needle in normalize_text(symbol) or needle in normalize_text(name)):
            out.append(SymbolHit(symbol, name, market, currency, 0.7))
    return out[:limit]


async def search(session: Session, query: str, limit: int = 8) -> list[SymbolHit]:
    """Search for the typeahead: local rows first, then the live provider chain."""
    needle = (query or "").strip()
    if not needle:
        return []
    local = search_local(session, needle, limit)
    if len(local) >= limit:
        return local[:limit]

    remote: list[SymbolHit] = []
    try:
        chain = await get_registry().search(needle, limit)
        remote = chain.value
    except ProviderError as exc:
        logger.info("provider search failed, falling back to synthetic: %s", exc)
        remote = _synthetic_search(needle, limit)

    merged: dict[str, SymbolHit] = {hit.symbol: hit for hit in local}
    for hit in remote:
        if hit.symbol not in merged:
            merged[hit.symbol] = hit
    return sorted(merged.values(), key=lambda hit: hit.score, reverse=True)[:limit]


async def resolve(session: Session, query: str) -> Resolution:
    """Turn any user input into a persisted `Instrument`.

    Raises ``InstrumentNotFound`` when nothing plausible matches.
    """
    raw = (query or "").strip()
    if not raw:
        raise InstrumentNotFound("请输入投资标的代码或名称")

    # 1. exact row by id or symbol
    id_match = _ID_SHAPE.match(raw.upper())
    if id_match:
        row = session.get(Instrument, f"{id_match['market']}:{id_match['symbol']}")
        if row is not None:
            return Resolution(instrument=row)
    upper = raw.upper()
    row = session.execute(select(Instrument).where(func.upper(Instrument.symbol) == upper)).scalar_one_or_none()
    if row is not None:
        return Resolution(instrument=row)

    # 2. rule-based canonical form
    canonical = canonicalize(raw)
    if canonical:
        row = session.execute(select(Instrument).where(Instrument.symbol == canonical)).scalar_one_or_none()
        if row is not None:
            return Resolution(instrument=row)
        market = infer_market_from_symbol(canonical) or "CN"
        # Known-name lookup keeps the pretty Chinese name for demo symbols.
        for symbol, name, known_market, currency, _exchange in KNOWN_SYMBOLS:
            if symbol == canonical:
                hit = SymbolHit(symbol, name, known_market, currency, 1.0)
                created_row, created = _row_to_instrument(session, hit)
                add_alias(session, created_row, raw)
                return Resolution(instrument=created_row, created=created)
        hit = SymbolHit(
            canonical,
            canonical,
            market,
            CURRENCY_BY_MARKET.get(market, "CNY"),
            0.75,
        )
        created_row, created = _row_to_instrument(session, hit)
        add_alias(session, created_row, raw)
        return Resolution(instrument=created_row, created=created)

    # 3. alias / name match in the database
    needle = normalize_text(raw)
    alias = session.get(SymbolAlias, needle)
    if alias is not None:
        row = session.get(Instrument, alias.instrument_id)
        if row is not None:
            return Resolution(instrument=row)

    local = search_local(session, raw, 5)
    if not local:
        local = _synthetic_search(raw, 5)
    if len(local) == 1 or (local and local[0].score >= 0.9):
        best = local[0]
        created_row, created = _row_to_instrument(session, best)
        add_alias(session, created_row, raw)
        return Resolution(instrument=created_row, created=created, candidates=local[1:])

    # 4. provider chain
    try:
        hits = await search(session, raw, 5)
    except (ProviderError, Unavailable) as exc:
        raise InstrumentNotFound(f"无法解析「{raw}」") from exc

    if not hits:
        raise InstrumentNotFound(f"无法解析「{raw}」，试试股票代码或完整名称")
    if len(hits) > 1 and hits[0].score < 0.9:
        # Ambiguous: hand the candidate list back for the UI to disambiguate.
        best = hits[0]
        created_row, created = _row_to_instrument(session, best)
        add_alias(session, created_row, raw)
        return Resolution(instrument=created_row, created=created, candidates=hits[1:])
    best = hits[0]
    created_row, created = _row_to_instrument(session, best)
    add_alias(session, created_row, raw)
    return Resolution(instrument=created_row, created=created, candidates=hits[1:])
