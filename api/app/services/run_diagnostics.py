"""Run 诊断的**落库与聚合**（spec §20.4 / V8-P2-10）。

★v8（V8-P2-10）边界写死：这个模块**不产生任何判定**。

* 失败码与 hint 的表在 `agent/failure.py`（`RUN_ERROR_CODES` / `hint_for`）；
* 失败指纹的**公式**也在 `agent/failure.py`（`failure_fingerprint`）；
* 成本三态的判定在 `agent/cost_model.py`（`resolve_cost`）。

这里只回答"把这些事实写到哪一行、以及怎么查回来"：

1. `apply_agent_result()` —— 把 `AgentResult` 上的诊断字段写进 `runs` 行
   （`retry_count / structured_attempts / agent_provider / agent_model`）。
   没有 `system` 事件时**留空，不猜**（§20.3.7）。
2. `record_failure()` —— 写 `failure_fingerprint` 与 `cost_source/cost_usd_est`。
3. `fingerprint_repeat_count()` —— "同标的最近 N 次同指纹"的聚合查询。
   查询形态是"最近 N 条同标的的 run"（已有 `ix_runs_instrument_started`），
   **不是**"按指纹查"，所以指纹列**不建索引**（§20.6.3：不要为一个不存在的查询建索引）。
4. `recent_failure()` —— `/capabilities.engine.lastFailure` 的只读来源。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.cost_model import CostResolution
from ..agent.failure import hint_for
from ..agent.runner_base import AgentResult
from ..logging import get_logger
from ..models import Run

logger = get_logger("run_diagnostics")

#: "同一原因失败三次"的阈值（前端只在达到它时把失败卡片换成更强的措辞）。
REPEAT_ALERT_THRESHOLD = 3

#: `lastFailure` / 指纹聚合看最近多少条 run。
RECENT_RUNS = 20


def apply_agent_result(run: Run, result: AgentResult) -> None:
    """把 `AgentResult` 上的诊断字段抄进 `runs` 行。**只抄，不解释。**"""
    run.retry_count = int(result.retries or 0)
    if result.structured_attempts is not None:
        run.structured_attempts = int(result.structured_attempts)
    engine = result.engine_info or {}
    provider = engine.get("provider") or result.provider
    model = engine.get("model") or result.model
    # ★v8（V8-P2-9）：这两行**替换**了 `run.provider` / `run.model`（那两行是
    # 静默丢弃的属性赋值，E-48）。`runs` 表没有 provider/model 列，只有这两列。
    run.agent_provider = (provider or None) if provider else None
    run.agent_model = (model or None) if model else None
    run.exit_code = result.exit_code if result.exit_code is not None else run.exit_code


def apply_cost(run: Run, resolution: CostResolution) -> None:
    """三态的列分工（§20.3.5）：`reported` 只落 `cost_usd/cost_known`，
    `estimated` 只落 `cost_usd_est`，`unknown` 两者皆空。**互斥，不重复计。**"""
    if resolution.source == "reported":
        run.cost_usd = resolution.reported_usd
        run.cost_known = True
        run.cost_usd_est = None
    elif resolution.source == "estimated":
        # 引擎没报成本：`cost_usd` 保持 NULL（AC-78），估算改写另一列。
        run.cost_usd = None
        run.cost_known = False
        run.cost_usd_est = resolution.estimated_usd
    else:
        run.cost_usd = None
        run.cost_known = False
        run.cost_usd_est = None
    run.cost_source = resolution.source


def record_failure(
    session: Session,
    run: Run,
    *,
    fingerprint: str | None,
    resolution: CostResolution | None = None,
) -> Run:
    """失败也要记账（H-9：一次失败同样是一次付费调用）。

    `fingerprint` 由调用方从 `agent/failure.failure_fingerprint()` 拿到——**公式不在
    这里**，否则"同因"的判据会出现第二份实现。
    """
    run.failure_fingerprint = fingerprint
    if resolution is not None:
        apply_cost(run, resolution)
    session.commit()
    return run


def fingerprint_repeat_count(session: Session, instrument_id: str, fingerprint: str | None) -> int:
    """同标的最近 `RECENT_RUNS` 条 run 里，与 `fingerprint` 相同的次数。"""
    if not fingerprint:
        return 0
    rows = (
        session.execute(
            select(Run.failure_fingerprint)
            .where(Run.instrument_id == instrument_id, Run.failure_fingerprint.is_not(None))
            .order_by(Run.started_at.desc())
            .limit(RECENT_RUNS)
        )
        .scalars()
        .all()
    )
    return sum(1 for value in rows if value == fingerprint)


def recent_failure(session: Session) -> dict[str, Any] | None:
    """最近一次**失败**的 `{code, hint, at, count}`；没有失败 → `None`。

    `count` 是该指纹在**同一标的**最近 `RECENT_RUNS` 条 run 里的次数（"这不是偶发失败"
    的判据）。**只读、不据此降级**（H-15）。
    """
    row = (
        session.execute(
            select(Run)
            .where(Run.error_code.is_not(None))
            .order_by(Run.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None or not row.error_code:
        return None
    count = fingerprint_repeat_count(session, row.instrument_id, row.failure_fingerprint)
    return {
        "code": row.error_code,
        "hint": hint_for(row.error_code),
        "at": row.finished_at or row.started_at,
        "count": count,
        # ★v8 评审：阈值由服务端判定一次，前端只转述（与 H-12「前端不拼文案」同一条纪律）。
        # 在此之前 `REPEAT_ALERT_THRESHOLD` 是个没人用的常量，前端各自估一个数。
        "alert": count >= REPEAT_ALERT_THRESHOLD,
    }


__all__ = [
    "RECENT_RUNS",
    "REPEAT_ALERT_THRESHOLD",
    "apply_agent_result",
    "apply_cost",
    "fingerprint_repeat_count",
    "recent_failure",
    "record_failure",
]
