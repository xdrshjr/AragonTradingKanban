"""Report versioning — the "one latest per instrument" invariant (spec §6.2, §7.1).

Every write goes through `create_version`, which in a single transaction clears
`is_latest`, computes `max(version)+1`, inserts, supersedes the old row, and
points the run at the new report. The unique partial index `ux_reports_latest`
is the backstop if any of that is ever bypassed.

Deletion is **soft** (★P1-1): `deleted_at` is stamped, the row disappears from
listings, and if it was the latest the pointer moves to the newest surviving
version. Hard deletes happen only in the retention job.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..logging import get_logger
from ..models import Instrument, Report, Run
from ..schemas.report import ReportQuality, ReportSummary
from . import cost_service, search_index

logger = get_logger("report_service")

MAX_VERSIONS_PER_INSTRUMENT = 20


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return uuid.uuid4().hex


def summarize(report: Report) -> ReportSummary:
    doc: dict[str, Any] = {}
    try:
        doc = json.loads(report.doc_json)
    except (json.JSONDecodeError, TypeError):  # pragma: no cover - defensive
        doc = {}
    quality_payload = report.quality or {}
    return ReportSummary(
        id=report.id,
        instrumentId=report.instrument_id,
        version=report.version,
        status=report.status,  # type: ignore[arg-type]
        title=report.title,
        headline=str(doc.get("headline") or "")[:200],
        stance=report.stance or (doc.get("rating") or {}).get("stance"),  # type: ignore[arg-type]
        asOf=report.as_of,
        generatedAt=report.generated_at,
        isLatest=bool(report.is_latest),
        pinned=bool(report.pinned),
        archived=bool(report.archived),
        provisional=bool(doc.get("provisional")) or report.status == "provisional",
        groundedRatio=report.grounded_ratio,
        quality=ReportQuality(
            groundedRatio=quality_payload.get("groundedRatio", report.grounded_ratio or 1.0) or 1.0,
            missingSections=quality_payload.get("missingSections") or [],
            prunedBlocks=quality_payload.get("prunedBlocks") or 0,
            ungroundedCount=quality_payload.get("ungroundedCount") or 0,
            source=quality_payload.get("source") or "aragon",
            # ★v6: an absent key stays absent (old reports render without a badge,
            # and `comparability.same` becomes null rather than a guess).
            grounding=quality_payload.get("grounding"),
            engine=quality_payload.get("engine"),
        ),
        provider=report.provider,
        model=report.model,
        costUsd=report.cost_usd,
        costKnown=bool(report.cost_known),
        tokensIn=report.tokens_in,
        tokensOut=report.tokens_out,
        durationMs=report.duration_ms,
        docBytes=report.doc_bytes,
    )


def create_version(
    session: Session,
    instrument: Instrument,
    doc: dict[str, Any],
    *,
    status: str,
    quality: dict[str, Any] | None = None,
    run_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    cost_known: bool = False,
    cost_source: str | None = None,
    cost_usd_est: float | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
    is_provisional: bool = False,
) -> Report:
    """Insert the next version and make it latest (unless provisional).

    Provisional versions never compete for `is_latest` (§8.1) — they appear in
    the version switcher as 临时版 and only render by default when they are the
    only thing the instrument has.
    """
    payload = json.dumps(doc, ensure_ascii=False)
    quality_payload = quality or {}
    generated_at = _iso()
    as_of = str(doc.get("asOf") or generated_at)
    rating = doc.get("rating") or {}
    title_parts = [
        str((doc.get("instrument") or {}).get("name") or instrument.name),
        "投研报告",
    ]
    title = " · ".join(part for part in title_parts if part)[:250]

    current_max = session.execute(
        select(func.max(Report.version)).where(Report.instrument_id == instrument.id)
    ).scalar_one()
    next_version = int(current_max or 0) + 1

    make_latest = not is_provisional
    if make_latest:
        session.execute(
            update(Report)
            .where(Report.instrument_id == instrument.id, Report.is_latest == 1)
            .values(is_latest=0, status="superseded")
        )

    report = Report(
        id=_new_id(),
        instrument_id=instrument.id,
        version=next_version,
        status="provisional" if is_provisional else ("degraded" if status == "degraded" else "ready"),
        title=title,
        stance=rating.get("stance"),
        as_of=as_of,
        generated_at=generated_at,
        doc_json=payload,
        doc_bytes=len(payload.encode("utf-8")),
        grounded_ratio=quality_payload.get("groundedRatio"),
        quality_json=json.dumps(quality_payload, ensure_ascii=False) if quality_payload else None,
        provider=provider,
        model=model,
        run_id=run_id,
        cost_usd=cost_usd,
        cost_known=cost_known,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        is_latest=make_latest,
        pinned=False,
        archived=False,
    )
    session.add(report)
    session.flush()

    # ★V4-P0-1 / O-4: the index rows are written in this very transaction, so
    # "生成完即可搜到" is a transaction property instead of a background race.
    # The projection itself lives in `search_index` (block-level, section+block ids).
    # A savepoint is what makes the promise below真实: catching the exception without
    # one leaves the connection in an aborted-transaction state, so the *report*
    # commit would then raise `PendingRollbackError` — "search never blocks a
    # generation" would be a comment, not a property.
    try:
        with session.begin_nested():
            search_index.reindex_report_payload(session, report.id, payload, title)
    except Exception:  # pragma: no cover - search must never block a generation
        logger.exception("FTS 索引写入失败 report=%s（报告本身仍会落库）", report.id)

    # ★v6 G3 / O-4: the ledger row is projected in this very transaction, and the
    # write is guarded by its own savepoint (`cost_service.record`), so "账本失败
    # 不得回滚报告" is a property of the code rather than a comment. Not a second
    # `begin_nested()` around this call — the template above is already the shape.
    # ★v7（V8-P1-4）：`source` 必填。深答那条路（`ask_service`）也要传，否则
    # 已知成本的行会被默认值静默标成 `unknown`（R-42 / AC-86）。
    # ★v8 评审（R-42 的第二个入口）：`cost_source` 的默认值 `"unknown"` 本身就是同一个
    # 坑——调用方传了 `cost_usd` 却忘了 `source`，`record()` 会把 `usd` 丢弃（三态互斥），
    # 于是那一行变成"未知成本"而**没有任何报错**。所以默认值改成 `None` 并在下面推导：
    # 三态的真源仍然只有 `COST_SOURCES`，这里只是不让"漏传"变成"错标"。
    resolved_source = cost_source or (
        "reported"
        if cost_known and cost_usd is not None
        else ("estimated" if cost_usd_est is not None else "unknown")
    )
    cost_service.record(
        session,
        kind="run",
        ref_id=run_id or report.id,
        symbol=instrument.symbol,
        report_id=report.id,
        model=model,
        usd=cost_usd,
        known=bool(cost_known),
        usd_est=cost_usd_est,
        source=resolved_source,
    )

    if run_id:
        session.execute(update(Run).where(Run.id == run_id).values(report_id=report.id))
    session.commit()
    # The supersede above went through a Core UPDATE, which the identity map does
    # not see (`expire_on_commit=False`); without this, a list loaded earlier in
    # the request would still report two `is_latest=1` rows.
    session.expire_all()

    logger.info(
        "report created instrument=%s version=%s status=%s latest=%s bytes=%s",
        instrument.id,
        next_version,
        report.status,
        make_latest,
        report.doc_bytes,
    )
    _trim_versions(session, instrument.id)
    return report


def _trim_versions(session: Session, instrument_id: str, keep: int | None = None) -> int:
    """R9: keep the newest N unpinned versions; hard-delete the rest.

    `keep` is resolved at call time so the limit can be configured (and tested)
    without re-importing the module.
    """
    limit = MAX_VERSIONS_PER_INSTRUMENT if keep is None else keep
    rows = (
        session.execute(
            select(Report)
            .where(
                Report.instrument_id == instrument_id,
                Report.pinned == 0,
                Report.deleted_at.is_(None),
            )
            .order_by(Report.version.desc())
        )
        .scalars()
        .all()
    )
    removed = 0
    for row in rows[limit:]:
        session.execute(update(Run).where(Run.report_id == row.id).values(report_id=None))
        search_index.delete_report_index(session, row.id)
        session.delete(row)
        removed += 1
    if removed:
        session.commit()
        logger.info("trimmed %s old versions for %s", removed, instrument_id)
    return removed


def get(session: Session, report_id: str, *, include_deleted: bool = False) -> Report | None:
    report = session.get(Report, report_id)
    if report is None:
        return None
    if report.deleted_at and not include_deleted:
        return None
    return report


def latest_of(session: Session, instrument_id: str) -> Report | None:
    """The version the UI opens by default.

    Falls back to the newest provisional when nothing else exists — a user with
    only a failed attempt still gets something to read (★P1-10).
    """
    report = (
        session.execute(
            select(Report)
            .where(
                Report.instrument_id == instrument_id,
                Report.is_latest == 1,
                Report.deleted_at.is_(None),
            )
            .order_by(Report.version.desc())
        )
        .scalars()
        .first()
    )
    if report is not None:
        return report
    return (
        session.execute(
            select(Report)
            .where(Report.instrument_id == instrument_id, Report.deleted_at.is_(None))
            .order_by(Report.version.desc())
        )
        .scalars()
        .first()
    )


def list_versions(
    session: Session,
    instrument_id: str,
    *,
    limit: int = 20,
    include_archived: bool = False,
) -> tuple[list[Report], int]:
    conditions = [Report.instrument_id == instrument_id, Report.deleted_at.is_(None)]
    if not include_archived:
        conditions.append(Report.archived == 0)
    total = session.execute(select(func.count()).select_from(Report).where(*conditions)).scalar_one()
    rows = (
        session.execute(
            select(Report)
            .where(*conditions)
            .order_by(Report.pinned.desc(), Report.version.desc())
            .limit(max(1, min(limit, 100)))
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def set_flags(
    session: Session,
    report: Report,
    *,
    pinned: bool | None = None,
    archived: bool | None = None,
    set_latest: bool | None = None,
) -> Report:
    if pinned is not None:
        report.pinned = pinned
    if archived is not None:
        report.archived = archived
        if archived:
            # An archived version can never be the front page (§7.1 PATCH).
            report.is_latest = False
            report.status = "superseded"
    if set_latest:
        if report.deleted_at:
            raise ValueError("cannot promote a deleted report")
        # Demote the incumbent first: `ux_reports_latest` allows exactly one.
        session.execute(
            update(Report)
            .where(
                Report.instrument_id == report.instrument_id,
                Report.id != report.id,
                Report.is_latest == 1,
            )
            .values(is_latest=0, status="superseded")
        )
        report.is_latest = True
        if report.status == "superseded":
            report.status = "ready"
    session.commit()
    # Same reason as `create_version`: the demotion is a Core UPDATE.
    session.expire_all()
    return report


def soft_delete(session: Session, report: Report) -> None:
    """★P1-1: stamp `deleted_at`, keep the audit trail, move the latest pointer."""
    report.deleted_at = _iso()
    was_latest = bool(report.is_latest)
    report.is_latest = False
    # Same transaction as the flag change: a soft-deleted report must stop being
    # findable *immediately* (AC-33).
    search_index.delete_report_index(session, report.id)
    session.flush()

    if was_latest:
        successor = (
            session.execute(
                select(Report)
                .where(
                    Report.instrument_id == report.instrument_id,
                    Report.id != report.id,
                    Report.deleted_at.is_(None),
                    Report.archived == 0,
                    Report.status != "provisional",
                )
                .order_by(Report.version.desc())
            )
            .scalars()
            .first()
        )
        if successor is not None:
            successor.is_latest = True
            if successor.status == "superseded":
                successor.status = "ready"
    session.commit()
    logger.info("report soft-deleted id=%s was_latest=%s", report.id, was_latest)


def count_active_runs(session: Session) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Run).where(Run.status.in_(("queued", "running", "cancelling")))
        ).scalar_one()
    )


def active_run_for(session: Session, instrument_id: str) -> Run | None:
    return (
        session.execute(
            select(Run)
            .where(
                Run.instrument_id == instrument_id,
                Run.status.in_(("queued", "running", "cancelling")),
            )
            .order_by(Run.started_at.desc())
        )
        .scalars()
        .first()
    )


def next_version_number(session: Session, instrument_id: str) -> int:
    current_max = session.execute(
        select(func.max(Report.version)).where(Report.instrument_id == instrument_id)
    ).scalar_one()
    return int(current_max or 0) + 1
