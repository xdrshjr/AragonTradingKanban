"""`/admin/workflows/{id}/assist` + `/admin/workflows/assists/*` — the AI workflow assistant routes
(report-workflow-graph §3.8 / §5.1). Registered in `main.py` **before** `workflows.router` so the
literal `/assists/…` paths win over `/{id}/…`.

The route-level permission (`admin.workflow.manage`) is the policy table's job. The two
`…/events*` routes follow `routers/asks.py`: the **token** is the authorization for the stream
(`EventSource` cannot send an `Authorization` header), issued by `POST …/events-token`.
The per-admin rate limit (`admin.workflow_assist`, 20/hour) is applied by the service, keyed by
user — not by the policy table, which would key it by IP.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import NotFound, Unauthorized
from ..schemas.run import EventsTokenResponse
from ..schemas.workflow import AssistRequest
from ..security import deps
from ..security.principal import Principal
from ..services import workflow_assist_service
from ..services.sse_token import TOKEN_TTL_SEC, issue_sse_token, verify_sse_token
from ..services.workflow_assist_registry import get_assist_registry

router = APIRouter(prefix="/admin/workflows", tags=["workflow-assist"])


def _need_assist(assist_id: str) -> None:
    if get_assist_registry().get(assist_id) is None:
        raise NotFound("该 AI 助手任务不存在（可能已过期或服务已重启）")


def _sse_frame(event: dict[str, object]) -> str:
    # Same wire format as `routers/runs.py` / `routers/asks.py`; a router may not import another.
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"


@router.post("/assists/{assist_id}/events-token", response_model=EventsTokenResponse)
def assist_events_token(assist_id: str) -> EventsTokenResponse:
    """Bound to the **assist** id, 60 s (same mechanism as asks)."""
    _need_assist(assist_id)
    token, expires_in = issue_sse_token(assist_id)
    return EventsTokenResponse(token=token, expiresIn=expires_in or TOKEN_TTL_SEC)


@router.get("/assists/{assist_id}/events")
async def assist_events(
    assist_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    token: str = Query(default=""),
) -> StreamingResponse:
    _need_assist(assist_id)
    if not verify_sse_token(assist_id, token):
        raise Unauthorized("SSE token 无效或已过期（助手流只接受绑定该 assistId 的签名）")

    async def event_stream() -> AsyncIterator[str]:
        async for event in get_assist_registry().stream(assist_id, after):
            if event is None:
                # A real frame, not a `:` comment: browsers never hand comments to `onmessage`,
                # so the client's stall watchdog could not see them (same reason as `/runs`).
                yield "event: ping\ndata: {}\n\n"
                continue
            if await request.is_disconnected():  # pragma: no cover - client gone
                return
            yield _sse_frame(event)
            if event["type"] == "assist.end":
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/assists/{assist_id}/cancel")
def cancel_assist(assist_id: str) -> dict[str, str]:
    """Kill the process tree; the terminal event is `assist.end{state:"cancelled"}`."""
    _need_assist(assist_id)
    live = workflow_assist_service.request_cancel(assist_id)
    return {"status": "cancelling" if live else "cancelled"}


@router.post("/{template_id}/assist", status_code=status.HTTP_202_ACCEPTED)
async def start_assist(
    template_id: str,
    payload: AssistRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> dict[str, str]:
    """Queue one assistant turn. Async on purpose: the busy check and the registry slot must be
    taken on the event loop with no `await` in between (two POSTs cannot both pass)."""
    assist_id = await workflow_assist_service.start_assist(db, principal, template_id, payload)
    return {"assistId": assist_id}
