"""Authorized, bounded history snapshots for one report version.

Historical answers explain references; they never extend the report's evidence.
The helper does not import ask_service, keeping the dependency direction acyclic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from ..errors import InvalidAskContext, NotFound
from ..models import Report, ReportAsk
from ..security.principal import Principal, service_principal, user_principal
from .block_text import legal_source_ids

MAX_CONTEXT_BYTES = 16384
CONTEXT_FILE = "conversation-context.json"


@dataclass(frozen=True)
class ContextSnapshot:
    """Effective chronological ids and the exact workspace UTF-8 bytes."""

    ids: list[str]
    content: bytes


def available(session: Session) -> bool:
    """Measure migration readiness without selecting unmigrated ORM columns."""
    inspector = inspect(session.connection())
    if not inspector.has_table("report_asks"):
        return False
    return any(
        column["name"] == "context_ask_ids_json"
        for column in inspector.get_columns("report_asks")
    )


def _validate_ids(ids: Any) -> list[str]:
    valid = isinstance(ids, list) and len(ids) <= 6
    valid = valid and all(
        isinstance(item, str) and item.strip() and len(item) <= 64
        for item in ids
    )
    if not valid or len(set(ids)) != len(ids):
        raise InvalidAskContext(detail={"reason": "unavailable"})
    return ids


def _authorized_rows(session, report, ids, principal) -> list[ReportAsk]:
    rows = (
        session.execute(select(ReportAsk).where(ReportAsk.id.in_(ids)))
        .scalars()
        .all()
    )
    owner = principal.user_id if principal.kind == "user" else None
    # Validate the entire authorization boundary before revealing readiness.
    if principal.kind == "guest" or len(rows) != len(ids):
        raise NotFound()
    if any(row.report_id != report.id or row.user_id != owner for row in rows):
        raise NotFound()
    return sorted(rows, key=lambda row: (row.created_at, row.id))


def _project_hits(answer: dict, legal: set[str]) -> list[dict]:
    hits = []
    raw_hits = answer.get("hits")
    if not isinstance(raw_hits, list):
        return hits
    for hit in raw_hits[:3]:
        if not isinstance(hit, dict):
            continue
        excerpt = hit.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            continue
        raw_citations = hit.get("citations")
        citations = raw_citations if isinstance(raw_citations, list) else []
        hits.append(
            {
                "blockId": str(hit.get("blockId") or ""),
                "sectionId": str(hit.get("sectionId") or ""),
                "excerpt": excerpt[:400],
                "citations": [
                    item
                    for item in citations
                    if isinstance(item, str) and item in legal
                ],
            }
        )
    return hits


def _project_turn(row: ReportAsk, legal: set[str]) -> dict:
    answer = row.answer
    raw_text = answer.get("answerText")
    text = raw_text.strip()[:1200] if isinstance(raw_text, str) else ""
    hits = _project_hits(answer, legal) if row.mode == "cite" else []
    usable = bool(text) if row.mode == "deep" else bool(hits)
    if row.status != "done" or not row.grounded or not usable:
        raise InvalidAskContext(detail={"reason": "not_ready"})
    raw_citations = answer.get("citations")
    citations = raw_citations if isinstance(raw_citations, list) else []
    source_ids = [
        item.get("sourceId")
        for item in citations
        if isinstance(item, dict)
        and isinstance(item.get("sourceId"), str)
        and item["sourceId"] in legal
    ]
    for hit in hits:
        source_ids.extend(hit["citations"])
    return {
        "askId": row.id,
        "question": row.question,
        "mode": row.mode,
        "answerText": text if row.mode == "deep" else None,
        "hits": hits,
        "sourceIds": list(dict.fromkeys(source_ids)),
    }


def _serialize(report_id: str, turns: list[dict]) -> bytes:
    return json.dumps(
        {"version": 1, "reportId": report_id, "turns": turns},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def load_context(
    session: Session,
    report: Report,
    ids: list[str],
    principal: Principal,
    *,
    strict: bool = False,
) -> ContextSnapshot:
    """Load owned, ready turns; prune oldest pre-charge, reject drift at runtime.

    Raises NotFound for any authorization failure and InvalidAskContext for
    unusable contents, malformed ids or an unsatisfied complete JSON budget.
    """
    ids = _validate_ids(ids)
    rows = _authorized_rows(session, report, ids, principal) if ids else []
    turns = [_project_turn(row, legal_source_ids(report.doc)) for row in rows]
    effective = [turn["askId"] for turn in turns]
    if strict and effective != ids:
        raise InvalidAskContext(detail={"reason": "unavailable"})
    content = _serialize(report.id, turns)
    while len(content) > MAX_CONTEXT_BYTES and turns and not strict:
        turns.pop(0)
        content = _serialize(report.id, turns)
    if len(content) > MAX_CONTEXT_BYTES or (ids and not turns):
        raise InvalidAskContext(detail={"reason": "too_large"})
    return ContextSnapshot([turn["askId"] for turn in turns], content)


def write_context(
    session: Session,
    report: Report,
    ask: ReportAsk,
    workspace: Path,
) -> bool:
    """Revalidate persisted ids and write exact bytes before rendering a prompt.

    Runtime missing/foreign records become a safe semantic failure; disk I/O
    errors remain ordinary internal failures handled by the existing pipeline.
    """
    try:
        raw = ask.context_ask_ids_json
        ids = _validate_ids(json.loads(raw) if raw is not None else [])
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidAskContext(detail={"reason": "unavailable"}) from exc
    if not ids:
        return False
    actor = service_principal()
    if ask.user_id is not None:
        actor = user_principal(
            user_id=ask.user_id,
            username="",
            role="user",
            session_id=None,
            must_change_password=False,
        )
    try:
        snapshot = load_context(session, report, ids, actor, strict=True)
    except NotFound as exc:
        raise InvalidAskContext(detail={"reason": "unavailable"}) from exc
    (workspace / CONTEXT_FILE).write_bytes(snapshot.content)
    return True
