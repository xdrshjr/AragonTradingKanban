"""`/runs` — read state, stream SSE, cancel (spec §7.1, §7.4).

★P1-3: `EventSource` cannot set an `Authorization` header, so SSE validates a
short-lived HMAC `?token=` (60 s, bound to the run id) instead. When `API_TOKEN`
is empty the whole thing is a no-op — local-first is the default deployment.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..agent.failure import hint_for
from ..db.session import get_db
from ..errors import RunNotFound, Unauthorized
from ..logging import get_logger
from ..models import Run
from ..schemas.cost import EstimateRequest, EstimateResponse
from ..schemas.run import CancelResponse, EventsTokenResponse, RunOut, RunUsage
from ..services import cost_service
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
        # ★v7（§20.5.1）：8 个新字段。`hint` 由服务端按码查表下发（H-12），
        # 前端不再拼字符串——只有服务端知道 exit code 与引擎日志。
        retryCount=int(run.retry_count or 0),
        structuredAttempts=run.structured_attempts,
        agentProvider=run.agent_provider,
        agentModel=run.agent_model,
        costSource=run.cost_source,
        costUsdEst=run.cost_usd_est,
        failureFingerprint=run.failure_fingerprint,
        hint=hint_for(run.error_code) if run.error_code else None,
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


@router.post("", include_in_schema=False)
def runs_root_is_not_a_creation_entry() -> Response:
    """★v6（V6-P0-1 / C-13 / AC-56）：`POST /api/v1/runs` 显式为 **405**。

    生成只能从 `POST /api/v1/instruments/{id}/reports` 发起（那里才有
    `active_run_for()` / `MAX_CONCURRENT_RUNS` / `ux_runs_active` 三道并发保证）。
    
    为什么要写这一个不做事的路由：没有它时，`/runs` 根路径根本不存在，
    调用方拿到的是 **404 not_found** —— “地址写错了”的语义。而这里真正要说的是
    “地址对了，但这个动作不允许”：那些想“顺手补一个 POST /runs”的人，
    应该在第一秒就被告知该去哪里写，而不是发现“原来没有这个接口”然后自己造一个。（R-31）
    """
    raise HTTPException(
        status_code=405,
        detail="生成只能从 POST /api/v1/instruments/{instrument_id}/reports 发起（唯一创建入口）",
    )


@router.post("/estimate", response_model=EstimateResponse)
def estimate_run(payload: EstimateRequest, db: Session = Depends(get_db)) -> EstimateResponse:
    """★v6（V6-P0-1）：**新增子路径**，不是第二个创建入口。

    与既有的 `POST /{run_id}/events-token` 段数不同，二者不冲突。
    `estimatedUsd: null` 是合法的 200 —— "无法预估"是一个答案，不是错误。
    """
    payload_dict = cost_service.estimate(db, payload.symbol, payload.kind)
    return EstimateResponse.model_validate(payload_dict)


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
                    # ★v12（S6 / §22.3.7(c) / AC-131）：保活必须发**真实帧**，不是注释帧。
                    #
                    # 注释帧 `: ping\n\n` 是合法 SSE，但浏览器**不会**把它交给
                    # `EventSource.onmessage`（也不触发任何命名事件监听器）⇒ 前端无论怎么
                    # 记 "最后一次收到帧的时刻"，一次安静的长模型调用（没有 tool 事件、
                    # 没有 progress）都够不到那个心跳，于是看门狗必然在 45 秒后误判断流、
                    # 主动重连——把"卡在 60%"换成"45 秒一次无谓重连"。这正是 V11-P1-5
                    # 证伪的那一版。
                    #
                    # 发真实帧之后，"服务端活着但安静"与"服务端死了"才可区分：前者能收到
                    # `ping`，后者连 TCP 都没有。事件名进 `contracts/run_error_codes.json`
                    # 的 `sseEvents`（三向断言会红，所以前端也必须订阅它）。
                    # 不带 `id:`——保活不是可重放的事件，给它一个 seq 会污染 `?after=`
                    # 的续传语义。
                    yield "event: ping\ndata: {}\n\n"
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
