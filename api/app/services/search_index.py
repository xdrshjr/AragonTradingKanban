"""FTS5 index + Chinese bigram tokenizer — the search substrate (§18.3.4).

Three things live here and nowhere else:

1. **The tokenizer.** SQLite's `unicode61` does not segment Chinese: a whole run
   becomes one token, so `MATCH '毛利'` never hits (measured, v4 evidence E-1).
   Both indexing and querying therefore pre-segment into bigrams.
2. **The DDL**, in `ensure_fts_schema()`. Alembic's `0002` and `tests/conftest.py`
   both call it — the ORM's create-all helper cannot build a virtual table, so without a
   shared function AC-33 would be permanently false (★V4-P1-7).
3. **The indexing calls**, one row per **block** with `section_id` + `block_id`
   (★V4-P0-1). The summary text is *not* produced by `snippet()`; it is cut from
   the block's original text by `search_service`.

Writes happen in the caller's transaction, so "生成完即可搜到" is a transaction
property, not a race (O-4 / AC-33).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from ..logging import get_logger
from . import block_text

logger = get_logger("search_index")

REPORT_FTS = "report_fts"
INSTRUMENT_FTS = "instrument_fts"

CREATE_REPORT_FTS = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {REPORT_FTS} USING fts5(
  report_id  UNINDEXED,
  section_id UNINDEXED,
  block_id   UNINDEXED,
  symbol     UNINDEXED,
  title,
  body,
  tokenize='unicode61 remove_diacritics 0'
)
"""

