"""`/health` — liveness plus a real database probe (AC-1)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import __version__
from ..db.session import get_db
from ..schemas.common import HealthResponse

router = APIRouter(tags=["health"])

_STARTED_AT = time.time()


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - only on a broken database
        db_status = "error"
    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        version=__version__,
        db=db_status,
        uptimeSec=round(time.time() - _STARTED_AT, 1),
    )
