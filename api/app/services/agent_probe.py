"""引擎自检的编排：单飞 + TTL + 身份五元组 + 落库（★v9 K2 / §21.3.3）。

依赖方向：`services/` 可以 import `agent/`（`test_boundaries.py` 规则 2），所以
`agent_probe` 在这里 `import agent.probe`，而不是反过来。

三条不可协商的口径：

1. **探针不阻断生成**（硬约束 2「降级永不上抛」）。它只让 `capabilities` 变红/变绿。
2. **单飞**：进程内 `_inflight` 标志。第二次并发请求得
   `409 probe_in_progress`，**不会**多 spawn 一个子进程（AC-96）。
3. **与真实生成互斥**（V10-P2-11）：`run_manager` 有活动 run 时**不 spawn**，
   直接返回 `busy` / 409 `probe_busy`。理由：单 worker 下两者会各 spawn 各烧 token，
   更麻烦的是探针的 `system` 事件会经 `note_base_url()` 回填进程级能力快照，
   把"这个端点生成了那份报告"的实证链搅浑。

**失效的五元组**是本模块的要点：只看"上次 ok"是不够的 —— 用户改完 `ARAGON_MODEL`
之后，上一条 ok 的记录会撒谎。所以 `verified()` 必须比较 `identity()`。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..agent import preflight
from ..agent.preflight import check_aragon
from ..agent.probe import ProbeOutcome, probe_identity, run_probe
from ..config import get_settings
from ..logging import get_logger
from ..models import EngineProbe

logger = get_logger("agent_probe_service")

#: 单飞的**唯一**状态位：进程内的 `_inflight` 布尔（与"API 必须单 worker"的前提一致，
#: 见 README）。这里刻意**不再**放一把 `asyncio.Lock`：锁要在事件循环里等，而"第二次
#: 并发请求立刻回 409"是一条**非阻塞**的产品结论，两者混用只会让第二次请求排队，
#: 而排队的结果是它等第一次跑完再 spawn 一次——正好是 AC-96 要禁止的那件事。
_inflight = False
#: 上一次成功/失败的结论（内存 + 数据库两份；数据库那份才是跨重启的事实）。
_last: ProbeOutcome | None = None
#: 上一次判定时用的五元组（用来回答 `identity_changed`）。
_last_identity: dict[str, Any] | None = None


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def probes_dir() -> Path:
    """探针工作区**与 `runs/` 分开**（O-21：报告路径里不会出现探针）。"""
    return get_settings().workspace_dir / "probes"


def identity() -> dict[str, Any]:
    """`{provider, model, baseUrlHost, cli, schemaSha1}` —— 任一变化即视为失效。"""
    return probe_identity(check_aragon())


def _row_to_outcome(row: EngineProbe) -> ProbeOutcome:
    return ProbeOutcome(
        ok=bool(row.ok),
        code=row.code,
        message=row.detail,
        ms=int(row.ms or 0),
        turns=row.turns,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        structure_attempts=row.structure_attempts,
        engine={
            "provider": row.provider,
            "model": row.model,
            "baseUrlHost": row.base_url_host,
        },
        cli=row.cli,
        schema_sha1=row.schema_sha1,
        probed_at=row.created_at,
    )


def last_probe(db: Session) -> ProbeOutcome | None:
    """最近一条探针（按 `created_at` 倒序）。空表 → `None`（"从未验证"是答案，不是空串）。"""
    row = (
        db.execute(select(EngineProbe).order_by(EngineProbe.created_at.desc()).limit(1)).scalars().first()
    )
    return _row_to_outcome(row) if row is not None else None


def _last_identity_row(db: Session) -> EngineProbe | None:
    return db.execute(select(EngineProbe).order_by(EngineProbe.created_at.desc()).limit(1)).scalars().first()


def verified(
    db: Session,
    *,
    ttl: int | None = None,
) -> tuple[bool, str | None, str | None]:
    """→ `(verified, verifiedAt, staleReason)`。

    `staleReason ∈ {never, expired, identity_changed, failed}`；`verified=True` 时它是 `None`。
    判据三条同时成立才绿：最近一条 `ok=1`、`created_at` 在 TTL 内、五元组与当前配置一致。
    """
    settings = get_settings()
    ttl = max(0, int(settings.agent_probe_ttl_sec if ttl is None else ttl))
    row = _last_identity_row(db)
    if row is None:
        return False, None, "never"
    if not row.ok:
        return False, row.created_at, "failed"
    if (row.schema_sha1 or "") != (identity().get("schemaSha1") or ""):
        # 探针 schema 变了：上一条验证不再代表现在。
        return False, row.created_at, "identity_changed"
    probe = _row_to_outcome(row)
    if not _identity_matches(probe):
        return False, row.created_at, "identity_changed"
    if _age_sec(row.created_at) > ttl:
        return False, row.created_at, "expired"
    return True, row.created_at, None


def _identity_matches(probe: ProbeOutcome) -> bool:
    current = identity()
    observed = probe.engine or {}
    for key in ("provider", "model", "baseUrlHost"):
        if (observed.get(key) or "") != (current.get(key) or ""):
            return False
    return (probe.cli or "") == (current.get("cli") or "")


def _age_sec(created_at: str | None) -> float:
    if not created_at:
        return float("inf")
    try:
        moment = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    return (datetime.now(UTC) - moment).total_seconds()


def _record(db: Session, outcome: ProbeOutcome, *, reason: str) -> None:
    """落一条 `engine_probes`（只追加；随后按硬上限裁剪）。"""
    settings = get_settings()
    engine = outcome.engine or {}
    row = EngineProbe(
        id=_row_id(),
        created_at=outcome.probed_at,
        ok=1 if outcome.ok else 0,
        code=outcome.code,
        ms=outcome.ms,
        turns=outcome.turns,
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        structure_attempts=outcome.structure_attempts,
        cli=outcome.cli,
        provider=(engine.get("provider") or None),
        model=(engine.get("model") or None),
        base_url_host=(engine.get("baseUrlHost") or None),
        schema_sha1=outcome.schema_sha1,
        reason=reason if reason in ("manual", "pre_run", "panel") else "manual",
        # `detail` 是"已打码的 message（≤1000 字符）"（0005 的列注释）。打码在
        # `failure.mask_secrets()` 里做过，长度在这里兜住——探针的 `internal` 分支会把
        # `f"{type(exc).__name__}: {exc}"` 原样带出来，那一句没有上限。
        detail=((outcome.message or "")[:1000] or None),
    )
    db.add(row)
    db.commit()
    _trim(db, keep=max(1, int(settings.agent_probe_keep)))
    # `/capabilities` 读的是进程级能力快照里的这两项，落库之后立刻同步。
    preflight.note_verified(outcome.ok, outcome.probed_at)


def _trim(db: Session, *, keep: int) -> None:
    """★v10 修订（V10-P2-4）：SQL **必须写全** `FROM engine_probes`。

    v9 草案少写了 `FROM`（`DELETE FROM engine_probes WHERE id NOT IN (SELECT id FROM …)` 之外
    还有一处写成 `SELECT id FROM engine_probes` 少了来源表的那种形态），SQLite 会报
    `no such table: id` —— 一行都删不掉，报的还是一个看不懂的错。

    `LIMIT` 放在子查询里，SQLite 与 PostgreSQL 同一条语句。
    """
    db.execute(
        delete(EngineProbe).where(
            EngineProbe.id.not_in(select(EngineProbe.id).order_by(EngineProbe.created_at.desc()).limit(keep))
        )
    )
    db.commit()


def _row_id() -> str:
    """`engine_probes.id` 是 `VARCHAR(36)` —— `str(uuid4())` 正好 36 字符。"""
    return str(uuid.uuid4())


def busy() -> bool:
    """是否有真实生成正在跑（与探针互斥，V10-P2-11）。"""
    from .run_manager import get_manager

    manager = get_manager()
    return manager.has_active_runs()


async def probe_now(
    db: Session,
    *,
    reason: str = "manual",
    force: bool = False,
) -> ProbeOutcome:
    """已被验证且未过期且身份未变 → 直接返回上一条；否则跑一次并落库。

    **不抛**：任何异常都变成一条 `ok=false` 的记录（硬约束 2）。
    """
    global _inflight, _last, _last_identity

    if _inflight:
        # 单飞：第二次并发请求由 router 翻成 `409 probe_in_progress`。
        return ProbeOutcome(ok=False, code="probe_in_progress", message="另一次引擎自检正在运行")

    if not force:
        ok, verified_at, stale = verified(db)
        if ok and _last is not None:
            return _last
        if ok:
            current = last_probe(db)
            if current is not None:
                _last = current
                return current

    if busy():
        # 有真实生成在跑：**不 spawn**（会把 token 与实证链一起搅乱）。
        return ProbeOutcome(ok=False, code="busy", ms=0, message="有生成任务正在运行，稍后自检")

    _inflight = True
    try:
        workspace = probes_dir() / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        outcome = await run_probe(workspace, capability=check_aragon())
        _record(db, outcome, reason=reason)
        _last = outcome
        _last_identity = identity()
        return outcome
    finally:
        _inflight = False


def reset_state() -> None:
    """Test hook：清掉进程内的单飞标志与缓存（数据库不动）。"""
    global _inflight, _last, _last_identity
    _inflight = False
    _last = None
    _last_identity = None


def cleanup(db: Session) -> int:
    """按 `RUN_RETENTION_DAYS` 同窗口清理探针，并保证硬上限。"""
    settings = get_settings()
    cutoff = _iso(datetime.now(UTC) - timedelta(days=settings.run_retention_days))
    rows = db.execute(select(EngineProbe).where(EngineProbe.created_at < cutoff)).scalars().all()
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    _trim(db, keep=max(1, int(settings.agent_probe_keep)))
    return len(rows)


__all__ = [
    "busy",
    "cleanup",
    "identity",
    "last_probe",
    "probe_now",
    "probes_dir",
    "reset_state",
    "verified",
]
