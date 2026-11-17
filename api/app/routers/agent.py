"""`/agent/probe` —— 引擎自检的两个端点（★v9 K2 / §21.5.1）。

```
POST /api/v1/agent/probe   body: {"force": false}
  200  ProbeOut（成功或失败都是 200：失败的**结论**也是结论）
  409  probe_in_progress  |  probe_busy
  503  agent_mock_mode    |  probe_disabled
GET  /api/v1/agent/probe
  200  {last, verified, verifiedAt, staleReason, probeCostHintUsd, probeTtlSec}
```

为什么"探针失败"也是 200：这不是一次**请求**失败，而是一次**测量**的结论。
把它做成 4xx/5xx 会让前端把"端点此刻不通"渲染成"接口坏了"。

三个 409/503 都是 `errors.py` 里的 `AppError` 子类，**不裸抛 `HTTPException`**
（V10-P2-6）：裸抛会被通用处理器压成 `conflict` / `service_unavailable`，前端分不出来。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..agent.pricing import cost_for_tokens
from ..config import get_settings
from ..db.session import get_db
from ..errors import AgentMockMode, ProbeBusy, ProbeDisabled, ProbeInProgress
from ..logging import get_logger
from ..schemas.probe import ProbeListOut, ProbeOut, ProbeRequest
from ..services import agent_probe

router = APIRouter(tags=["agent"])

logger = get_logger("agent_router")


def _ensure_enabled() -> None:
    """探针的前置条件：不是 mock 模式、开关没关。两条都是 503，但码不同（动作不同）。"""
    settings = get_settings()
    if settings.agent_mode == "mock":
        raise AgentMockMode()
    if not settings.agent_probe_enabled:
        raise ProbeDisabled()


def _cost_hint(db: Session) -> float | None:
    """★v10（V10-P2-2）：优先用上一条探针**实测**的 tokens，没有历史才回退常量。

    把 7301/159 写死会让提示长期低报 1.5 倍（真机 argv 路径实测 11251 in + 215 out），
    而"点一次自检多贵"正是用户敢不敢点它的唯一依据。
    """
    last = agent_probe.last_probe(db)
    if last is not None and (last.tokens_in or last.tokens_out) is not None:
        model = (last.engine or {}).get("model")
        measured = cost_for_tokens(model, int(last.tokens_in or 0), int(last.tokens_out or 0))
        if measured is not None:
            return round(float(measured), 6)
    settings = get_settings()
    fallback = cost_for_tokens(settings.aragon_model, 7_300, 200)
    return round(float(fallback), 6) if fallback is not None else None


@router.post("/agent/probe", response_model=ProbeOut)
async def run_engine_probe(
    payload: ProbeRequest | None = None,
    db: Session = Depends(get_db),
) -> ProbeOut:
    """跑一次最小真实生成（≈10 秒 / ≈$0.0016）。失败也是 200 —— 见模块文档。"""
    _ensure_enabled()
    force = bool(payload.force) if payload is not None else False
    outcome = await agent_probe.probe_now(db, reason="manual", force=force)
    if outcome.code == "probe_in_progress":
        raise ProbeInProgress()
    if outcome.code == "busy":
        raise ProbeBusy()
    return ProbeOut.model_validate(outcome.as_payload())


@router.get("/agent/probe", response_model=ProbeListOut)
def read_engine_probe(db: Session = Depends(get_db)) -> ProbeListOut:
    """`GET` 是纯读：**不 spawn**（面板打开一次不该花掉一次自检）。"""
    ok, verified_at, stale_reason = agent_probe.verified(db)
    last = agent_probe.last_probe(db)
    settings = get_settings()
    return ProbeListOut(
        last=ProbeOut.model_validate(last.as_payload()) if last is not None else None,
        verified=ok,
        verifiedAt=verified_at,
        staleReason=stale_reason,
        probeCostHintUsd=_cost_hint(db),
        probeTtlSec=max(0, int(settings.agent_probe_ttl_sec)),
    )
