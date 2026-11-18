"""Run orchestration — the six stages, the event log, and the locks (§3.2, §7.4).

Design constraints this file exists to honour:

* **Single worker** (★P1-5). Orchestration is in-process; run state lives in the
  DB so a restart can reconcile, and SSE replays from `run_events` so a late
  subscriber is never starved.
* **One live run per instrument** (★P1-9). An in-process lock *and* the partial
  unique index `ux_runs_active`, including the `cancelling` hand-over state.
* **Cancel means cancel** (★P1-9). `argoning.CMD` is a cmd.exe shim, so the kill
  targets the process tree.
* **Never end empty-handed** (★P1-10). Every failure path first persists a
  readable `provisional` report, then records the error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..agent import build_runner, preflight
from ..agent.cost_model import resolve_cost
from ..agent.failure import failure_fingerprint, hint_for, is_retryable
from ..agent.runner_base import AgentError, AgentEvent, AgentEventType, AgentRequest, AgentResult
from ..config import get_settings
from ..logging import get_logger
from ..models import Instrument, Run, RunEvent
from ..schemas.run import ACTIVE, STAGE_BOUNDS, STAGE_ORDER, STAGE_START, stage_end_pct, stage_label
from . import cost_service, report_service, report_validator, run_diagnostics
from . import snapshot as snapshot_service

logger = get_logger("run_manager")

MAX_CONCURRENT_RUNS = 2
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "timeout", "budget_exceeded")

#: ★v14（U3 / §23.3.4b）：`{{ base_digest }}` 段的硬上限。2048 B 的来源不是"看着差不多"：
#: 预算的判定口径是 `prompt.md + system.md + schema.json ≤ 32 KB`
#: （`agent/schema_budget.py`）。schema 18392 B + system 3822 B + prompt.md ≈ 2.5 KB ≈ 22 KB，
#: 再加 2048 B 仍 ≈ 24 KB，**余量 8 KB 以上**。所以这里只断言"≤2048 B"，不新增第二个
#: 预算判定器（AC-146）。
BASE_DIGEST_MAX_BYTES = 2048

#: 摘要最后那句硬指令。它的存在是 R-69 的那一半："这是上一版的判断，不是新证据"。
BASE_DIGEST_INSTRUCTION = "以上是上一版的结构与判断，不是新证据；有新材料支持时改判，否则保持一致。"


def base_digest_text(doc: dict[str, Any], *, version: int, max_bytes: int = BASE_DIGEST_MAX_BYTES) -> str:
    """把上一版 doc 收敛成一段**只有结构与判断、没有数值**的提示词片段。

    五样东西，按 §23.3.4b 写死：标的与 `asOf`（一行）、评级四元组（标注"上一版的判断"）、
    8 个章节的 id + 标题、至多 10 个 `keyMetrics.label`（**只给标签，不给数值**）、
    以及一句硬指令。

    字节上限是**硬约束**（不是心算）：章节行与指标行按顺序加，加不进去就停——于是"上一版
    章节特别多"只会让指标行缺席，而不会让整段超预算。最后还有一道兜底切片，保证返回值
    `len(text.encode()) <= max_bytes` 恒成立。
    """
    instrument = doc.get("instrument") or {}
    rating = doc.get("rating") or {}
    sections = [item for item in (doc.get("sections") or []) if isinstance(item, dict)]
    labels = [
        str(item.get("label"))
        for item in (doc.get("keyMetrics") or [])
        if isinstance(item, dict) and item.get("label")
    ][:10]

    head = [
        "# 上一版报告的结构与判断（不是新证据）",
        "",
        f"- 标的：{instrument.get('symbol', '')} {instrument.get('name', '')} · "
        f"as-of {doc.get('asOf', '')} · 版本 v{version}",
        "- 上一版评级（这是上一版的判断，不是新证据）："
        f"stance={rating.get('stance', '')} · conviction={rating.get('conviction', '')} · "
        f"horizon={rating.get('horizon', '')} · targetPrice={rating.get('targetPrice', '—')}",
    ]
    section_line = (
        "- 上一版章节（保持这套标题与顺序）："
        + " / ".join(f"{item.get('id', '')} {item.get('title', '')}".strip() for item in sections[:8])
        if sections
        else ""
    )
    metric_line = "- 上一版指标标签（只有标签，没有数值，请按新资料重算）：" + " / ".join(labels) if labels else ""
    tail = ["", BASE_DIGEST_INSTRUCTION]

    pieces = list(head)
    for extra in (section_line, metric_line):
        if not extra:
            continue
        candidate = "\n".join([*pieces, extra, *tail])
        if len(candidate.encode("utf-8")) <= max_bytes:
            pieces.append(extra)
    text = "\n".join([*pieces, *tail])
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        ellipsis = "…".encode()
        text = encoded[: max_bytes - len(ellipsis)].decode("utf-8", "ignore") + "…"
    return text



def hard_deadline_seconds() -> float:
    """★v12（S2 / §22.3.3 第 1 层）：一次生成在 Python 侧的墙钟上限。

    `--max-duration` 是**交给 CLI** 的预算，它管不住"CLI 卡死 / shim 留下不关管道的
    孙子 / `taskkill` 失败"这三种情形（E-91/E-92）。所以在 `runner.run()` 外面再套一层
    `asyncio.wait_for`，截止 = CLI 的预算 + `agent_hard_deadline_grace_sec`。

    ★v12 修订（V11-P1-10 / C-45）：**不引入 `AGENT_HARD_DEADLINE_SEC` 配置项**。
    一个可设的绝对秒数会与 `--max-duration` 形成第二个截止真源，而两个真源里更松的那个
    永远赢——于是"调大 max-duration"变成唯一能解释的行为（与 V10-P1-6 的 `keySource`
    是同一课）。这里只加"给 CLI 收尾的余量"。
    """
    settings = get_settings()
    grace = max(0, int(settings.agent_hard_deadline_grace_sec))
    return max(1.0, float(settings.aragon_max_duration_ms) / 1000.0 + grace)

#: ★v7（H-9 / I-42）：没有 `AgentResult` 时临时报告携带的三态成本（"不知道"而不是"0"）。
UNKNOWN_COST_PAYLOAD: dict[str, Any] = {
    "source": "unknown",
    "reportedUsd": None,
    "estimatedUsd": None,
    "usd": None,
    "priceVersion": None,
}


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _schema_identity(path: Path) -> str | None:
    """工作区 schema 的 12 位 sha1（"当次交给 CLI 的那份"）；读不到 → `None`。

    它在 `write_snapshot_files()` 之后立刻算，写进 `quality.engine.schemaSha1`
    （★v8 V8-P0-1）：事后任何一次"CLI 接受、Python 拒绝"都能对上号。
    """
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except OSError:  # pragma: no cover - 工作区被删才会走到
        return None


def read_run_state(session: Session, run_id: str) -> tuple[str, bool] | None:
    """`(status, cancel_requested)` straight from the database.

    Deliberately a column select: an ORM `session.get` would hand back the cached
    instance and miss an UPDATE committed by another session — which is exactly
    how a cancel request silently never arrives.
    """
    row = session.execute(select(Run.status, Run.cancel_requested).where(Run.id == run_id)).one_or_none()
    if row is None:
        return None
    return str(row[0]), bool(row[1])


class RunManager:
    """Owns the live runs. One instance per process (see `main.py` lifespan)."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
        self._cancels: dict[str, Callable[[], Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._sessions: dict[str, Session] = {}
        self._global_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

    # ── bookkeeping ──────────────────────────────────────────────────────
    def _lock_for(self, instrument_id: str) -> asyncio.Lock:
        lock = self._locks.get(instrument_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[instrument_id] = lock
        return lock

    def _publish(self, run_id: str, event: dict[str, Any] | None) -> None:
        for queue in list(self._subscribers.get(run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - subscriber is too slow
                logger.warning("subscriber queue full run=%s, dropping event", run_id)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=2048)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks and not self._tasks[run_id].done()

    def has_active_runs(self) -> bool:
        """★v9（K2 / V10-P2-11）：有没有真实生成在跑。

        引擎自检与真实生成必须互斥：单 worker 下两者会各自 spawn、各烧各的 token；
        更麻烦的是探针的 `system` 事件会经 `note_base_url()` 回填进程级能力快照，
        把"这个端点生成了那份报告"的实证链搅浑。
        """
        return any(not task.done() for task in self._tasks.values())

    # ── event log (DB is the durable half, queues are the live half) ─────
    def _record(
        self,
        session: Session,
        run: Run,
        event_type: str,
        payload: dict[str, Any],
        *,
        touch_run: bool = True,
    ) -> dict[str, Any]:
        record = RunEvent(
            run_id=run.id,
            type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=_iso(),
        )
        session.add(record)
        if touch_run:
            if "pct" in payload and isinstance(payload["pct"], int) and payload["pct"] >= run.pct:
                run.pct = min(100, payload["pct"])
            if "stage" in payload and payload["stage"] in STAGE_ORDER:
                run.stage = payload["stage"]
        session.flush()
        session.commit()
        envelope = {
            "seq": record.seq,
            "runId": run.id,
            "type": event_type,
            "payload": payload,
            "createdAt": record.created_at,
        }
        self._publish(run.id, envelope)
        return envelope

    def replay(self, session: Session, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        rows = session.execute(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq.asc())
        ).scalars()
        return [
            {
                "seq": row.seq,
                "runId": row.run_id,
                "type": row.type,
                "payload": row.payload,
                "createdAt": row.created_at,
            }
            for row in rows
        ]

    # ── start ────────────────────────────────────────────────────────────
    def start(
        self,
        session: Session,
        instrument: Instrument,
        *,
        reason: str = "manual",
        force: bool = False,
        override_budget: bool = False,
        base_report_id: str | None = None,
    ) -> Run:
        """Create the run row and schedule the orchestration task.

        Raises `RunConflict`/`TooManyRuns`/`MonthlyBudgetExceeded` before any row
        is written.

        ★v6（V6-P1-7）**插位写死**：`active_run_for()` → `count_active_runs()` →
        `can_start()` → `workspace.mkdir()`。三个理由，少一个都不行：

        * 放在两个既有冲突检查**之后**，才不会把"同标已有运行中"从 409 翻成 402
          （既有契约被静默改写）；
        * 放在 `mkdir()` **之前**，否则每次 402 都在磁盘上留一个孤儿工作区目录，
          而 `RUN_RETENTION_DAYS` 只清理**有 run 行**的目录，等于永久垃圾；
        * 它是**同步**方法、没有 asyncio.Lock，"临界区"由事务 + `ux_runs_active`
          承担，所以闸门只读账本、不改任何锁结构（O-9）。

        ★v12（S1 / V11-P0-2 / C-38）：**第 0 步**先问"这个进程能不能 spawn 子进程"。
        位置不能更晚：`start()` 在写 `runs` 行**之前**就 `workspace.mkdir()`（下面几行），
        `_pipeline()` 第一句又 mkdir 一次 ⇒ 闸门放进 `research`（`:369`）时工作区**必然
        已经存在**，而 AC-111 要求"目录未创建、`runs` 表没有该行"。一次注定不可能的生成
        不该在磁盘和账本上留下任何痕迹，用户也不该等 10 秒才看到一个 `internal`。

        ★评审修正（评审节点 I-127）：**`AGENT_MODE=mock` 时这道闸门必须让路**。
        mock 引擎一次子进程都不 spawn（`build_runner()` 的注释就写着"mock must never
        spawn anything"），所以"事件循环不支持子进程"对它是**不成立的前提**。上一版无条件
        拒绝，于是 `start-all.ps1 -Mock -Reload`（或 `.env` 里写 `AGENT_MODE=mock` 再
        `DEV_RELOAD=1 npm run dev`）把整条**无需任何 CLI 的演示链路**也一并关掉了——
        用户看到的是一句"这台机器起不来"，而它其实起得来。
        注意 `auto` **不**豁免：那里 `loopOk` 已经是 `fullyCapable` 的一道门（§22.3.2(c)），
        静默退回 mock 正是 v9 说的"最坏的失败形态：不是红，是绿"。
        """
        if get_settings().agent_mode != "mock":
            loop_ok, loop_reason = preflight.spawn_capability()
            if not loop_ok:
                # 同步错误，不是 run 行 + 后台失败：用户点下去立刻看到"这台机器起不来"。
                from ..errors import EventLoopUnsupported

                raise EventLoopUnsupported(
                    f"当前进程的事件循环不支持启动子进程（{loop_reason}）："
                    "去掉 --reload 后重启 API，或直接用 npm run dev 的默认参数。"
                )

        active = report_service.active_run_for(session, instrument.id)
        if active is not None:
            from ..errors import RunConflict

            raise RunConflict(detail={"activeRunId": active.id})
        if report_service.count_active_runs(session) >= MAX_CONCURRENT_RUNS:
            from ..errors import TooManyRuns

            raise TooManyRuns(detail={"retryAfterMs": 15000, "limit": MAX_CONCURRENT_RUNS})

        estimate_usd = cost_service.estimate(session, instrument.symbol, "run").get("estimatedUsd")
        decision = cost_service.can_start(session, "run", estimate_usd=estimate_usd)
        if not decision.allowed:
            if not override_budget:
                from ..errors import MonthlyBudgetExceeded

                raise MonthlyBudgetExceeded(detail=decision.detail)
            # 留痕：`String(16)`，15 个字符放得下（§19.5.3）。
            reason = "budget_override"

        # ★v14（§23.5.3 / ★P1-15）：`baseReportId` 的校验排在**三个既有闸门之后**——
        # 一个开不了的请求不该先报 422（K-6 的优先级链 409 → 429 → 402 一个字不改）。
        # 只校验"属于该标的、且未被硬删"：已软删的版本**允许**（它仍是素材）；
        # **取不到结构**（provisional / corrupt）不在这里报错，由 `_base_digest()` 按
        # "无 base"处理（不 422、不崩）。
        if base_report_id:
            base = report_service.get(session, base_report_id, include_deleted=True)
            if base is None or base.instrument_id != instrument.id:
                from ..errors import BaseReportMismatch

                raise BaseReportMismatch(
                    detail={"baseReportId": base_report_id, "instrumentId": instrument.id}
                )

        settings = get_settings()
        run_id = uuid.uuid4().hex
        workspace = settings.runs_dir / run_id
        workspace.mkdir(parents=True, exist_ok=True)

        run = Run(
            id=run_id,
            instrument_id=instrument.id,
            report_id=None,  # ★P1-1: written after the report exists
            status="queued",
            stage=None,
            pct=0,
            started_at=_iso(),
            workspace_path=str(workspace),
            log_path=str(workspace / "aragon.ndjson"),
            reason=reason,
            cancel_requested=0,
            cost_known=0,
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            from ..errors import RunConflict

            active = report_service.active_run_for(session, instrument.id)
            raise RunConflict(detail={"activeRunId": active.id if active else None}) from exc

        self._record(session, run, "run.stage", {"stage": "resolve", "pct": 0, "label": stage_label("resolve")})
        logger.info("run queued id=%s instrument=%s reason=%s", run.id, instrument.id, reason)
        return run

    def schedule(
        self,
        run_id: str,
        *,
        force: bool = False,
        base_report_id: str | None = None,
        dedupe: bool = True,
    ) -> None:
        """Fire-and-forget the orchestration in a background task."""
        task = asyncio.create_task(
            self._run(run_id, force=force, base_report_id=base_report_id, dedupe=dedupe),
            name=f"run-{run_id}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda finished: self._forget(run_id, finished))

    def _forget(self, run_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(run_id, None)
        try:
            task.result()
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            pass
        except Exception:  # pragma: no cover - already recorded
            logger.exception("run task crashed run=%s", run_id)

    # ── the six stages ───────────────────────────────────────────────────
    async def _run(
        self,
        run_id: str,
        *,
        force: bool = False,
        base_report_id: str | None = None,
        dedupe: bool = True,
    ) -> None:
        session = report_service_session()
        self._sessions[run_id] = session
        run = session.get(Run, run_id)
        if run is None:  # pragma: no cover - defensive
            session.close()
            return
        instrument = session.get(Instrument, run.instrument_id)
        if instrument is None:  # pragma: no cover - defensive
            self._finalize_failure(session, run, "instrument_not_found", "标的已被删除", None)
            session.close()
            return

        lock = self._lock_for(instrument.id)
        async with self._global_slots, lock:
            try:
                await self._pipeline(
                    session,
                    run,
                    instrument,
                    force=force,
                    base_report_id=base_report_id,
                    dedupe=dedupe,
                )
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                self._finalize_failure(session, run, "interrupted", "服务重启，本次生成中断", None)
                raise
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("run pipeline crashed run=%s", run_id)
                self._finalize_failure(session, run, "internal", f"{type(exc).__name__}: {exc}"[:300], None)
            finally:
                self._cancels.pop(run_id, None)
                self._sessions.pop(run_id, None)
                self._publish(run_id, None)
                session.close()
                # ★v12（S7 / §22.3.8 第 3 条）：run 终止后删掉**本 run 自己的**会话文件。
                # 只删 `sanitize_session_id(run_id)` 精确匹配的那个 id；**没有**任何全局
                # `prune`（C-40：实测 `prune --older-than 7 --dry-run` 会列出
                # `smoke-threads-tenant-a/b`、`webuse-…` 这些别的项目的会话 9 条，而目录
                # 今天已经 1545.71 MB 早过阈值 ⇒ 第一次 tick 就会删别人的东西）。
                await self._cleanup_session(run_id)

    async def _pipeline(
        self,
        session: Session,
        run: Run,
        instrument: Instrument,
        *,
        force: bool,
        base_report_id: str | None = None,
        dedupe: bool = True,
    ) -> None:
        settings = get_settings()
        workspace = Path(run.workspace_path or settings.runs_dir / run.id)
        workspace.mkdir(parents=True, exist_ok=True)

        run.status = "running"
        session.commit()
        started = datetime.now(UTC)

        # ── 1. resolve ───────────────────────────────────────────────────
        self._advance(session, run, "resolve")
        await asyncio.sleep(0)
        self._record(
            session,
            run,
            "run.log",
            {"level": "info", "message": f"标的已归一化：{instrument.symbol} {instrument.name}"},
            touch_run=False,
        )

        # ── 2. snapshot ──────────────────────────────────────────────────
        self._advance(session, run, "snapshot")
        try:
            bundle = await snapshot_service.build_snapshot(session, instrument, force=force)
        except Exception as exc:
            logger.warning("snapshot failed run=%s: %s", run.id, exc)
            self._finalize_failure(session, run, "data_provider_unavailable", f"数据源不可用：{exc}"[:300], None)
            return
        for warning in bundle.warnings[:6]:
            self._record(session, run, "run.log", {"level": "warn", "message": warning}, touch_run=False)

        # ── 3. prepare ───────────────────────────────────────────────────
        self._advance(session, run, "prepare")
        system_path = workspace / "system.md"
        # ★v14（U3 / §23.3.4b）：`{{ base_digest }}` 进的是**任务提示词**（渲染后落在工作区的
        # `prompt.md`），而 `quality.prompt` 记的是**系统提示词** —— 两件事不要混（P0-3）。
        # 摘要必须在 `write_snapshot_files()` **之前**算出来，那是唯一渲染 prompt.md 的地方。
        base_digest, base_version = self._base_digest(session, run, instrument, base_report_id)
        try:
            prompt_path = snapshot_service.write_snapshot_files(
                bundle, workspace, system_path, base_digest=base_digest
            )
        except AgentError as exc:
            # ★v7（§20.3.3 / AC-72）：`write_schema_copy()` 在写完副本后立刻做身份断言，
            # 所以 `schema_drift` 在 spawn 之前、prepare 阶段就失败。
            logger.warning("workspace assembly refused run=%s code=%s", run.id, exc.code)
            self._finalize_failure(session, run, exc.code, exc.message, bundle.snapshot)
            return
        except Exception as exc:
            logger.exception("workspace assembly failed run=%s", run.id)
            self._finalize_failure(session, run, "internal", f"资料组装失败：{exc}"[:300], bundle.snapshot)
            return
        schema_sha1 = _schema_identity(workspace / "schema.json")

        bars = bundle.bars
        self_check = []
        if len(bars) < 30:
            self_check.append(f"bars 仅 {len(bars)} 根，技术面结论不可靠")
        if not bundle.sources:
            self_check.append("sources 为空")
        if (bundle.snapshot.get("dataQuality") or {}).get("stale"):
            self_check.append("数据存在陈旧警告")
        for note in self_check:
            self._record(session, run, "run.log", {"level": "warn", "message": f"资料自检：{note}"}, touch_run=False)

        # ── 4. research ──────────────────────────────────────────────────
        self._advance(session, run, "research")
        request = AgentRequest(
            run_id=run.id,
            workspace=workspace,
            prompt_path=prompt_path,
            system_prompt_path=system_path,
            schema_path=workspace / "schema.json",
            # ★v8（V8-P0-1）：真源随 request 走，`build_argv()` 拿它做字节身份断言。
            schema_source_path=get_settings().schemas_dir / "report_doc.schema.json",
            instrument_symbol=instrument.symbol,
            instrument_name=instrument.name,
            max_turns=settings.aragon_max_turns,
            max_duration_ms=settings.aragon_max_duration_ms,
            # ★评审修正（评审节点 I-131）：**这个调用点认领会话清理**，所以它才有资格
            # 让 CLI 保存会话（`--session-id` + 重试时 `--resume`）。
            # `ask_runner` / `probe` 不在这条 finally 里，它们保持 v11 的
            # `--no-save-session`——否则每问一次就在会话目录里留一个永不回收的文件
            # （那正是 S7 要治的病，见 R-61）。
            keep_session=True,
        )

        try:
            # `build_runner` may spawn `aragon info --json` (up to 30 s) on a cache
            # miss; doing that inline would freeze the single worker loop — and with
            # it every live SSE stream — so it runs in a worker thread (§5.4).
            runner = await asyncio.to_thread(build_runner, None, strict=False)
        except AgentError as exc:
            self._finalize_failure(session, run, exc.code, exc.message, bundle.snapshot)
            return

        result = await self._drive_agent(session, run, runner, request)
        if result is None:
            return  # cancelled; already finalized

        # ★v8 评审（§20.3.7）：`baseUrl` 只出现在 `exec` 的 `system` 事件里，
        # 写回进程级能力快照，`/capabilities.aragon.baseUrl` 才不会永远是 null。
        preflight.note_base_url((result.engine_info or {}).get("baseUrl"))

        if result.exit_code is not None:
            run.exit_code = result.exit_code
        # ★v8（V8-P2-9）：这两行**替换**了旧的两行 `run.provider = result.provider` /
        # `run.model = result.model` —— `runs` 表没有 provider/model 列，那两行是被
        # SQLAlchemy 当普通属性静默丢弃的赋值（E-48），于是"哪个模型生成的这一版"
        # 永远是 NULL。诊断列才是这条信息的真正落点。
        run_diagnostics.apply_agent_result(run, result)
        # ★v7（H4/O-13）：三态成本只由 `resolve_cost()` 判定一次，这里只落列。
        resolution = resolve_cost(result)
        run_diagnostics.apply_cost(run, resolution)
        run.tokens_in = result.usage.input_tokens
        run.tokens_out = result.usage.output_tokens
        session.commit()

        if run.cancel_requested or result.error_code == "cancelled":
            self._finalize_failure(session, run, "cancelled", "用户取消了本次生成", bundle.snapshot, result=result)
            return

        # ── ★v9 K5（§21.3.6）：L1 抢救 ─────────────────────────────────────
        # L0（CLI 的 --result-repair-turns）已经跑过；走到这里说明仍然没有 structured。
        # 真实失败里最常见的一类不是"模型没提交"，而是"提交了、但多一个键 / 少一个必填
        # 字段 / 有一个我们不认识的块类型"——那时用户花掉 3 万 token，却只拿到一句
        # "这不是一份可用的投研报告"。
        doc = result.doc
        salvage_report: report_validator.SalvageReport | None = None
        if doc is None and isinstance(result.salvageable_raw, dict):
            salvaged, salvage_report = report_validator.salvage_doc(result.salvageable_raw, bundle.snapshot)
            if salvaged is not None:
                doc = salvaged
                self._record(
                    session,
                    run,
                    "run.log",
                    {
                        "level": "warn",
                        "code": "rescue",
                        "message": (
                            f"模型产出有 {salvage_report.total} 处格式不合法，已抢救后展示（"
                            f"{salvage_report.droppedKeys} 个多余字段 / "
                            f"{salvage_report.droppedBlocks} 个块 / "
                            f"{salvage_report.droppedSections} 个章节）"
                        ),
                    },
                    touch_run=False,
                )
            else:
                salvage_report.reasons = salvage_report.reasons or ["unsalvageable"]

        # ── ★v12 S4（§22.3.5）：L1.5 —— 从模型正文里把已经付过钱的 JSON 捡回来 ────
        # 顺序写死：先 L1（`result.result` 里的字符串，更便宜也更可信），后 L1.5。
        # ★v12 修订（V11-P2-11）：这是**防御性**能力，本机 24 份 `result.json` 的 `text`
        # 最长 413 字、花括号出现 0 次（E-115），所以它不是"33% 真实生成可救"的机制；
        # 但一旦模型真的把完整 JSON 写在正文里，扔掉它等于把已经付出的 token 再付一次
        # （S-6）。
        if doc is None and result.text:
            rescued, how, rescued_report = report_validator.rescue_from_text(result.text, bundle.snapshot)
            if rescued is not None:
                doc = rescued
                salvage_report = rescued_report
                self._record(
                    session,
                    run,
                    "run.log",
                    {
                        "level": "warn",
                        "code": "rescue",
                        "message": f"结构化结果缺失，已从模型正文中还原（{how}）",
                    },
                    touch_run=False,
                )

        if doc is None:
            code = result.error_code or "result_schema_unsatisfied"
            message = result.error_message or "生成引擎没有返回结构化报告"
            self._finalize_failure(
                session,
                run,
                code,
                message,
                bundle.snapshot,
                result=result,
                salvage=salvage_report,
            )
            return

        # ── 5. validate ──────────────────────────────────────────────────
        self._advance(session, run, "validate")
        outcome = report_validator.validate_doc(
            doc,
            bundle.snapshot,
            mock=result.source == "mock",
            source=result.source,
            engine=self._engine_quality(schema_sha1),
            cost=resolution.as_payload(),
            prompt=report_validator.prompt_quality(
                system_path,
                base_report_id=base_report_id,
                base_version=base_version,
            ),
            # ★v8（V8-P0-1 / AC-88）：用**当次交给 CLI 的那份**校验。mock 路径下
            # 工作区里同样有那份副本（`write_snapshot_files` 写的），所以两条路一致。
            schema_path=request.schema_path,
            # ★v9（K5）：抢救过的文档必须降 status 到 degraded，并带上计数器。
            salvage=salvage_report,
            # ★实施发现（I-91）：未知块是在 `salvage_doc()` 内部裁掉的，把它转交回来，
            # `quality.prunedBlocks` 才不会因为"抢救过一次"而变成 0（AC-102 / AC-109）。
            pruned_blocks=salvage_report.prunedBlocks if salvage_report is not None else None,
        )
        for repair in outcome.repairs[:6]:
            # ★评审修正（评审节点 I-130）：前缀读 `report_validator.CLAMP_PREFIX`，
            # 不写字面量 `"收敛"`。上一版在 `repairs` 的生产者（report_validator）与消费者
            # （这里）各写了一遍同样的两个字，改一处就静默失配：`quality.clamp` 还在记账，
            # 而 `run.log` 的 `code: "clamp"` 再也不出现，前端抽屉悄悄少一行。
            is_clamp = repair.startswith(report_validator.CLAMP_PREFIX)
            self._record(
                session,
                run,
                "run.log",
                {
                    "level": "warn" if is_clamp else "info",
                    "message": f"结构修补：{repair}",
                    # ★v12（§22.5.2）：`clamp` 是结构化标记，前端按 `code` 决定是否展开抽屉。
                    **({"code": "clamp"} if is_clamp else {}),
                },
                touch_run=False,
            )
        if outcome.errors:
            self._finalize_failure(
                session,
                run,
                "result_schema_unsatisfied",
                "；".join(outcome.errors)[:400],
                bundle.snapshot,
                result=result,
            )
            return
        quality = outcome.quality

        # ── 6. persist ───────────────────────────────────────────────────
        self._advance(session, run, "persist")
        duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        # ★v14（U3）：`create_or_reuse_version()` —— 内容与最新非临时版完全相同时**不新增
        # 版本**，把这次 run 指向既有那一版，并**照记**这次的花费（硬约束 4 / E-127）。
        # 临时版那条路（`_finalize_failure`）仍用 `create_version()`：两份失败说明文本相同
        # **不代表**两次失败相同。
        persisted = report_service.create_or_reuse_version(
            session,
            instrument,
            outcome.doc,
            status=outcome.status,
            quality=quality,
            run_id=run.id,
            provider=result.provider,
            model=result.model,
            cost_usd=result.cost_usd,
            cost_known=result.cost_known,
            cost_source=resolution.source,
            cost_usd_est=resolution.estimated_usd,
            tokens_in=result.usage.input_tokens,
            tokens_out=result.usage.output_tokens,
            duration_ms=duration_ms,
            dedupe=dedupe,
        )
        report = persisted.report
        if persisted.deduped:
            self._record(
                session,
                run,
                "run.log",
                {
                    "level": "info",
                    "code": "deduped",
                    "message": f"内容与 v{report.version} 完全一致，未新增版本（本次花费仍已记账）",
                },
                touch_run=False,
            )
        (workspace / "report.json").write_text(json.dumps(outcome.doc, ensure_ascii=False, indent=2), encoding="utf-8")

        run.status = "succeeded"
        run.stage = "persist"
        run.pct = 100
        run.report_id = report.id
        run.finished_at = _iso()
        session.commit()

        self._record(
            session,
            run,
            "run.done",
            {
                "reportId": report.id,
                "version": report.version,
                "status": report.status,
                "pct": 100,
                "costUsd": resolution.usd,
                "costKnown": resolution.known,
                # ★v7（H4）：三态一起给，前端才不会把估算当回执。
                "costSource": resolution.source,
                "costUsdEst": resolution.estimated_usd,
                "retryCount": run.retry_count,
                "structuredAttempts": run.structured_attempts,
                "usage": result.usage.as_payload(),
                "durationMs": duration_ms,
                "groundedRatio": round(outcome.grounded_ratio, 4),
                "source": result.source,
                # ★v14（U3 / §23.3.4a）：`deduped=true` ⇒ 没有新增版本，`reportId`/`version`
                # 指向的是**既有**那一版。前端据此提示"内容与 v13 一致，未新增版本"，并且
                # **仍然**打开那一版（不要"什么都不发生"）。
                "deduped": bool(persisted.deduped),
            },
        )
        self._check_cost(session, run, result, resolution)
        logger.info("run succeeded id=%s report=%s v%s", run.id, report.id, report.version)

    def _engine_quality(self, schema_sha1: str | None = None) -> dict[str, Any] | None:
        """★v6 G4：`quality.engine` 的四元组 + ★v7 的 `schemaSha1`。

        `None` 不是失败——它让老报告与新报告在同一套读路径下都能正确渲染
        （`comparability.same = null`），而不是伪造一份"看起来一样"的指纹（O-13）。
        `schemaSha1` 记的是**当次交给 CLI 的那份**字节（V8-P0-1），事后任何一次
        "CLI 接受、Python 拒绝"都能对上号。`model`/`provider` **仍不进来**（C-18）。
        """
        from ..agent.fingerprint import current_fingerprint

        fingerprint = current_fingerprint(schema_bytes=report_validator.schema_bytes())
        if fingerprint is None:
            return None
        payload = fingerprint.as_quality()
        if schema_sha1:
            payload["schemaSha1"] = schema_sha1
        return payload

    def _base_digest(
        self,
        session: Session,
        run: Run,
        instrument: Instrument,
        base_report_id: str | None,
    ) -> tuple[str, int | None]:
        """★v14（U3 / §23.3.4b）：把"上一版的结构与判断"算成一段 ≤2048 B 的提示词片段。

        返回 `(文本, baseVersion)`。文本为空 ⇒ 模板里的 `{{ base_digest }}` 渲染成空行
        （**不留占位符痕迹**）；`baseVersion` 仍然返回原值，因为 `quality.prompt.baseVersion`
        记的是"用户确实选了哪一版"，不是"我们成功注入了哪一版"。

        ★P1-15：**取不到结构不是 422**——provisional（`sections` 为空、内容是失败说明）与
        `integrity=corrupt` 的版本都走"按无 base 处理"这条路：不报错、不崩，`run.log` 记一条
        `info`，摘要段渲染成空行。否则用户选了一份失败说明就会拿到一张 500。

        摘要里**只说结构与判断，不给数值**（R-69）：keyMetrics 只给标签，数值是"上一版的
        事实"，把它写进提示词等于给模型一份可以照抄的旧数，而那正是接地率崩掉的方式。
        """
        if not base_report_id:
            return "", None
        base = report_service.get(session, base_report_id, include_deleted=True)
        if base is None or base.instrument_id != instrument.id:
            # 理论上 `start()` 已经拦过（422 base_report_mismatch），这里是防御。
            return "", None
        doc, integrity = report_service.load_doc_safe(base, instrument)
        reason = None
        if integrity != report_service.INTEGRITY_OK:
            reason = "corrupt"
        elif base.status == "provisional" or doc.get("provisional") is True or not doc.get("sections"):
            reason = "provisional"
        if reason is not None:
            self._record(
                session,
                run,
                "run.log",
                {
                    "level": "info",
                    "message": (
                        f"base_digest 跳过（原因={reason}）：v{base.version} 取不出结构，"
                        "本次按独立生成处理"
                    ),
                },
                touch_run=False,
            )
            return "", base.version
        return base_digest_text(doc, version=base.version), base.version

    def _advance(self, session: Session, run: Run, stage: str) -> None:
        """Emit `run.stage` with a monotonic pct (§7.4)."""
        start_pct = STAGE_START.get(stage, 0)
        end_pct = stage_end_pct(stage)
        pct = max(run.pct, start_pct)
        run.stage = stage
        run.pct = pct
        session.commit()
        self._record(
            session,
            run,
            "run.stage",
            {"stage": stage, "pct": pct, "label": stage_label(stage), "endPct": end_pct},
            touch_run=False,
        )

    async def _drive_agent(
        self,
        session: Session,
        run: Run,
        runner: Any,
        request: AgentRequest,
    ) -> AgentResult | None:
        """Run the agent while a watcher turns a cancel request into a real kill.

        ★v12（S2 / §22.3.3 第 1 层）：`runner.run()` 外面加一层墙钟硬截止。
        今天 Python 侧**没有任何**截止时间——`while True`（`aragon_runner.py:703`）
        只在 `waiter.done()` 或队列空时退出，真正的时间上限完全依赖交给 CLI 的
        `--max-duration`。CLI 卡死、shim 留下不关管道的孙子、或 `taskkill` 失败
        （E-91）时，run 会永远停在 `running`，SSE 只发 ping 永不 return
        （`stream_events` 在非终态不退出）——前端永远转圈，还占掉一个并发名额。
        """

        def on_event(event: AgentEvent) -> None:
            self._forward_agent_event(session, run, event)

        async def watch_cancel() -> None:
            while True:
                await asyncio.sleep(0.1)
                state = read_run_state(session, run.id)
                if state is not None and state[1]:
                    self._record(
                        session,
                        run,
                        "run.log",
                        {"level": "warn", "message": "已收到取消请求，正在终止生成进程"},
                    )
                    await runner.cancel()
                    session.expire_all()
                    return

        cancel_task = asyncio.create_task(watch_cancel(), name=f"cancel-watch-{run.id}")
        self._cancels[run.id] = runner.cancel
        deadline = hard_deadline_seconds()
        try:
            result = await asyncio.wait_for(runner.run(request, on_event), timeout=deadline)
        except TimeoutError:
            # 先收进程树，再报错：留下一个还在烧 token 的子进程比失败本身更糟。
            # `cancel_task` 的收尾只写在 `finally` 里（★评审修正：这里原本又 cancel 一次，
            # 同一个任务在两个地方收尾，读起来像"两个生命周期"，实际只有一条）。
            await runner.cancel()
            self._finalize_failure(
                session,
                run,
                "agent_timeout",
                f"引擎超过 {int(deadline)} 秒仍未结束（--max-duration={get_settings().aragon_max_duration_ms}ms）",
                None,
                deadline_sec=int(deadline),
            )
            return None
        except AgentError as exc:
            self._finalize_failure(session, run, exc.code, exc.message, None)
            return None
        finally:
            cancel_task.cancel()

        session.refresh(run)
        return result if isinstance(result, AgentResult) else None

    def _forward_agent_event(self, session: Session, run: Run, event: AgentEvent) -> None:
        if event.type == AgentEventType.log:
            if "__result" in event.payload:
                return
            self._record(
                session,
                run,
                "run.log",
                {"level": event.payload.get("level", "info"), "message": str(event.payload.get("message", ""))[:600]},
                touch_run=False,
            )
            return
        if event.type == AgentEventType.tool:
            self._record(session, run, "agent.tool", event.payload, touch_run=False)
            return
        if event.type == AgentEventType.text:
            self._record(session, run, "agent.text", event.payload, touch_run=False)
            return
        if event.type == AgentEventType.progress:
            payload = dict(event.payload)
            payload.setdefault("elapsedMs", 0)
            # Research stage spans 20→70%; map internal progress onto it.
            span = max(1, STAGE_BOUNDS["research"][0] - STAGE_START["research"])
            raw = payload.get("pct")
            if isinstance(raw, int) and raw <= 100:
                payload["pct"] = min(STAGE_BOUNDS["research"][0], STAGE_START["research"] + int(span * raw / 100))
            else:
                payload["pct"] = min(STAGE_BOUNDS["research"][0], max(run.pct, STAGE_START["research"] + 5))
            self._record(session, run, "run.progress", payload)
            return
        if event.type == AgentEventType.retry:
            # ★v7（§20.3.4 / P-4 / O-15）：`agent.retry` → `run.retry`。
            # `agent.*` 是 CLI 的词汇（`runner_base.AgentEventType`），`run.*` 是 run 的
            # 词汇；SSE 类型名与外层事件名一致，都是 `run.retry`，载荷逐字段透传
            # （`retries` 是 runner 的**本地累计**，不是 `attempt`——后者会重置）。
            payload = dict(event.payload)
            self._record(session, run, "run.retry", payload, touch_run=False)
            run.retry_count = int(run.retry_count or 0) + 1
            session.commit()
            return

    # ── failure, cancellation, recovery ─────────────────────────────────
    def _finalize_failure(
        self,
        session: Session,
        run: Run,
        code: str,
        message: str,
        snapshot: dict[str, Any] | None,
        *,
        result: AgentResult | None = None,
        salvage: report_validator.SalvageReport | None = None,
        deadline_sec: int | None = None,
    ) -> None:
        """★P1-10: persist a readable provisional report *before* recording the error."""
        session.rollback()
        run = session.get(Run, run.id) or run

        if code != "cancelled" and snapshot is not None:
            try:
                provisional = report_validator.build_provisional_doc(
                    snapshot, raw_text=(result.text if result else "") or "", error_code=code, error_message=message
                )
                # ★v7（H-9 / I-42 的同一条纪律）：临时报告也要记成本与来源。
                # token 数为 0 时 `resolve_cost` 给 `unknown`（"0 成本"与"未知成本"是
                # 两件事），所以这里永远写一个三态，而不是省略这个键。
                provisional_cost = (resolve_cost(result) if result is not None else None)
                quality_payload: dict[str, Any] = {
                    "groundedRatio": 0.0,
                    "missingSections": [],
                    "prunedBlocks": 0,
                    "source": "aragon-text",
                    "cost": (
                        provisional_cost.as_payload() if provisional_cost is not None else UNKNOWN_COST_PAYLOAD
                    ),
                }
                # ★v9（K5）：L1 抢救也失败时，`quality.salvage.used = false` 且
                # `reasons = ["unsalvageable"]` —— "试过了、救不回来"必须可读，
                # 否则用户会以为我们根本没试。
                if salvage is not None:
                    if not salvage.used:
                        salvage.reasons = salvage.reasons or ["unsalvageable"]
                    quality_payload["salvage"] = salvage.as_payload()
                report = report_service.create_version(
                    session,
                    session.get(Instrument, run.instrument_id),  # type: ignore[arg-type]
                    provisional,
                    status="provisional",
                    quality=quality_payload,
                    run_id=run.id,
                    provider=result.provider if result else None,
                    model=result.model if result else None,
                    cost_usd=result.cost_usd if result else None,
                    cost_known=result.cost_known if result else False,
                    cost_source=provisional_cost.source if provisional_cost is not None else "unknown",
                    cost_usd_est=provisional_cost.estimated_usd if provisional_cost is not None else None,
                    duration_ms=None,
                    is_provisional=True,
                )
                run = session.get(Run, run.id) or run
                self._record(
                    session,
                    run,
                    "run.log",
                    {"level": "warn", "message": f"已保存一版临时报告（v{report.version}），可作为原始产出阅读"},
                    touch_run=False,
                )
            except Exception:  # pragma: no cover - never mask the real failure
                logger.exception("failed to persist provisional report run=%s", run.id)
                session.rollback()

        status = "cancelled" if code == "cancelled" else ("budget_exceeded" if code == "budget_exceeded" else "failed")
        run.status = status
        run.error_code = code
        run.error_message = message[:1000]
        run.finished_at = _iso()

        # ★v7（H3.5 / H-9）：失败也要留下三条信息——同一根因的指纹、下一步做什么、
        # 这次花了多少钱。指纹的**公式**在 `agent/failure.py`，这里只调用。
        fingerprint = failure_fingerprint(code, message, run.exit_code)
        resolution = resolve_cost(result) if result is not None else None
        run_diagnostics.record_failure(session, run, fingerprint=fingerprint, resolution=resolution)
        run = session.get(Run, run.id) or run
        # ★v8 评审（H3.5 的数据通路）：前端要能说"这不是偶发失败：最近 N 次同一原因"，
        # 就必须有人把 `N` 算出来下发。算它在 `run_diagnostics`（聚合查询），
        # 判定阈值在 `REPEAT_ALERT_THRESHOLD`——前端只负责比较与措辞。
        repeat_count = run_diagnostics.fingerprint_repeat_count(session, run.instrument_id, fingerprint)

        self._record(
            session,
            run,
            "run.error",
            {
                "code": code,
                "message": message[:600],
                "exitCode": run.exit_code,
                # ★v7（P-2 / H-12）：retryable 与 hint 都来自 `failure.RUN_ERROR_CODES`
                # 一张表，前端不再自己拼文案（AC-76 因此可机器判定）。
                "retryable": is_retryable(code),
                "hint": hint_for(code),
                "retryCount": run.retry_count,
                "structuredAttempts": run.structured_attempts,
                "failureFingerprint": fingerprint,
                # 同一标的最近 N 条 run 里同指纹的次数（含本次）。前端 `>= 3` 时换措辞。
                "repeatCount": repeat_count,
                "tokensIn": run.tokens_in,
                "tokensOut": run.tokens_out,
                "costSource": run.cost_source,
                "costUsdEst": run.cost_usd_est,
                "costUsd": run.cost_usd,
                # ★v12（§22.5.2）：`agent_timeout` 时告诉用户"我们等了多久"。只在该码上
                # 出现——给每条失败都塞一个 `deadlineSec` 会让它变成噪音。
                **({"deadlineSec": int(deadline_sec)} if deadline_sec is not None else {}),
            },
        )
        logger.warning("run finished status=%s code=%s run=%s", status, code, run.id)

    def _check_cost(
        self,
        session: Session,
        run: Run,
        result: AgentResult,
        resolution: Any | None = None,
    ) -> None:
        """§11.2 / ★v7：两档告警——`reported` 超限 → warn；`estimated` 超限 → info。

        估算不该吓人（它可能偏 10%），但也不该静默：静默会让"这个月为什么花了这么多"
        在下个月变成一句无法回答的问题。
        """
        settings = get_settings()
        resolved = resolution if resolution is not None else resolve_cost(result)
        usd = resolved.usd
        if usd is None:
            return
        if usd <= settings.max_report_cost_usd:
            return
        level = "warn" if resolved.source == "reported" else "info"
        label = "已确认成本" if resolved.source == "reported" else "估算成本"
        self._record(
            session,
            run,
            "run.log",
            {
                "level": level,
                "message": (
                    f"本次生成{label} ${usd:.2f} 超出预算阈值 "
                    f"${settings.max_report_cost_usd:.2f}"
                    f"{'' if resolved.source == 'reported' else '（按 token × 单价估算，非引擎回执）'}"
                ),
            },
            touch_run=False,
        )

    def request_cancel(self, session: Session, run_id: str) -> Run:
        run = session.get(Run, run_id)
        if run is None:
            from ..errors import RunNotFound

            raise RunNotFound()
        if run.status not in ACTIVE:
            from ..errors import RunNotActive

            raise RunNotActive(detail={"status": run.status})
        run.cancel_requested = 1
        if run.status == "queued":
            run.status = "cancelling"
        else:
            run.status = "cancelling"
        session.commit()
        self._record(session, run, "run.log", {"level": "warn", "message": "取消请求已受理（cancelling）"})
        return run

    def cancel_now(self, run_id: str) -> None:
        """Used on shutdown — no HTTP, no DB session, just stop the child."""
        cancellation = self._cancels.get(run_id)
        if cancellation is None:
            return
        try:
            asyncio.ensure_future(cancellation())  # type: ignore[arg-type]
        except RuntimeError:  # pragma: no cover - no running loop
            logger.warning("cannot cancel run %s: no event loop", run_id)

    # ── startup reconciliation ───────────────────────────────────────────
    def recover_interrupted(self, session: Session) -> int:
        """★P1-5 / AC-23: nothing survives a restart, so nothing may look alive."""
        stale = session.execute(select(Run).where(Run.status.in_(ACTIVE))).scalars().all()
        for run in stale:
            run.status = "failed"
            run.error_code = "interrupted"
            run.error_message = "服务重启，本次生成中断"
            run.finished_at = _iso()
        if stale:
            session.commit()
        for run in stale:
            self._record(
                session,
                run,
                "run.error",
                {
                    "code": "interrupted",
                    "message": "服务重启，本次生成中断",
                    "exitCode": run.exit_code,
                    "retryable": True,
                },
            )
            logger.warning("recovered stale run id=%s -> failed(interrupted)", run.id)
        return len(stale)

    def sweep_stale_runs(self) -> int:
        """★v12（S2 第 2 层 / §22.3.3 / AC-115）：把"还在 running 但已经没人管"的 run 收掉。

        **同步函数**（只碰 DB，无 `await`），所以三处调用点都能用它（C-41）：
        ①`main.py::lifespan` 启动时跑一次；②`main.py::_sweep_loop` 每 60 秒跑一次
        （**不看 `SCHEDULER_ENABLED`**）；③ticker 若想顺带跑，写成
        `await asyncio.wait_for(asyncio.to_thread(manager.sweep_stale_runs), 5)`。

        为什么第 1 层的 `wait_for` 不够：它只在 `research` 阶段有效。`persist` 里
        `create_version()` 抛 `IntegrityError` 之后，如果 `_finalize_failure` 自己的
        commit 撞 `SQLITE_BUSY`（`db/session.py` 只给 5000ms），行就会停在 `running`
        ——这条路径上**没有任何协程还活着**，只有对账能收。

        `if run.id in self._tasks: continue` 是误杀防线（R-62）：有活任务的 run 自己
        的 `wait_for` 会收，清扫不许抢。
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=hard_deadline_seconds())
        # ★实施发现（I-116）：`report_service_session()` 必须**在 try 里面**建。
        # 它过去在外面：连接池耗尽 / DB 文件被锁时这里抛出的异常不会被下面的 `except`
        # 接住，而 `_sweep_loop` 是常驻任务——一次抛错就再也不会对账
        # （`main.py` 的 `_sweep_loop` 会 catch，但那等于把"清扫"变成"偶尔清扫"）。
        session: Session | None = None
        try:
            session = report_service_session()
            rows = (
                session.execute(
                    select(Run).where(
                        Run.status.in_(("queued", "running", "cancelling")),
                        Run.started_at < cutoff.isoformat().replace("+00:00", "Z"),
                    )
                )
                .scalars()
                .all()
            )
            swept = 0
            for run in list(rows):
                if run.id in self._tasks:
                    continue
                self._finalize_failure(
                    session,
                    run,
                    "agent_timeout",
                    "清扫：超过硬截止仍在运行（进程活着，但已没有协程在管这次生成）",
                    None,
                    deadline_sec=int(hard_deadline_seconds()),
                )
                swept += 1
            return swept
        except Exception:  # pragma: no cover - 一次对账失败不该杀死清扫
            logger.exception("sweep_stale_runs failed")
            if session is not None:
                session.rollback()
            return 0
        finally:
            if session is not None:
                session.close()

    async def _cleanup_session(self, run_id: str) -> None:
        """★v12（S7 / §22.3.8）：删掉**本 run 自己的** CLI 会话文件。永不抛。

        命令形态是**实测过**的（Aragon CLIB 的 `aragon sessions --help` + 真跑，E-108）：

        ```text
        $ aragon sessions rm <id>
        Refusing to delete without --yes.          ← exit 2（缺 --yes 就是这样，而且它
                                                    过去被 try/except: log debug 吞掉）
        $ aragon sessions prune --older-than 7d --dry-run
        --older-than takes a non-negative number of days.   ← exit 2（只收天数，不是 7d）
        ```

        所以：`rm <id> --yes`。**绝不**用 `prune`——它是全局的，实测会列出
        `smoke-threads-tenant-a/b`、`webuse-17d194d2-…` 这些**别的项目**的会话，删掉它们
        是不可接受的副作用（R-61 / C-40）。

        失败升为 `run.log(level=warn, code=session_cleanup_failed)`，不再是 debug：
        静默失败正是这一节要消灭的东西（S-12）。
        """
        from ..agent.aragon_runner import cleanup_session

        try:
            ok, detail = await cleanup_session(run_id)
        except asyncio.CancelledError:
            # ★评审修正（E-114）：这条 finally 也会在**关停**时执行（`_run` 的
            # `except CancelledError: … raise` 之后）。那里为了删一个会话文件去 spawn
            # 一个子进程（最多等 15 秒）会拖住进程退出，而 run 的结论早已落库。
            # 吞掉这一次取消不影响任何事实：`rm` 幂等，下次启动的清扫会照常进行。
            return
        except Exception as exc:  # pragma: no cover - cleanup 绝不影响 run 结论
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        if ok:
            return
        if detail in ("no_binary", "absent"):
            # 没有 CLI（mock 模式）或压根没有会话文件（单次尝试走 --no-save-session，
            # 或第一次就没 spawn 起来）——两者都**不是失败**，不该落 warn（I-117）。
            return
        session = report_service_session()
        try:
            run = session.get(Run, run_id)
            if run is not None:
                self._record(
                    session,
                    run,
                    "run.log",
                    {
                        "level": "warn",
                        "code": "session_cleanup_failed",
                        "message": f"清理本次生成的 CLI 会话失败（{detail}）",
                    },
                    touch_run=False,
                )
        except Exception:  # pragma: no cover - 日志都不该影响结论
            logger.exception("session cleanup log failed run=%s", run_id)
        finally:
            session.close()

    def cleanup_workspaces(self, session: Session) -> int:
        """Drop run workspaces older than `RUN_RETENTION_DAYS` (§8.2)."""
        settings = get_settings()
        cutoff = datetime.now(UTC) - timedelta(days=settings.run_retention_days)
        runs_dir = settings.runs_dir
        if not runs_dir.exists():
            return 0
        removed = 0
        for child in runs_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=UTC)
            except OSError:  # pragma: no cover
                continue
            if mtime >= cutoff:
                continue
            if self.is_active(child.name):
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        if removed:
            logger.info("cleaned %s expired run workspaces", removed)
        return removed


def report_service_session() -> Session:
    """A fresh session for a background task (never the request's session)."""
    from ..db.session import SessionLocal

    return SessionLocal()


_manager: RunManager | None = None


def get_manager() -> RunManager:
    global _manager
    if _manager is None:
        _manager = RunManager()
    return _manager


def reset_manager() -> None:
    """Test hook."""
    global _manager
    _manager = None


async def stream_events(
    manager: RunManager,
    session: Session,
    run_id: str,
    after: int = 0,
    *,
    poll_timeout: float = 15.0,
) -> AsyncIterator[dict[str, Any] | None]:
    """Yield events for SSE: replay from the DB, then live from the subscriber queue.

    ``None`` means "no event yet" and lets the caller emit a keep-alive ping.
    Replay-from-DB is what makes a late or reconnecting subscriber correct
    (★P1-5) — the in-memory queue only carries what happens next.
    """
    for event in manager.replay(session, run_id, after):
        after = max(after, int(event["seq"]))
        yield event

    queue = manager.subscribe(run_id)
    try:
        while True:
            state = read_run_state(session, run_id)
            finished = state is None or state[0] in TERMINAL_STATUSES
            try:
                event = await asyncio.wait_for(queue.get(), timeout=poll_timeout)
            except TimeoutError:
                if finished:
                    return
                yield None  # caller sends `: ping`
                continue
            if event is None:
                return
            seq = int(event.get("seq") or 0)
            if seq and seq <= after:
                continue
            after = max(after, seq)
            yield event
            if event.get("type") in ("run.done", "run.error"):
                return
    finally:
        manager.unsubscribe(run_id, queue)
