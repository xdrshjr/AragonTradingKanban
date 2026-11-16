"""`GET /search` — one endpoint for the rail, `⌘K` and deep links (§7.1, §18.5.3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import InvalidQuery
from ..schemas.search import SearchResponse
from ..services import search_service

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> SearchResponse:
    query = (q or "").strip()
    if len(query) > 200:
        raise InvalidQuery("查询过长（≤200 字符）")
    return search_service.search(db, query, limit=limit)
