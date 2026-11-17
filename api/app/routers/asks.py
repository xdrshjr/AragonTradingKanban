"""`/asks` — report Q&A: `cite` (free, sync) and `deep` (paid, SSE) (§18.5.1).

★V4-P1-4: `deep` streams over `GET /asks/{id}/events?token=` and issues its own
short signature from `POST /asks/{id}/events-token`. Re-using
`/runs/{id}/events-token` was impossible — that endpoint checks run existence first,
so an ask id would always 404.

C-12: an ask never creates a `runs` row. Its lifecycle lives in `AskRegistry` and
its event log is `ask_events`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.session import get_db
from ..errors import ReportNotFound, Unauthorized
from ..models import Report, ReportAsk
from ..schemas.ask import AskListResponse, AskOut, AskQueuedResponse, AskRequest
from ..schemas.run import CancelResponse, EventsTokenResponse
from ..services import ask_service, report_service
from ..services.sse_token import TOKEN_TTL_SEC, issue_sse_token, verify_sse_token

router = APIRouter(tags=["asks"])

#: `deep` requires a grounded baseline; a provisional/degraded report has none (§7.3).
DEEP_BLOCKED_STATUSES = ("provisional", "degraded")


def _load_report(db: Session, report_id: str) -> Report:
    report = report_service.get(db, report_id)
    if report is None:
        raise ReportNotFound()
    return report


def _load_ask(db: Session, ask_id: str) -> ReportAsk:
    ask = db.get(ReportAsk, ask_id)
    if ask is None:
        from ..errors import NotFound

        raise NotFound("该追问不存在")
    return ask


@router.post(
    "/reports/{report_id}/ask",
    response_model=AskOut | AskQueuedResponse,
    status_code=status.HTTP_200_OK,
)
async def ask_report(
    report_id: str,
    payload: AskRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AskOut | AskQueuedResponse:
    """`cite` answers inline (200) — `deep` queues a task and returns 202 (§18.5.1)."""
    report = _load_report(db, report_id)
    question = ask_service.normalize_question(payload.question)
    settings = get_settings()

    if payload.mode == "cite":
        ask = ask_service.ask_cite(db, report, question)
        return ask_service.to_ask_out(ask)

    # ── deep ────────────────────────────────────────────────────────────────
    from ..errors import ReportNotReady, TooManyAsks

    if report.status in DEEP_BLOCKED_STATUSES:
        raise ReportNotReady(detail={"status": report.status})
    used = ask_service.daily_count(db, report.id, mode="deep")
    if used >= settings.ask_daily_limit:
        raise TooManyAsks(
            detail={
                "limit": settings.ask_daily_limit,
                "used": used,
                "retryAfterMs": 24 * 60 * 60 * 1000,
                "suggestMode": "cite",
            }
        )

    ask = await asyncio.to_thread(ask_service.create_deep_ask, db, report, question)
    ask_service.schedule_deep(ask.id)
    # Deep is asynchronous: 202 is the contract, and a client branching on it must
    # never see 200 (§18.5.1).
    response.status_code = status.HTTP_202_ACCEPTED
    return AskQueuedResponse(
        id=ask.id,
        askId=ask.id,
        status="queued",
        streamUrl=f"/api/v1/asks/{ask.id}/events",
    )


@router.get("/reports/{report_id}/asks", response_model=AskListResponse)
def list_asks(
    report_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AskListResponse:
    _load_report(db, report_id)
    rows = (
        db.execute(
            select(ReportAsk)
            .where(ReportAsk.report_id == report_id)
            .order_by(ReportAsk.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return AskListResponse(items=[ask_service.to_ask_out(row) for row in rows], total=len(rows))


@router.get("/asks/{ask_id}", response_model=AskOut)
def get_ask(ask_id: str, db: Session = Depends(get_db)) -> AskOut:
    return ask_service.to_ask_out(_load_ask(db, ask_id))


@router.post("/asks/{ask_id}/events-token", response_model=EventsTokenResponse)
def ask_events_token(ask_id: str, db: Session = Depends(get_db)) -> EventsTokenResponse:
    """Bound to the **ask** id (★V4-P1-4). This endpoint itself needs the Bearer."""
    _load_ask(db, ask_id)
    token, expires_in = issue_sse_token(ask_id)
    return EventsTokenResponse(token=token, expiresIn=expires_in or TOKEN_TTL_SEC)


def _sse_frame(event: dict[str, object]) -> str:
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"


@router.get("/asks/{ask_id}/events")
async def ask_events(
    ask_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    token: str = Query(default=""),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _load_ask(db, ask_id)
    if not verify_sse_token(ask_id, token):
        raise Unauthorized("SSE token 无效或已过期（ask 流只接受绑定该 askId 的签名）")

    async def event_stream() -> AsyncIterator[str]:
        async for event in ask_service.stream_ask_events(ask_id, after):
            if event is None:
                yield ": ping\n\n"
                continue
            if await request.is_disconnected():  # pragma: no cover - client gone
                return
            yield _sse_frame(event)
            if event.get("type") in ("ask.done", "ask.error"):
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/asks/{ask_id}/cancel", response_model=CancelResponse)
def cancel_ask(ask_id: str, db: Session = Depends(get_db)) -> CancelResponse:
    ask = _load_ask(db, ask_id)
    ask_service.request_cancel(db, ask)
    return CancelResponse(status="cancelling", runId=ask.id)