CREATE_INSTRUMENT_FTS = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {INSTRUMENT_FTS} USING fts5(
  instrument_id UNINDEXED,
  symbol,
  name,
  pinyin,
  tokenize='unicode61'
)
"""

#: ~60 stopwords. Applied to query terms only — the index keeps everything so a
#: stopword-only question can still fall back to a raw scan.
STOPWORDS: frozenset[str] = frozenset(
    [
        "的", "了", "是", "在", "和", "与", "或", "及", "也", "就", "都", "而", "被", "把", "对",
        "这", "那", "有", "无", "为", "以", "于", "从", "到", "由", "其", "之", "等", "个", "些",
        "请", "问", "请问", "一下", "多少", "如何", "什么", "怎么", "是否", "哪些", "哪", "为什么",
        "可以", "能否", "应该", "目前", "现在", "情况", "方面", "怎么", "怎样", "吗", "呢", "吧",
        "大概", "具体", "主要", "相关", "the", "a", "an", "of", "is", "are", "and", "or", "to",
        "what", "why", "how", "which", "does", "do", "in", "on", "for", "with",
    ]
)

_LATIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-]*")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

try:  # optional, and honestly reported by `/capabilities` when missing
    from pypinyin import lazy_pinyin as _lazy_pinyin
except ImportError:  # pragma: no cover - exercised by the degradation test
    _lazy_pinyin = None


# ── tokenizing ───────────────────────────────────────────────────────────────


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def tokenize_zh_bigram(value: str) -> list[str]:
    """Deterministic bigram segmentation — same input, same output, always.

    Chinese runs → overlapping bigrams (`毛利率下滑` → `毛利/利率/率下/下滑`), a
    one-character run stays a single token; Latin/digit runs are lowercased and
    kept whole (`62.1%`, `pe`).
    """
    normalized = normalize_text(value)
    if not normalized:
        return []
    tokens: list[str] = []
    position = 0
    length = len(normalized)
    while position < length:
        latin = _LATIN_RE.match(normalized, position)
        if latin:
            tokens.append(latin.group(0).lower())
            position = latin.end()
            continue
        cjk = _CJK_RE.match(normalized, position)
        if cjk:
            run = cjk.group(0)
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
            position = cjk.end()
            continue
        position += 1
    return [token for token in tokens if token]


def query_terms(value: str, *, limit: int = 24) -> list[str]:
    """Query-side terms: bigrams minus stopwords, de-duplicated, order kept."""
    terms: list[str] = []
    for token in tokenize_zh_bigram(value):
        if token in STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
    if not terms:  # everything was a stopword — better a weak query than none
        terms = tokenize_zh_bigram(value)
    return terms[:limit]


def match_expression(terms: Iterable[str]) -> str:
    """An FTS5 `MATCH` string: quoted phrases OR-joined.

    Quoting is not cosmetic: a raw `62.1%` would be parsed as FTS5 syntax. Each
    phrase therefore stays inside double quotes, and the whole thing degrades to
    an empty string when there is nothing to search for.
    """
    quoted = [f'"{term.replace(chr(34), "")}"' for term in terms if term]
    return " OR ".join(quoted)


# ── capability detection & DDL ───────────────────────────────────────────────


def _connection(target: Session | Connection) -> Connection:
    return target.connection() if isinstance(target, Session) else target


def detect_fts(target: Session | Connection | None = None) -> bool:
    """`PRAGMA compile_options` must contain `ENABLE_FTS5` (R-18 / V4-P1-8)."""
    if target is not None:
        conn = _connection(target)
        if conn.dialect.name != "sqlite":
            return False
        try:
            rows = conn.execute(text("PRAGMA compile_options")).fetchall()
        except Exception:  # pragma: no cover - very old sqlite
            return False
        return any("ENABLE_FTS5" in str(row[0]).upper() for row in rows)
    # Standalone probe on a throwaway in-memory database.
    import sqlite3

    try:
        with sqlite3.connect(":memory:") as probe:
            rows = probe.execute("PRAGMA compile_options").fetchall()
    except sqlite3.Error:  # pragma: no cover - defensive
        return False
    return any("ENABLE_FTS5" in str(row[0]).upper() for row in rows)


def fts_ready(target: Session | Connection) -> bool:
    """True when the virtual tables actually exist (i.e. `ensure_fts_schema` ran)."""
    conn = _connection(target)
    if conn.dialect.name != "sqlite":
        return False
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"), {"name": REPORT_FTS}
    ).fetchone()
    return row is not None


def ensure_fts_schema(target: Session | Connection) -> bool:
    """Idempotent native DDL — the **only** definition of the FTS tables.

    Returns False (without raising) when the SQLite build has no FTS5, which is
    exactly what keeps `alembic upgrade head` succeeding on such a build
    (★V4-P1-8 / C-11).
    """
    conn = _connection(target)
    if conn.dialect.name != "sqlite":
        logger.info("非 SQLite 方言，跳过 FTS5 建表（/search 走 LIKE 兜底）")
        return False
    if not detect_fts(conn):
        logger.warning("当前 SQLite 构建不含 ENABLE_FTS5，跳过 FTS5 建表（/search 走 LIKE 兜底）")
        return False
    conn.execute(text(CREATE_REPORT_FTS))
    conn.execute(text(CREATE_INSTRUMENT_FTS))
    return True


def drop_fts_schema(target: Session | Connection) -> None:
    """Test/downgrade helper — the ORM create-all/drop-all pair never sees these tables."""
    conn = _connection(target)
    if conn.dialect.name != "sqlite":
        return
    conn.execute(text(f"DROP TABLE IF EXISTS {REPORT_FTS}"))
    conn.execute(text(f"DROP TABLE IF EXISTS {INSTRUMENT_FTS}"))


# ── pinyin ───────────────────────────────────────────────────────────────────


def pinyin_available() -> bool:
    return _lazy_pinyin is not None


def pinyin_text(name: str) -> str:
    """`贵州茅台` → `guizhoumaotai gzmt` (full spelling + initials)."""
    if _lazy_pinyin is None or not name:
        return ""
    syllables = _lazy_pinyin(name)
    full = "".join(syllables)
    initials = "".join(syllable[:1] for syllable in syllables if syllable)
    return f"{full} {initials}".strip()


# ── indexing ─────────────────────────────────────────────────────────────────


def reindex_report_payload(
    target: Session | Connection,
    report_id: str,
    doc_json: str,
    title: str,
) -> int:
    """Index one report from its stored JSON — the projection lives here only.

    Taking the raw payload (not a `Report` row) lets the Alembic migration backfill
    with the exact same code the runtime uses, instead of a second SQL-side
    implementation of "how a block becomes text" (§18.3.4).
    """
    import json

    delete_report_index(target, report_id)
    if not fts_ready(target):
        return 0
    try:
        doc: dict[str, Any] = json.loads(doc_json or "{}")
    except json.JSONDecodeError:  # pragma: no cover - defensive
        return 0
    symbol = str((doc.get("instrument") or {}).get("symbol") or "")
    heading = str(title or doc.get("headline") or "")
    written = 0
    conn = _connection(target)
    for block_id, section_id, block in block_text.block_ids(doc):
        body = " ".join(tokenize_zh_bigram(block_text.block_to_text(block)))
        if not body:
            continue
        conn.execute(
            text(
                f"INSERT INTO {REPORT_FTS}(report_id, section_id, block_id, symbol, title, body) "
                "VALUES (:report_id, :section_id, :block_id, :symbol, :title, :body)"
            ),
            {
                "report_id": report_id,
                "section_id": section_id,
                "block_id": block_id,
                "symbol": symbol,
                "title": heading,
                "body": body,
            },
        )
        written += 1
    return written


def reindex_report(session: Session, report: Any) -> int:
    """Delete and re-insert one report's block rows, in the caller's transaction.

    `report` may be a `Report` row or a report id string. Rows are per **block**
    so that ranking is block-level and the hit can carry `sectionId`/`blockId`
    (★V4-P0-1).
    """
    from ..models import Report

    if isinstance(report, str):
        row = session.get(Report, report)
        if row is None:
            return 0
    else:
        row = report
    return reindex_report_payload(session, row.id, row.doc_json, str(row.title or ""))


def delete_report_index(target: Session | Connection, report_id: str) -> int:
    """Remove every index row for a report (soft delete and re-index both use it)."""
    if not fts_ready(target):
        return 0
    result = _connection(target).execute(
        text(f"DELETE FROM {REPORT_FTS} WHERE report_id = :rid"), {"rid": report_id}
    )
    return int(result.rowcount or 0)


def reindex_instrument_payload(
    target: Session | Connection,
    instrument_id: str,
    symbol: str,
    name: str,
) -> bool:
    if not fts_ready(target):
        return False
    conn = _connection(target)
    conn.execute(text(f"DELETE FROM {INSTRUMENT_FTS} WHERE instrument_id = :iid"), {"iid": instrument_id})
    conn.execute(
        text(
            f"INSERT INTO {INSTRUMENT_FTS}(instrument_id, symbol, name, pinyin) "
            "VALUES (:iid, :symbol, :name, :pinyin)"
        ),
        {
            "iid": instrument_id,
            "symbol": symbol,
            # ★ Same segmentation as the query side: `query_terms()` asks for bigrams,
            # so a verbatim `贵州茅台` (one unicode61 token) could never match `茅台`.
            # Writing the pre-segmented form is what makes the name dimension work at
            # all, exactly as `report_body` already does.
            "name": " ".join(tokenize_zh_bigram(name)),
            "pinyin": pinyin_text(name),
        },
    )
    return True


def reindex_instrument(session: Session, instrument: Any) -> bool:
    """Refresh one instrument's row in `instrument_fts` (symbol / name / pinyin)."""
    from ..models import Instrument

    if isinstance(instrument, str):
        row = session.get(Instrument, instrument)
        if row is None:
            return False
    else:
        row = instrument
    return reindex_instrument_payload(session, row.id, row.symbol, row.name)


