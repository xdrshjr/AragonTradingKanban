"""`/search` orchestration — instruments + report blocks + deep links (§18.5.3).

The FTS index only decides **which blocks are candidates and in what order**. The
snippet is cut from the block's own text here, because `snippet()` on a bigram body
returns shredded fragments (measured, v4 evidence E-2) while the reader needs a
sentence. `blockId` rides along so a hit can deep-link and highlight (★V4-P0-1).
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Instrument, Report, WatchlistItem
from ..schemas.search import InstrumentHit, ReportHit, SearchResponse
from . import block_text, search_fallback, search_index, symbol_resolver

#: Characters shown on either side of the match. Kept tight: a search hit is a
#: signpost, not a paragraph.
SNIPPET_RADIUS = 30
MIN_SNIPPET_CHARS = 12


def _snippet(text: str, terms: list[str], *, radius: int = SNIPPET_RADIUS) -> str:
    """A slice of the **original** text with `<mark>` around the first match.

    No ellipsis characters are added on purpose: after stripping tags this string
    must be a verbatim substring of `block_to_text(block)`, which is exactly what
    AC-39 asserts.
    """
    compact = " ".join(text.split())
    if not compact:
        return ""
    lowered = compact.lower()
    position = -1
    hit_term = ""
    for term in terms:
        found = lowered.find(term.lower())
        if found >= 0 and (position < 0 or found < position):
            position = found
            hit_term = term
    if position < 0:
        return compact[: max(MIN_SNIPPET_CHARS, radius * 2)]
    start = max(0, position - radius)
    end = min(len(compact), position + len(hit_term) + radius)
    if end - start < MIN_SNIPPET_CHARS:
        end = min(len(compact), start + MIN_SNIPPET_CHARS)
    snippet = compact[start:end]
    offset = position - start
    head = snippet[:offset]
    matched = snippet[offset : offset + len(hit_term)]
    tail = snippet[offset + len(hit_term) :]
    return f"{head}<mark>{matched}</mark>{tail}"


def _has_report(session: Session, instrument_id: str) -> bool:
    row = session.execute(
        select(Report.id).where(Report.instrument_id == instrument_id, Report.deleted_at.is_(None)).limit(1)
    ).first()
    return row is not None


def _contains_term(text: str, terms: list[str]) -> bool:
    """Whether `text` can be highlighted with one of `terms` (case-insensitive)."""
    if not text or not terms:
        return False
    lowered = text.lower()
    return any(term and term.lower() in lowered for term in terms)


def _instrument_hits(session: Session, query: str, limit: int) -> list[InstrumentHit]:
    hits: list[InstrumentHit] = []
    seen: set[str] = set()

    for hit in symbol_resolver.search_local(session, query, limit=limit):
        row = session.execute(select(Instrument).where(Instrument.symbol == hit.symbol)).scalar_one_or_none()
        if row is None:
            continue
        hits.append(
            InstrumentHit(
                symbol=row.symbol,
                name=row.name,
                market=row.market,
                currency=row.currency,
                score=float(hit.score),
                existing=_has_report(session, row.id),
            )
        )
        seen.add(row.symbol)

    # Pinyin / FTS matches add instruments the scored resolver cannot see.
    for instrument_id in search_index.search_instruments(session, query, limit=limit):
        row = session.get(Instrument, instrument_id)
        if row is None or row.symbol in seen:
            continue
        hits.append(
            InstrumentHit(
                symbol=row.symbol,
                name=row.name,
                market=row.market,
                currency=row.currency,
                score=0.7,
                existing=_has_report(session, row.id),
            )
        )
        seen.add(row.symbol)

    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:limit]


def _watchlist_instruments(session: Session) -> list[Instrument]:
    rows = session.execute(
        select(Instrument)
        .join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
        .order_by(WatchlistItem.sort_order.asc())
        .limit(20)
    ).scalars()
    return list(rows)


def _block_texts(doc: dict[str, Any]) -> dict[str, str]:
    return {block_id: block_text.block_to_text(block) for block_id, _, block in block_text.block_ids(doc)}


def search(session: Session, query: str, *, limit: int = 20) -> SearchResponse:
    """Search instruments *and* report bodies; degrade to LIKE, never 500."""
    settings = get_settings()
    started = time.perf_counter()
    normalized = search_index.normalize_text(query)
    terms = search_index.query_terms(normalized)
    raw_terms = [normalized, *terms] if normalized else []

    use_fts = bool(settings.search_fts) and search_index.fts_ready(session)
    if use_fts:
        candidates = search_index.search_report_blocks(session, normalized, limit=limit)
    else:
        candidates = search_fallback.search_reports(session, normalized, limit=limit)

    instruments = _instrument_hits(session, normalized, limit=8)
    if not instruments and not candidates and not normalized and use_fts:
        # An empty query is a browse request: show the watchlist instead of nothing.
        instruments = [
            InstrumentHit(
                symbol=row.symbol,
                name=row.name,
                market=row.market,
                currency=row.currency,
                score=0.5,
                existing=_has_report(session, row.id),
            )
            for row in _watchlist_instruments(session)
        ]

    doc_cache: dict[str, dict[str, Any]] = {}
    report_rows: dict[str, Report] = {}
    report_hits: list[ReportHit] = []
    #: Reports already represented by a title-only hit (see the branch below).
    title_hit_reports: set[str] = set()
    for candidate in candidates:
        report_id = candidate["report_id"]
        if report_id not in report_rows:
            report = session.get(Report, report_id)
            if report is None or report.deleted_at:
                continue
            report_rows[report_id] = report
            try:
                doc_cache[report_id] = json.loads(report.doc_json or "{}")
            except json.JSONDecodeError:  # pragma: no cover - defensive
                doc_cache[report_id] = {}
        report = report_rows[report_id]
        doc = doc_cache[report_id]
        texts = _block_texts(doc)
        block_id = candidate.get("block_id") or ""
        text = candidate.get("block_text") or texts.get(block_id, "")
        if not text:
            continue
        snippet_terms = raw_terms or terms
        if not _contains_term(text, snippet_terms):
            # `title` is indexed on every block row, so a title-only match arrives as
            # N identical candidates whose block text contains nothing to highlight.
            # Emitting them would break the "snippet marks the match" contract and
            # fill the whole list with one report: keep at most one title hit.
            if report.id in title_hit_reports or not _contains_term(report.title, snippet_terms):
                continue
            title_hit_reports.add(report.id)
            text = report.title
        titles = block_text.section_titles(doc)
        symbol = str((doc.get("instrument") or {}).get("symbol") or candidate.get("symbol") or "")
        report_hits.append(
            ReportHit(
                reportId=report.id,
                symbol=symbol,
                title=report.title,
                sectionId=str(candidate.get("section_id") or ""),
                sectionTitle=titles.get(str(candidate.get("section_id") or ""), ""),
                blockId=block_id,
                snippet=_snippet(text, snippet_terms),
                score=round(abs(float(candidate.get("score") or 0.0)), 4),
            )
        )

    took_ms = int((time.perf_counter() - started) * 1000)
    return SearchResponse(
        query=query,
        instruments=instruments,
        reports=report_hits,
        tookMs=took_ms,
        degraded=not use_fts,
    )


def search_available(session: Session) -> bool:
    """`features.fullTextSearch`: FTS5 detected *and* the tables actually built."""
    return search_index.fts_ready(session)


__all__ = ["SNIPPET_RADIUS", "search", "search_available"]
