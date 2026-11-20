"""`/me` — the signed-in person's own quota (spec §24.5.2).

Account-scoped, so only a real `user` principal belongs here: the API-token principal has no
account (and no quota — it is never charged), a guest has no session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import Forbidden, NotFound, Unauthorized
from ..schemas.me import (
    QuotaLedgerPage,
    QuotaRequestCreate,
    QuotaRequestList,
    QuotaRequestOut,
    QuotaStatus,
)
from ..security import deps
from ..security.principal import Principal
from ..services import audit_service, quota_requests, quota_service, user_service
from ..services.audit_service import AuditTarget

router = APIRouter(prefix="/me", tags=["me"])


def _require_user(principal: Principal) -> Principal:
    if principal.kind == "user":
        return principal
    if principal.kind == "guest":
        raise Unauthorized(reason="no_session")
    raise Forbidden(permission="account.self")


@router.get("/quota", response_model=QuotaStatus)
def get_quota(
    db: Session = Depends(get_db), principal: Principal = Depends(deps.get_principal)
) -> QuotaStatus:
    _require_user(principal)
    user = user_service.get_by_id(db, principal.user_id)
    if user is None:
        raise NotFound()
    return QuotaStatus.model_validate(quota_service.status(db, user))


@router.get("/quota-ledger", response_model=QuotaLedgerPage)
def get_ledger(
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, max_length=20),
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> QuotaLedgerPage:
    _require_user(principal)
    items, cursor = quota_service.list_ledger(db, principal.user_id or "", limit=limit, before=before)
    return QuotaLedgerPage.model_validate({"items": items, "nextCursor": cursor})


@router.post("/quota-requests", response_model=QuotaRequestOut, status_code=201)
def create_quota_request(
    payload: QuotaRequestCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> QuotaRequestOut:
    _require_user(principal)
    row = quota_requests.create(
        db, principal, resource=payload.resource, amount=payload.amount, reason=payload.reason
    )
    audit_service.record(
        db,
        principal,
        "quota.request.create",
        AuditTarget("quota_request", row.id, principal.username),
        {"resource": row.resource, "amount": row.amount},
    )
    db.commit()
    return QuotaRequestOut.model_validate(quota_requests.to_dict(row))


@router.get("/quota-requests", response_model=QuotaRequestList)
def list_quota_requests(
    db: Session = Depends(get_db), principal: Principal = Depends(deps.get_principal)
) -> QuotaRequestList:
    _require_user(principal)
    rows = quota_requests.list_for_user(db, principal.user_id or "")
    return QuotaRequestList.model_validate({"items": [quota_requests.to_dict(row) for row in rows]})
