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

from ..agent import build_runner
from ..agent.runner_base import AgentError, AgentEvent, AgentEventType, AgentRequest, AgentResult
from ..config import get_settings
from ..logging import get_logger
from ..models import Instrument, Run, RunEvent
from ..schemas.run import ACTIVE, STAGE_BOUNDS, STAGE_ORDER, STAGE_START, stage_end_pct, stage_label
from . import report_service, report_validator
from . import snapshot as snapshot_service

logger = get_logger("run_manager")

MAX_CONCURRENT_RUNS = 2
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "timeout", "budget_exceeded")


def _iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    def start(self, session: Session, instrument: Instrument, *, reason: str = "manual", force: bool = False) -> Run:
        """Create the run row and schedule the orchestration task.

        Raises `RunConflict`/`TooManyRuns` before any row is written.
        """
        active = report_service.active_run_for(session, instrument.id)
        if active is not None:
            from ..errors import RunConflict

            raise RunConflict(detail={"activeRunId": active.id})
        if report_service.count_active_runs(session) >= MAX_CONCURRENT_RUNS:
            from ..errors import TooManyRuns

            raise TooManyRuns(detail={"retryAfterMs": 15000, "limit": MAX_CONCURRENT_RUNS})

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

    def schedule(self, run_id: str, *, force: bool = False) -> None:
        """Fire-and-forget the orchestration in a background task."""
        task = asyncio.create_task(self._run(run_id, force=force), name=f"run-{run_id}")
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
    async def _run(self, run_id: str, *, force: bool = False) -> None:
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
                await self._pipeline(session, run, instrument, force=force)
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

    async def _pipeline(self, session: Session, run: Run, instrument: Instrument, *, force: bool) -> None:
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
        try:
            prompt_path = snapshot_service.write_snapshot_files(bundle, workspace, system_path)
        except Exception as exc:
            logger.exception("workspace assembly failed run=%s", run.id)
            self._finalize_failure(session, run, "internal", f"资料组装失败：{exc}"[:300], bundle.snapshot)
            return

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
            instrument_symbol=instrument.symbol,
            instrument_name=instrument.name,
            max_turns=settings.aragon_max_turns,
            max_duration_ms=settings.aragon_max_duration_ms,
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

        if result.exit_code is not None:
            run.exit_code = result.exit_code
        run.provider = result.provider
        run.model = result.model
        run.cost_usd = result.cost_usd
        run.cost_known = result.cost_known
        run.tokens_in = result.usage.input_tokens
        run.tokens_out = result.usage.output_tokens
        session.commit()

        if run.cancel_requested or result.error_code == "cancelled":
            self._finalize_failure(session, run, "cancelled", "用户取消了本次生成", bundle.snapshot, result=result)
            return
        if result.doc is None:
            code = result.error_code or "result_schema_unsatisfied"
            message = result.error_message or "生成引擎没有返回结构化报告"
            self._finalize_failure(session, run, code, message, bundle.snapshot, result=result)
            return

        # ── 5. validate ──────────────────────────────────────────────────
        self._advance(session, run, "validate")
        outcome = report_validator.validate_doc(
            result.doc,
            bundle.snapshot,
            mock=result.source == "mock",
            source=result.source,
        )
        for repair in outcome.repairs[:6]:
            self._record(session, run, "run.log", {"level": "info", "message": f"结构修补：{repair}"}, touch_run=False)
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
        report = report_service.create_version(
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
            tokens_in=result.usage.input_tokens,
            tokens_out=result.usage.output_tokens,
            duration_ms=duration_ms,
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
                "costUsd": result.cost_usd,
                "costKnown": result.cost_known,
                "usage": result.usage.as_payload(),
                "durationMs": duration_ms,
                "groundedRatio": round(outcome.grounded_ratio, 4),
                "source": result.source,
            },
        )
        self._check_cost(session, run, result)
        logger.info("run succeeded id=%s report=%s v%s", run.id, report.id, report.version)

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
        """Run the agent while a watcher turns a cancel request into a real kill."""

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
        try:
            result = await runner.run(request, on_event)
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
    ) -> None:
        """★P1-10: persist a readable provisional report *before* recording the error."""
        session.rollback()
        run = session.get(Run, run.id) or run

        if code != "cancelled" and snapshot is not None:
            try:
                provisional = report_validator.build_provisional_doc(
                    snapshot, raw_text=(result.text if result else "") or "", error_code=code, error_message=message
                )
                report = report_service.create_version(
                    session,
                    session.get(Instrument, run.instrument_id),  # type: ignore[arg-type]
                    provisional,
                    status="provisional",
                    quality={"groundedRatio": 0.0, "missingSections": [], "prunedBlocks": 0, "source": "aragon-text"},
                    run_id=run.id,
                    provider=result.provider if result else None,
                    model=result.model if result else None,
                    cost_usd=result.cost_usd if result else None,
                    cost_known=result.cost_known if result else False,
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
        session.commit()
        self._record(
            session,
            run,
            "run.error",
            {
                "code": code,
                "message": message[:600],
                "exitCode": run.exit_code,
                "retryable": code
                in ("internal", "budget_exceeded", "result_schema_unsatisfied", "agent_error", "interrupted"),
            },
        )
        logger.warning("run finished status=%s code=%s run=%s", status, code, run.id)

    def _check_cost(self, session: Session, run: Run, result: AgentResult) -> None:
        """§11.2: warn only when the cost is actually known."""
        settings = get_settings()
        if not result.cost_known or result.cost_usd is None:
            return
        if result.cost_usd > settings.max_report_cost_usd:
            self._record(
                session,
                run,
                "run.log",
                {
                    "level": "warn",
                    "message": f"本次生成成本 ${result.cost_usd:.2f} 超出预算阈值 ${settings.max_report_cost_usd:.2f}",
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
