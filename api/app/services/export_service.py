"""CSV export of the admin lists (spec §25.3.5a, D-13, RV-Y9).

Why this is more than `csv.writer`:

* **Constant memory.** The response is a *generator* that reads the table in keyset chunks of
  `CHUNK` (500) rows; nothing ever holds the whole result. 500, not 1000: each chunk makes one
  `quota_lite` call whose `IN (…)` must stay under SQLite's old 999-variable ceiling.
* **Own sessions.** The generator runs after the handler has returned, i.e. outside the
  request-scoped session's lifetime, so every chunk opens (and closes) its **own** short
  `SessionLocal()`. Nothing about its correctness depends on when the framework closes
  `Depends(get_db)`.
* **Leave a trace before the first byte.** The rate-limit hit and the `admin.export` audit row
  are committed *before* the streaming response is returned. An export that dies half-way still
  has its audit row; an export that is refused (429) writes nothing.
* **Bounded.** At most `EXPORT_MAX_ROWS` rows. `COUNT(*)` is taken first so the
  `X-Export-Truncated: 1` header can be decided before the body starts; the reader *also*
  stops at the same number, so the header and the body cannot disagree even if rows are added
  while streaming.
* **Safe cells.** `csv_safe()` defuses spreadsheet formula injection; the header whitelist is
  asserted by a reflection test to contain nothing credential-like.
* **Excel-friendly.** UTF-8 with a BOM, `\\r\\n` line ends (RFC 4180).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session as db_session
from ..models import AuditLog
from ..security import clock, ratelimit
from ..security.principal import Principal
from . import admin_user_service, audit_service
from .audit_service import AuditTarget

#: Hard ceiling per export (D-13). A module attribute (not a constant folded into closures) so
#: tests can lower it.
EXPORT_MAX_ROWS = 50_000
CHUNK = 500
BOM = "﻿"

#: Whitelisted headers, in order. Never add a credential-like column (AC-231 reflects on these).
USER_COLUMNS: tuple[str, ...] = (
    "username",
    "displayName",
    "role",
    "status",
    "createdAt",
    "lastLoginAt",
    "dailyLimit",
    "usedToday",
    "bonus",
    "hasRecoveryKey",
    "invitedBy",
)
AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "at",
    "actor",
    "actorRole",
    "action",
    "targetType",
    "targetName",
    "ip",
    "requestId",
    "detail",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(cell: Any) -> Any:
    """Defuse CSV/formula injection: a text cell that starts with `= + - @ TAB CR` gets a
    leading `'` so Excel/Sheets/Numbers read it as text. Non-text cells pass through
    (`None` -> empty, `bool` -> `true`/`false`)."""
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "true" if cell else "false"
    if isinstance(cell, str) and cell.startswith(_FORMULA_PREFIXES):
        return "'" + cell
    return cell


def _line(cells: list[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow([csv_safe(cell) for cell in cells])
    return buffer.getvalue()


def _user_cells(item: dict[str, Any]) -> list[Any]:
    quota = (item.get("quota") or {}).get("report") or {}
    return [
        item["username"],
        item["displayName"],
        item["role"],
        item["status"],
        item["createdAt"],
        item["lastLoginAt"],
        quota.get("limit"),
        quota.get("usedToday", 0),
        quota.get("bonus", 0),
        item.get("hasRecoveryKey", False),
        item["invitedBy"],
    ]


def _audit_cells(row: AuditLog) -> list[Any]:
    detail = audit_service.parse_detail(row.detail_json)
    return [
        row.id,
        row.at,
        row.actor_name,
        row.actor_role,
        row.action,
        row.target_type,
        row.target_name,
        row.ip,
        row.request_id,
        # Defence in depth: the audit writer already drops secret-looking keys.
        json.dumps(audit_service.sanitize_detail(detail), ensure_ascii=False, separators=(",", ":")) if detail else "",
    ]


def filename(kind: str, now: datetime | None = None) -> str:
    """`aragon-<kind>-YYYYMMDD.csv`, the date in `QUOTA_TZ` (the admin's "today")."""
    day = clock.local_day_key(now or clock.now(), get_settings().quota_tz).replace("-", "")
    return f"aragon-{kind}-{day}.csv"


def _stream(
    header: tuple[str, ...],
    read_chunk: Callable[[Session, str | None, int], tuple[list[list[Any]], str | None]],
    *,
    row_cap: int,
) -> Iterator[str]:
    """Header + chunks, each chunk read in its own short session, stopping at `row_cap` rows."""
    yield BOM + _line(list(header))
    written = 0
    cursor: str | None = None
    while written < row_cap:
        size = min(CHUNK, row_cap - written)
        # Looked up at call time (not imported by name) so a test can re-point the factory.
        session = db_session.SessionLocal()
        try:
            rows, cursor = read_chunk(session, cursor, size)
        finally:
            session.close()
        if not rows:
            return
        yield "".join(_line(row) for row in rows)
        written += len(rows)
        if cursor is None or len(rows) < size:
            return


def _respond(kind: str, body: Iterator[str], *, truncated: bool) -> StreamingResponse:
    headers = {"Content-Disposition": f'attachment; filename="{filename(kind)}"'}
    if truncated:
        headers["X-Export-Truncated"] = "1"
    return StreamingResponse(body, media_type="text/csv; charset=utf-8", headers=headers)


def _begin(db: Session, principal: Principal, kind: str, total: int, filters: dict[str, Any]) -> int:
    """Rate-limit, audit and **commit** — all before the first byte. Returns the row cap."""
    ratelimit.check("admin.export", principal.user_id or principal.kind)
    audit_service.record(
        db,
        principal,
        "admin.export",
        AuditTarget("export", None, kind),
        {"kind": kind, "rows": min(total, EXPORT_MAX_ROWS), "filters": {k: v for k, v in filters.items() if v}},
    )
    db.commit()
    return EXPORT_MAX_ROWS


def users_csv(
    db: Session,
    principal: Principal,
    *,
    q: str | None,
    role: str | None,
    status: str | None,
    locked: bool,
) -> StreamingResponse:
    moment = clock.now()
    filters = admin_user_service.user_filters(q=q, role=role, status=status, locked=locked, now=moment)
    total = 0 if filters is None else admin_user_service.count_users(db, filters)
    cap = _begin(
        db, principal, "users", total, {"q": q, "role": role, "status": status, "locked": "1" if locked else None}
    )

    def read(session: Session, cursor: str | None, size: int) -> tuple[list[list[Any]], str | None]:
        if filters is None:
            return [], None
        items, last = admin_user_service.export_rows(session, filters, size=size, before=cursor, now=moment)
        return [_user_cells(item) for item in items], last

    return _respond("users", _stream(USER_COLUMNS, read, row_cap=cap), truncated=total > cap)


def audit_csv(
    db: Session,
    principal: Principal,
    *,
    action: str | None,
    actor: str | None,
    target: str | None,
    since: str | None,
    until: str | None,
) -> StreamingResponse:
    # Snapshot bound: rows written after this point (including this export's own audit row) are
    # outside the export, so `COUNT` and the body agree.
    max_id = int(db.execute(select(func.max(AuditLog.id))).scalar_one() or 0)
    statement_args = {
        "action": action,
        "actor": actor,
        "target": target,
        "since": since,
        "until": until,
        "max_id": max_id,
    }
    total = int(
        db.execute(select(func.count()).select_from(audit_service.filtered(**statement_args).subquery())).scalar_one()
    )
    cap = _begin(
        db, principal, "audit", total, {"action": action, "actor": actor, "target": target, "from": since, "to": until}
    )

    def read(session: Session, cursor: str | None, size: int) -> tuple[list[list[Any]], str | None]:
        rows, _next = audit_service.query(session, **statement_args, limit=size, before=cursor, max_limit=CHUNK)
        return [_audit_cells(row) for row in rows], (audit_service.encode_cursor(rows[-1]) if rows else None)

    return _respond("audit", _stream(AUDIT_COLUMNS, read, row_cap=cap), truncated=total > cap)
