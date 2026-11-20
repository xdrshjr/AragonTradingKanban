"""Quota requests (spec §24.3.8(f), AC-193).

A user who has run out files **one** request (a partial unique index, `ux_quota_req_pending`,
is the real guard — the in-code check only gives a friendly error); an admin approves (which
writes a `request_approved` bonus row **in the same transaction**) or rejects (with a reason
the user can read).

Every function here `flush`es and never `commit`s; the router records the audit row and
commits, so the request, the ledger row and the audit entry succeed or fail together.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.base import iso
from ..errors import AppError, Conflict, NotFound, QuotaRequestPending
from ..models import QuotaRequest, User
from ..models.quota import QUOTA_RESOURCES
from ..security import clock
from ..security.principal import Principal
from . import quota_service

AMOUNT_MIN, AMOUNT_MAX = 1, 20
REASON_MAX = 200
STATUSES = ("pending", "approved", "rejected")


def _validation(message: str, field: str) -> AppError:
    return AppError(
        message, code="validation_error", status_code=422, detail={"errors": [{"loc": field, "msg": message}]}
    )


def to_dict(row: QuotaRequest, *, user: User | None = None, user_runs_7d: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row.id,
        "userId": row.user_id,
        "resource": row.resource,
        "amount": row.amount,
        "reason": row.reason,
        "status": row.status,
        "granted": row.granted,
        "resolvedBy": row.resolved_by,
        "resolvedAt": row.resolved_at,
        "resolutionNote": row.resolution_note,
        "createdAt": row.created_at,
    }
    if user is not None:
        payload["user"] = {"id": user.id, "username": user.username, "displayName": user.display_name}
    if user_runs_7d is not None:
        payload["userRuns7d"] = user_runs_7d
    return payload


def create(db: Session, principal: Principal, *, resource: str, amount: int, reason: str) -> QuotaRequest:
    user_id = quota_service.constrained_user_id(principal)
    if user_id is None:
        raise NotFound()
    if resource not in QUOTA_RESOURCES:
        raise _validation("resource 只能是 report 或 deep_ask", "resource")
    if not (AMOUNT_MIN <= int(amount) <= AMOUNT_MAX):
        raise _validation(f"amount 必须在 {AMOUNT_MIN}..{AMOUNT_MAX}", "amount")
    text = (reason or "").strip()
    if not text or len(text) > REASON_MAX:
        raise _validation(f"理由必须为 1..{REASON_MAX} 个字符", "reason")

    existing = quota_service._pending_request(db, user_id)  # noqa: SLF001 - same package
    if existing is not None:
        raise QuotaRequestPending(request_id=existing.id)
    row = QuotaRequest(
        id=uuid.uuid4().hex,
        user_id=user_id,
        resource=resource,
        amount=int(amount),
        reason=text,
        status="pending",
        created_at=iso(clock.now()),
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError as exc:  # two tabs, one click each: the index decides
        existing = quota_service._pending_request(db, user_id)  # noqa: SLF001
        raise QuotaRequestPending(request_id=existing.id if existing else None) from exc
    db.flush()
    return row


def list_for_user(db: Session, user_id: str, *, limit: int = 50) -> list[QuotaRequest]:
    return list(
        db.execute(
            select(QuotaRequest)
            .where(QuotaRequest.user_id == user_id)
            .order_by(QuotaRequest.created_at.desc(), QuotaRequest.id.desc())
            .limit(max(1, min(limit, 200)))
        )
        .scalars()
        .all()
    )


def list_queue(
    db: Session, *, status: str = "pending", limit: int = 50, before: str | None = None
) -> tuple[list[QuotaRequest], int, str | None]:
    """`(rows, total, next_cursor)`; oldest first for `pending` (a queue), newest first otherwise.

    The cursor is `"{created_at}|{id}"`, the same shape the other keyset lists use."""
    if status not in STATUSES:
        raise _validation("status 只能是 pending / approved / rejected", "status")
    limit = max(1, min(int(limit), 200))
    total = int(
        db.execute(select(func.count()).select_from(QuotaRequest).where(QuotaRequest.status == status)).scalar_one()
    )
    query = select(QuotaRequest).where(QuotaRequest.status == status)
    ascending = status == "pending"
    if before:
        created, _, row_id = before.partition("|")
        if ascending:
            query = query.where(
                (QuotaRequest.created_at > created)
                | ((QuotaRequest.created_at == created) & (QuotaRequest.id > row_id))
            )
        else:
            query = query.where(
                (QuotaRequest.created_at < created)
                | ((QuotaRequest.created_at == created) & (QuotaRequest.id < row_id))
            )
    order = (
        (QuotaRequest.created_at.asc(), QuotaRequest.id.asc())
        if ascending
        else (QuotaRequest.created_at.desc(), QuotaRequest.id.desc())
    )
    rows = db.execute(query.order_by(*order).limit(limit + 1)).scalars().all()
    page = list(rows[:limit])
    next_cursor = f"{page[-1].created_at}|{page[-1].id}" if len(rows) > limit and page else None
    return page, total, next_cursor


def resolve(
    db: Session,
    *,
    actor: Principal,
    request_id: str,
    decision: str,
    grant: int | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> QuotaRequest:
    if decision not in ("approve", "reject"):
        raise _validation("decision 只能是 approve 或 reject", "decision")
    row = db.get(QuotaRequest, request_id)
    if row is None:
        raise NotFound("额度申请不存在")
    if row.status != "pending":
        raise Conflict("该申请已被处理", detail={"requestId": row.id, "status": row.status})

    moment = now or clock.now()
    note_text = (note or "").strip()[:200] or None
    if decision == "reject":
        if not note_text:
            raise _validation("拒绝申请必须填写理由", "note")
        row.status = "rejected"
        row.granted = 0
    else:
        amount = int(grant if grant is not None else row.amount)
        if not (AMOUNT_MIN <= amount <= AMOUNT_MAX):
            raise _validation(f"grant 必须在 {AMOUNT_MIN}..{AMOUNT_MAX}", "grant")
        target = db.get(User, row.user_id)
        if target is None:
            raise NotFound("申请人不存在")
        quota_service.grant(
            db,
            actor=actor,
            target_user=target,
            resource=row.resource,
            amount=amount,
            note=(f"申请 {row.id[:8]}" + (f"：{note_text}" if note_text else ""))[:120],
            reason="request_approved",
            now=moment,
        )
        row.status = "approved"
        row.granted = amount
    row.resolved_by = actor.user_id
    row.resolved_at = iso(moment)
    row.resolution_note = note_text
    db.flush()
    return row
