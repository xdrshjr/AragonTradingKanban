"""`LIKE` fallback search for builds without FTS5 — R-18's degraded path.

Deliberately dumb and bounded: it scans at most `LIKE_SCAN_LIMIT` live reports and
matches on the **original block text** (no index, no bigrams). At the scale R-18
assumes (a few hundred reports) this stays under 50 ms, and — crucially — it
returns 200 with `degraded=true` instead of a 500.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..logging import get_logger
from ..models import Report
from . import block_text, search_index

logger = get_logger("search_fallback")

#: Beyond this the LIKE scan stops being interactive; the capability flag already
#: tells the UI that full-text search is unavailable, so a short list is honest.
LIKE_SCAN_LIMIT = 200


def _raw_terms(query: str) -> list[str]:
    """Coarse terms for substring matching: whole query + bigrams."""
    raw = search_index.normalize_text(query).lower()
    terms = [raw] if raw else []
    for term in search_index.query_terms(query):
        if term not in terms:
            terms.append(term)
    return [term for term in terms if term]


def search_reports(session: Session, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return block-level hits shaped like the FTS path."""
    terms = _raw_terms(query)
    if not terms:
        return []
    rows = (
        session.execute(
            select(Report)
            .where(Report.deleted_at.is_(None))
            .order_by(Report.generated_at.desc())
            .limit(LIKE_SCAN_LIMIT)
        )
        .scalars()
        .all()
    )
    hits: list[dict[str, Any]] = []
    for report in rows:
        try:
            doc = json.loads(report.doc_json or "{}")
        except json.JSONDecodeError:  # pragma: no cover - defensive
            continue
        symbol = str((doc.get("instrument") or {}).get("symbol") or "")
        for block_id, section_id, block in block_text.block_ids(doc):
            text = block_text.block_to_text(block)
            lowered = text.lower()
            matched = sum(1 for term in terms if term in lowered)
            if not matched:
                continue
            hits.append(
                {
                    "report_id": report.id,
                    "section_id": section_id,
                    "block_id": block_id,
                    "symbol": symbol,
                    "score": -1.0 * (matched / len(terms)),
                    "block_text": text,
                }
            )
    hits.sort(key=lambda hit: hit["score"])
    return hits[:limit]


def search_instruments(session: Session, query: str, *, limit: int = 8) -> list[str]:
    """Instrument ids whose symbol contains the query (name/pinyin already handled)."""
    from ..models import Instrument

    needle = search_index.normalize_text(query).lower()
    if not needle:
        return []
    rows = session.execute(select(Instrument.id, Instrument.symbol).limit(500)).fetchall()
    matched = [str(row[0]) for row in rows if needle in str(row[1]).lower()]
    return matched[:limit]


__all__ = ["LIKE_SCAN_LIMIT", "search_instruments", "search_reports"]
