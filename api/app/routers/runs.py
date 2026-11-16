"""`/runs` — read state, stream SSE, cancel (spec §7.1, §7.4).

★P1-3: `EventSource` cannot set an `Authorization` header, so SSE validates a
short-lived HMAC `?token=` (60 s, bound to the run id) instead. When `API_TOKEN`
is empty the whole thing is a no-op — local-first is the default deployment.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.session import get_db
from ..errors import RunNotFound, Unauthorized
from ..logging import get_logger
from ..models import Run
from ..schemas.run import CancelResponse, EventsTokenResponse, RunOut, RunUsage
from ..services.run_manager import RunManager, get_manager, stream_events

router = APIRouter(prefix="/runs", tags=["runs"])
logger = get_logger("runs")

TOKEN_TTL_SEC = 60


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
    settings = get_settings()
    secret = settings.api_token.encode("utf-8")
    payload = f"{run_id}.{expires_at}".encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_events_token(run_id: str, token: str) -> bool:
    """`<expiresAt>.<signature>` — checked once, never stored."""
    settings = get_settings()
    if not settings.api_token:
        return True
    try:
        expires_raw, signature = token.split(".", 1)
        expires_at = int(expires_raw)
    except (ValueError, AttributeError):
        return False
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(signature, sign_events_token(run_id, expires_at))


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
    settings = get_settings()
    if not settings.api_token:
        # No auth configured: hand back a token anyway so clients keep one code path.
        return EventsTokenResponse(token=f"{int(time.time()) + TOKEN_TTL_SEC}.open", expiresIn=TOKEN_TTL_SEC)
    expires_at = int(time.time()) + TOKEN_TTL_SEC
    return EventsTokenResponse(token=f"{expires_at}.{sign_events_token(run_id, expires_at)}", expiresIn=TOKEN_TTL_SEC)


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
