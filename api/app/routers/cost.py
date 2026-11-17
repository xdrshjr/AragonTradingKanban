"""`/cost` — the cost panel's read side (spec §19.5.1, M17).

Three endpoints, all read-only except the budget override:

* `GET /cost/summary?window=` — the visible line a long-term user needs;
* `GET /cost/entries` — the ledger rows behind it (bounded);
* `POST /cost/budget` — the override the panel writes; takes effect immediately
  because `cost_service` reads it on every check (no cache).

`POST /runs/estimate` lives in `routers/runs.py` next to the other `/runs` routes
(★v6 V6-P0-1: it is a *new sub-path*, so it cannot collide with anything, and it
is not a second run-creation entry).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.cost import (
    BudgetUpdate,
    CostEntriesResponse,
    CostEntry,
    CostModelRow,
    CostSummary,
)
from ..services import cost_service

router = APIRouter(prefix="/cost", tags=["cost"])


def _to_summary(payload: dict) -> CostSummary:
    data = dict(payload)
    data["byModel"] = [CostModelRow(**row) for row in data.get("byModel") or []]
    return CostSummary.model_validate(data)


@router.get("/summary", response_model=CostSummary)
def cost_summary(
    window: str = Query(default="30d", pattern="^(7d|30d|90d|mtd|ytd)$"),
    db: Session = Depends(get_db),
) -> CostSummary:
    return _to_summary(cost_service.summary(db, window))


@router.get("/entries", response_model=CostEntriesResponse)
def cost_entries(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> CostEntriesResponse:
    rows = cost_service.entries(db, limit)
    items = [
        CostEntry(
            id=row.id,
            kind=row.kind,
            refId=row.ref_id,
            symbol=row.symbol,
            reportId=row.report_id,
            model=row.model,
            usd=row.usd,
            known=bool(row.known),
            # ★v7（H-4）：估算值与来源三态，只增不改。
            usdEst=row.usd_est,
            source=row.source or "unknown",
            createdAt=row.created_at,
        )
        for row in rows
    ]
    return CostEntriesResponse(items=items, total=len(items))


@router.post("/budget", response_model=CostSummary)
def set_budget(payload: BudgetUpdate, db: Session = Depends(get_db)) -> CostSummary:
    """`monthlyUsd = 0` → unlimited. The write is a `Setting` row, not an env edit."""
    cost_service.set_budget(db, payload.monthlyUsd)
    return _to_summary(cost_service.summary(db, "30d"))
