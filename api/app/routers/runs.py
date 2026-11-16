"""`/runs` — read state, stream SSE, cancel (spec §7.1, §7.4).

★P1-3: `EventSource` cannot set an `Authorization` header, so SSE validates a
short-lived HMAC `?token=` (60 s, bound to the run id) instead. When `API_TOKEN`
is empty the whole thing is a no-op — local-first is the default deployment.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import RunNotFound, Unauthorized
from ..logging import get_logger
from ..models import Run
from ..schemas.run import CancelResponse, EventsTokenResponse, RunOut, RunUsage
from ..services.run_manager import RunManager, get_manager, stream_events
from ..services.sse_token import TOKEN_TTL_SEC, issue_sse_token, sign_sse_token, verify_sse_token

router = APIRouter(prefix="/runs", tags=["runs"])
logger = get_logger("runs")


def to_run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        instrumentId=run.instrument_id,
        status=run.status,  # type: ignore[arg-type]
        stage=run.stage,
        pct=run.pct,
        startedAt=run.started_at,
        finishedAt=run.finished_at,
        exitCode=run.exit_code,
        errorCode=run.error_code,
        errorMessage=run.error_message,
        reportId=run.report_id,
        costUsd=run.cost_usd,
        costKnown=bool(run.cost_known),
        usage=RunUsage(
            inputTokens=run.tokens_in,
            outputTokens=run.tokens_out,
            # Both parts known → the total is known; otherwise leave it null
            # rather than inventing a number (§11.2: never fake a measurement).
            totalTokens=(
                run.tokens_in + run.tokens_out
                if run.tokens_in is not None and run.tokens_out is not None
                else None
            ),
        ),
    )


def sign_events_token(run_id: str, expires_at: int) -> str:
    """Kept as a thin alias — the only implementation is `services.sse_token`."""
    return sign_sse_token(run_id, expires_at)


def verify_events_token(run_id: str, token: str) -> bool:
    return verify_sse_token(run_id, token)


def _load(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise RunNotFound()
    return run


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunOut:
    return to_run_out(_load(db, run_id))


@router.post("/{run_id}/events-token", response_model=EventsTokenResponse)
def events_token(run_id: str, db: Session = Depends(get_db)) -> EventsTokenResponse:
    _load(db, run_id)
    token, expires_in = issue_sse_token(run_id)
    return EventsTokenResponse(token=token, expiresIn=expires_in or TOKEN_TTL_SEC)


def _sse_frame(event: dict[str, object]) -> str:
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    token: str = Query(default=""),
    db: Session = Depends(get_db),
    manager: RunManager = Depends(get_manager),
) -> StreamingResponse:
    run = _load(db, run_id)
    # Accept both `?token=` and `Last-Event-ID`-style resume via `after`.
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and not after:
        # A malformed `Last-Event-ID` just means "start from the beginning".
        with contextlib.suppress(ValueError):
            after = max(after, int(last_event_id))
    if not verify_events_token(run_id, token):
        raise Unauthorized("SSE token 无效或已过期")

    from ..db.session import SessionLocal

    async def event_stream() -> AsyncIterator[str]:
        session = SessionLocal()
        try:
            async for event in stream_events(manager, session, run.id, after):
                if event is None:
                    yield ": ping\n\n"
                    continue
                if await request.is_disconnected():  # pragma: no cover - client gone
                    return
                yield _sse_frame(event)
                if event.get("type") in ("run.done", "run.error"):
                    return
        finally:
            session.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel", response_model=CancelResponse)
async def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    manager: RunManager = Depends(get_manager),
) -> CancelResponse:
    """Return 202-equivalent immediately; the state machine converges to `cancelled`.

    ★P1-9: the transition state is `cancelling` and it must be visible, not a
    lie about having finished. The worker sets the terminal state.
    """
    run = _load(db, run_id)
    manager.request_cancel(db, run.id)
    return CancelResponse(status="cancelling", runId=run.id)
