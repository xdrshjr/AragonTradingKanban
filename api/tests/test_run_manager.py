"""Run orchestration: locks, cancellation, recovery, provisional fallback.

Covers AC-10 (budget), AC-11 (cancel), AC-21 (provisional on failure) and
AC-23 (single worker + interrupted-run recovery).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.runner_base import AgentEvent, AgentEventType, AgentResult
from app.errors import RunConflict, RunNotActive, TooManyRuns
from app.models import Instrument, Report, Run, RunEvent
from app.services import run_manager as run_manager_module
from app.services.run_manager import RunManager

FAKE = Path(__file__).parent / "fixtures" / "fake_aragon.py"


class StubRunner:
    """A runner that yields a scripted result without spawning anything."""

    name = "stub"
    source = "aragon"

    def __init__(self, result: AgentResult | None = None, *, events: list[AgentEvent] | None = None) -> None:
        self.result = result or AgentResult(doc=None, error_code="internal", error_message="stub")
        self.events = events or []
        self.cancelled = False
        self.started = False

    async def stream(self, request, on_event=None):  # noqa: ANN001
        self.started = True
        for event in self.events:
            if on_event:
                on_event(event)
            yield event

    async def run(self, request, on_event=None):  # noqa: ANN001
        self.started = True
        for event in self.events:
            if on_event:
                on_event(event)
        return self.result

    async def cancel(self) -> None:
        self.cancelled = True


@pytest.fixture()
def manager() -> RunManager:
    run_manager_module.reset_manager()
    return run_manager_module.get_manager()


def _make_run(session, instrument, *, status="queued", run_id="run-x") -> Run:
    run = Run(
        id=run_id,
        instrument_id=instrument.id,
        status=status,
        pct=0,
        started_at="2026-02-10T00:00:00Z",
        cost_known=0,
        workspace_path=str(Path("workspace/runs") / run_id),
    )
    session.add(run)
    session.commit()
    return run


# ── start / conflict ─────────────────────────────────────────────────────


def test_start_creates_a_queued_run_with_a_stage_event(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    assert run.id
    assert Path(run.workspace_path or "").name == run.id
    events = session.execute(select(RunEvent).where(RunEvent.run_id == run.id)).scalars().all()
    assert events
    assert events[0].type == "run.stage"


def test_second_run_for_the_same_instrument_conflicts(session, instrument, manager) -> None:
    manager.start(session, instrument)
    with pytest.raises(RunConflict) as excinfo:
        manager.start(session, instrument)
    assert "activeRunId" in excinfo.value.detail


def test_conflict_includes_the_active_run_id(session, instrument, manager) -> None:
    first = manager.start(session, instrument)
    with pytest.raises(RunConflict) as excinfo:
        manager.start(session, instrument)
    assert excinfo.value.detail["activeRunId"] == first.id


def test_too_many_global_runs_is_429(session, instrument, manager) -> None:
    """§7.1 ★P2-02: at most two concurrent runs, per instrument one."""
    other = [
        Instrument(
            id=f"CN:00000{index}.SZ",
            symbol=f"00000{index}.SZ",
            name=f"测试{index}",
            market="CN",
            currency="CNY",
            tz="Asia/Shanghai",
            created_at="2026-02-10T00:00:00Z",
            updated_at="2026-02-10T00:00:00Z",
        )
        for index in (1, 2)
    ]
    session.add_all(other)
    session.commit()

    manager.start(session, instrument)
    manager.start(session, other[0])
    with pytest.raises(TooManyRuns) as excinfo:
        manager.start(session, other[1])
    assert excinfo.value.detail["retryAfterMs"] > 0


def test_cancelling_state_still_holds_the_lock(session, instrument, manager) -> None:
    """★P1-9: `cancelling` is in `ux_runs_active`, so a new run cannot sneak in."""
    run = manager.start(session, instrument)
    run.status = "cancelling"
    session.commit()
    with pytest.raises(RunConflict):
        manager.start(session, instrument)


# ── cancel ───────────────────────────────────────────────────────────────


def test_request_cancel_sets_the_transition_state(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    updated = manager.request_cancel(session, run.id)
    assert updated.status == "cancelling"
    assert updated.cancel_requested == 1


def test_cancel_on_a_finished_run_is_rejected(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    run.status = "succeeded"
    session.commit()
    with pytest.raises(RunNotActive):
        manager.request_cancel(session, run.id)


def test_cancel_unknown_run_is_404(session, manager) -> None:
    from app.errors import RunNotFound

    with pytest.raises(RunNotFound):
        manager.request_cancel(session, "nope")


# ── event log & replay ───────────────────────────────────────────────────


def test_events_are_sequenced_and_replayable(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    manager._record(session, run, "run.stage", {"stage": "snapshot", "pct": 15})
    manager._record(session, run, "run.log", {"level": "info", "message": "hello"})

    events = manager.replay(session, run.id, after=0)
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
    assert len(events) >= 3

    after_first = manager.replay(session, run.id, after=events[0]["seq"])
    assert all(event["seq"] > events[0]["seq"] for event in after_first)


def test_pct_never_goes_backwards(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    manager._record(session, run, "run.stage", {"stage": "research", "pct": 60})
    manager._record(session, run, "run.stage", {"stage": "prepare", "pct": 20})
    session.refresh(run)
    assert run.pct == 60


def test_subscribers_receive_published_events(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    queue = manager.subscribe(run.id)
    try:
        manager._record(session, run, "run.log", {"level": "info", "message": "live"})
        assert not queue.empty()
        event = queue.get_nowait()
        assert event is not None
        assert event["type"] == "run.log"
    finally:
        manager.unsubscribe(run.id, queue)


# ── startup recovery (AC-23) ─────────────────────────────────────────────


def test_interrupted_runs_are_recovered_on_startup(session, instrument, manager) -> None:
    """★P1-5: nothing survives a restart, so nothing may still look alive.

    One run per instrument — `ux_runs_active` forbids otherwise, which is itself
    the behaviour we want to keep proving.
    """
    session.add(instrument)
    for index, status in enumerate(("queued", "running", "cancelling"), start=1):
        extra = Instrument(
            id=f"CN:60000{index}.SH",
            symbol=f"60000{index}.SH",
            name=f"测试标的{index}",
            market="CN",
            currency="CNY",
            tz="Asia/Shanghai",
            created_at="2026-02-10T00:00:00Z",
            updated_at="2026-02-10T00:00:00Z",
        )
        session.add(extra)
        session.commit()
        _make_run(session, extra, status=status, run_id=f"run-{status}")

    recovered = manager.recover_interrupted(session)
    assert recovered == 3

    rows = session.execute(select(Run)).scalars().all()
    for row in rows:
        assert row.status == "failed"
        assert row.error_code == "interrupted"
        assert row.finished_at is not None

    error_events = session.execute(select(RunEvent).where(RunEvent.type == "run.error")).scalars().all()
    assert len(error_events) == 3


def test_recovery_leaves_finished_runs_alone(session, instrument, manager) -> None:
    finished = _make_run(session, instrument, status="succeeded", run_id="run-ok")
    assert manager.recover_interrupted(session) == 0
    session.refresh(finished)
    assert finished.status == "succeeded"


def test_single_worker_assertion(monkeypatch) -> None:
    """AC-23: a second worker must fail the boot, not silently mis-route SSE."""
    from app.main import assert_single_worker

    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    assert_single_worker()  # default is 1 → fine

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError) as excinfo:
        assert_single_worker()
    assert "单 worker" in str(excinfo.value)


# ── failure paths → provisional report (AC-21) ───────────────────────────


def test_failure_persists_a_provisional_report(session, instrument, manager, snapshot_payload) -> None:
    """★P1-10 / AC-21: a failed run still leaves something readable."""
    run = _make_run(session, instrument, status="running", run_id="run-fail")
    manager._finalize_failure(
        session,
        run,
        "result_schema_unsatisfied",
        "structured was null",
        snapshot_payload,
        result=AgentResult(text="模型输出的原始内容", source="aragon"),
    )

    session.expire_all()
    fresh = session.get(Run, "run-fail")
    assert fresh.status == "failed"
    assert fresh.error_code == "result_schema_unsatisfied"

    reports = session.execute(select(Report).where(Report.instrument_id == instrument.id)).scalars().all()
    assert len(reports) == 1
    assert reports[0].status == "provisional"
    assert reports[0].is_latest == 0
    assert reports[0].doc["provisional"] is True
    assert "模型输出的原始内容" in reports[0].doc["sections"][0]["blocks"][1]["text"]


def test_cancelled_run_does_not_persist_a_report(session, instrument, manager, snapshot_payload) -> None:
    """AC-11: cancelling leaves no half-finished report behind."""
    run = _make_run(session, instrument, status="running", run_id="run-cancel")
    manager._finalize_failure(session, run, "cancelled", "用户取消", snapshot_payload)

    session.expire_all()
    assert session.get(Run, "run-cancel").status == "cancelled"
    assert session.execute(select(Report)).scalars().all() == []


def test_budget_failure_is_marked_and_still_readable(session, instrument, manager, snapshot_payload) -> None:
    """AC-10 + AC-21: exit 3 → `budget_exceeded` plus a provisional version."""
    run = _make_run(session, instrument, status="running", run_id="run-budget")
    manager._finalize_failure(
        session,
        run,
        "budget_exceeded",
        "生成超出预算",
        snapshot_payload,
        result=AgentResult(text="写到一半的内容", exit_code=3, source="aragon"),
    )

    session.expire_all()
    assert session.get(Run, "run-budget").status == "budget_exceeded"
    reports = session.execute(select(Report)).scalars().all()
    assert len(reports) == 1 and reports[0].status == "provisional"


def test_failure_without_a_snapshot_still_records_the_error(session, instrument, manager) -> None:
    run = _make_run(session, instrument, status="running", run_id="run-nosnap")
    manager._finalize_failure(session, run, "data_provider_unavailable", "所有数据源都失败", None)
    session.expire_all()
    fresh = session.get(Run, "run-nosnap")
    assert fresh.status == "failed"
    assert fresh.error_message and "数据源" in fresh.error_message
    assert session.execute(select(Report)).scalars().all() == []


def test_run_error_event_is_terminal(session, instrument, manager, snapshot_payload) -> None:
    run = _make_run(session, instrument, status="running", run_id="run-err")
    manager._finalize_failure(session, run, "internal", "boom", snapshot_payload)
    events = manager.replay(session, run.id)
    assert events[-1]["type"] == "run.error"
    assert events[-1]["payload"]["code"] == "internal"
    assert events[-1]["payload"]["retryable"] is True


# ── progress mapping & cost masking ──────────────────────────────────────


def test_agent_progress_maps_into_the_research_stage(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    run.stage = "research"
    run.pct = 20
    session.commit()

    manager._forward_agent_event(
        session, run, AgentEvent(AgentEventType.progress, {"pct": 50, "turns": 3, "toolCalls": 4})
    )
    session.refresh(run)
    assert 20 <= run.pct <= 70


def test_cost_warning_only_fires_when_cost_is_known(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    before = len(manager.replay(session, run.id))

    manager._check_cost(session, run, AgentResult(cost_usd=99.0, cost_known=False))
    assert len(manager.replay(session, run.id)) == before, "unknown cost must not warn"

    manager._check_cost(session, run, AgentResult(cost_usd=99.0, cost_known=True))
    events = manager.replay(session, run.id)
    assert len(events) > before
    assert "成本" in events[-1]["payload"]["message"]


# ── workspace housekeeping ───────────────────────────────────────────────


def test_expired_workspaces_are_cleaned(session, manager, monkeypatch) -> None:
    import os
    import time

    from app.config import get_settings

    settings = get_settings()
    runs_dir = settings.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)

    old_dir = runs_dir / "old-run"
    old_dir.mkdir(exist_ok=True)
    (old_dir / "snapshot.json").write_text("{}", encoding="utf-8")
    eight_days = time.time() - 8 * 86400
    os.utime(old_dir, (eight_days, eight_days))

    fresh_dir = runs_dir / "fresh-run"
    fresh_dir.mkdir(exist_ok=True)

    monkeypatch.setattr(settings, "run_retention_days", 1, raising=False)
    removed = manager.cleanup_workspaces(session)
    assert removed >= 1
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_stream_events_ends_on_a_terminal_run(session, instrument, manager) -> None:
    run = manager.start(session, instrument)
    run.status = "succeeded"
    run.pct = 100
    session.commit()
    manager._record(session, run, "run.done", {"reportId": "r1", "version": 1}, touch_run=False)

    async def consume() -> list[dict]:
        out = []
        async for event in run_manager_module.stream_events(manager, session, run.id, after=0, poll_timeout=0.2):
            if event is not None:
                out.append(event)
        return out

    events = asyncio.run(consume())
    assert events[-1]["type"] == "run.done"