def search_report_blocks(session: Session, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Candidate blocks, best first. FTS is a *filter* here, never a formatter."""
    terms = query_terms(query)
    expression = match_expression(terms)
    if not expression or not fts_ready(session):
        return []
    rows = session.execute(
        text(
            f"SELECT report_id, section_id, block_id, bm25({REPORT_FTS}) AS score "
            f"FROM {REPORT_FTS} WHERE {REPORT_FTS} MATCH :expr "
            f"ORDER BY bm25({REPORT_FTS}) LIMIT :limit"
        ),
        {"expr": expression, "limit": limit},
    ).fetchall()
    return [
        {
            "report_id": str(row[0]),
            "section_id": str(row[1] or ""),
            "block_id": str(row[2] or ""),
            # bm25() in SQLite returns a negative number (lower is better).
            "score": float(row[3]) if row[3] is not None else 0.0,
        }
        for row in rows
    ]


def search_instruments(session: Session, query: str, *, limit: int = 8) -> list[str]:
    """Instrument ids matched by symbol / name / pinyin, best first."""
    terms = query_terms(query)
    expression = match_expression(terms)
    if not expression or not fts_ready(session):
        return []
    rows = session.execute(
        text(
            f"SELECT instrument_id FROM {INSTRUMENT_FTS} WHERE {INSTRUMENT_FTS} MATCH :expr "
            f"ORDER BY bm25({INSTRUMENT_FTS}) LIMIT :limit"
        ),
        {"expr": expression, "limit": limit},
    ).fetchall()
    return [str(row[0]) for row in rows]


__all__ = [
    "INSTRUMENT_FTS",
    "REPORT_FTS",
    "STOPWORDS",
    "delete_report_index",
    "detect_fts",
    "drop_fts_schema",
    "ensure_fts_schema",
    "fts_ready",
    "match_expression",
    "normalize_text",
    "pinyin_available",
    "pinyin_text",
    "query_terms",
    "reindex_instrument",
    "reindex_report",
    "search_instruments",
    "search_report_blocks",
    "tokenize_zh_bigram",
]
