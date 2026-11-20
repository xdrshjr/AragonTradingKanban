"""Lease-based scheduler — a *trigger source*, never a second scheduler (§18.3.3).

The whole design is one sentence: **this file decides *when* to call, never
*whether a run may start*.** Every run still goes through `RunManager.start()`, so
the semaphore and the per-instrument lock keep their single source of truth (O-3).

★V4-P0-2 is the reason the module is split in two:

* `tick_claim()` is **synchronous and pure DB** (leases + deferral checks), so it
  can safely run inside `asyncio.to_thread`;
* `mark_triggered` / `defer` / `record_failure` / `release_lease` are small DB
  writes;
* the **trigger** (`manager.start()` + `manager.schedule()`) happens back in the
  event-loop coroutine, because `schedule()` calls `asyncio.create_task` and would
  raise `RuntimeError: no running event loop` inside a worker thread. That failure
  used to be swallowed as a `failure_count` increment, which auto-disabled the
  schedule and looked exactly like "cron 配错了".

Leases use `UPDATE … WHERE claimed_by IS NULL`. That is database-level mutual
exclusion — an `asyncio.Lock` would do nothing inside `to_thread`, and SQLite's
single-writer semantics guarantee two concurrent ticks cannot both win.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import InvalidCron
from ..logging import get_logger
from ..models import Instrument, Report, Schedule
from . import cron as cron_module
from . import report_service

logger = get_logger("scheduler")

MAX_FAILURES = 3
DEFER_MINUTES = 5


def _iso(moment: datetime | None = None) -> str:
    return (
        (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:  # pragma: no cover - defensive
        return None


def new_schedule_id() -> str:
    return f"sch_{uuid.uuid4().hex}"


def claim_token() -> str:
    """`host:pid:uuid` — unique per claim attempt, used only for the lease."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Scheduler:
    """DB-only operations around one cron trigger. No asyncio, no RunManager."""

    def __init__(self, lease_seconds: int | None = None, batch_size: int | None = None) -> None:
        settings = get_settings()
        self.lease_seconds = settings.scheduler_lease_seconds if lease_seconds is None else lease_seconds
        self.batch_size = settings.scheduler_batch_size if batch_size is None else batch_size

    # ── leases ───────────────────────────────────────────────────────────
    def recover_dead_leases(self, session: Session) -> int:
        """Release leases whose holder died (no heartbeat, no lease expiry)."""
        cutoff = _iso(datetime.now(UTC) - timedelta(seconds=self.lease_seconds))
        result = session.execute(
            text(
                "UPDATE schedules SET claimed_by = NULL, claimed_at = NULL "
                "WHERE claimed_by IS NOT NULL AND claimed_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        session.commit()
        return int(result.rowcount or 0)

    def release_lease(self, session: Session, schedule_id: str) -> None:
        session.execute(
            sql_update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(claimed_by=None, claimed_at=None)
        )
        session.commit()
        # The lease was written with raw SQL, so any instance already in this
        # session's identity map still claims a lease it no longer holds — and
        # `mark_triggered` would then consider its own write a no-op.
        session.expire_all()

    # ── the only thing that runs in a thread ─────────────────────────────
    def tick_claim(self, session: Session) -> list[dict[str, Any]]:
        """Claim due schedules and return **plain dicts** — never ORM objects.

        Crossing a thread boundary with a live ORM instance is a bug waiting to
        happen (lazy loads on the wrong session), so the caller gets data, not rows.
        """
        self.recover_dead_leases(session)
        now = datetime.now(UTC)
        due = (
            session.execute(
                select(Schedule)
                .where(Schedule.enabled == 1, Schedule.next_run_at <= _iso(now), Schedule.claimed_by.is_(None))
                .order_by(Schedule.next_run_at.asc())
                .limit(self.batch_size)
            )
            .scalars()
            .all()
        )

        claimed: list[dict[str, Any]] = []
        for schedule in due:
            token = claim_token()
            result = session.execute(
                text(
                    "UPDATE schedules SET claimed_by = :token, claimed_at = :now "
                    "WHERE id = :sid AND claimed_by IS NULL"
                ),
                {"token": token, "now": _iso(now), "sid": schedule.id},
            )
            session.commit()
            if int(result.rowcount or 0) != 1:
                # Someone else won the lease; failing quietly is the correct behaviour.
                continue
            # The lease went in through raw SQL, so the ORM copy of this row is now
            # stale. Expire it before anyone (this method included) reads it again,
            # otherwise a later UPDATE looks like "no changes" and the lease never
            # gets released.
            session.expire_all()
            if not self._should_fire(session, schedule, now):
                self.release_lease(session, schedule.id)
                continue
            claimed.append(
                {
                    "id": schedule.id,
                    "instrument_id": schedule.instrument_id,
                    "cron": schedule.cron,
                    "min_age_hours": schedule.min_age_hours,
                }
            )
        return claimed

    def _should_fire(self, session: Session, schedule: Schedule, now: datetime) -> bool:
        """Deferral checks — still pure DB, still inside the thread (AC-30)."""
        report = (
            session.execute(
                select(Report)
                .where(Report.instrument_id == schedule.instrument_id, Report.deleted_at.is_(None))
                .order_by(Report.generated_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if report is not None:
            generated = _parse(report.generated_at)
            if generated is not None:
                age_hours = (now - generated).total_seconds() / 3600
                if age_hours < schedule.min_age_hours:
                    logger.info(
                        "schedule %s skip: report age %.1fh < min %sh", schedule.id, age_hours, schedule.min_age_hours
                    )
                    self.advance_next_run(session, schedule)
                    return False

        if report_service.count_active_runs(session) >= _max_concurrent():
            logger.info("schedule %s defer: run capacity exhausted", schedule.id)
            self.defer(session, schedule.id, minutes=DEFER_MINUTES)
            return False
        return True

    # ── bookkeeping ──────────────────────────────────────────────────────
    def mark_triggered(self, session: Session, schedule_id: str, run_id: str) -> None:
        schedule = session.get(Schedule, schedule_id)
        if schedule is None:  # pragma: no cover - deleted mid-flight
            return
        schedule.last_run_id = run_id
        schedule.failure_count = 0
        schedule.claimed_by = None
        schedule.claimed_at = None
        schedule.updated_at = _iso()
        schedule.next_run_at = _iso(cron_module.next_fire(schedule.cron, datetime.now(UTC)))
        session.commit()

    def advance_next_run(self, session: Session, schedule: Schedule) -> None:
        schedule.next_run_at = _iso(cron_module.next_fire(schedule.cron, datetime.now(UTC)))
        schedule.updated_at = _iso()
        schedule.claimed_by = None
        schedule.claimed_at = None
        session.commit()

    def defer(self, session: Session, schedule_id: str, *, minutes: int = DEFER_MINUTES) -> None:
        """Give the slot back to manual runs; the schedule survives untouched."""
        schedule = session.get(Schedule, schedule_id)
        if schedule is None:  # pragma: no cover
            return
        schedule.next_run_at = _iso(datetime.now(UTC) + timedelta(minutes=minutes))
        schedule.claimed_by = None
        schedule.claimed_at = None
        schedule.updated_at = _iso()
        session.commit()

    def record_failure(self, session: Session, schedule_id: str) -> None:
        schedule = session.get(Schedule, schedule_id)
        if schedule is None:  # pragma: no cover
            return
        schedule.failure_count = int(schedule.failure_count or 0) + 1
        schedule.claimed_by = None
        schedule.claimed_at = None
        schedule.updated_at = _iso()
        if schedule.failure_count >= MAX_FAILURES:
            schedule.enabled = 0
            logger.warning("schedule %s disabled after %s failures", schedule_id, schedule.failure_count)
        try:
            schedule.next_run_at = _iso(cron_module.next_fire(schedule.cron, datetime.now(UTC)))
        except InvalidCron:  # pragma: no cover - stored expression was validated
            schedule.enabled = 0
        session.commit()


def _max_concurrent() -> int:
    from .run_manager import MAX_CONCURRENT_RUNS

    return MAX_CONCURRENT_RUNS


# ── CRUD helpers (used by the router and the tests) ──────────────────────────


def create(
    session: Session,
    instrument: Instrument,
    expression: str,
    *,
    enabled: bool = True,
    min_age_hours: int = 12,
    owner_id: str | None = None,
) -> Schedule:
    normalized = cron_module.validate(expression)
    now = datetime.now(UTC)
    schedule = Schedule(
        id=new_schedule_id(),
        instrument_id=instrument.id,
        cron=normalized,
        enabled=1 if enabled else 0,
        min_age_hours=max(1, min(int(min_age_hours), 720)),
        owner_id=owner_id,
        next_run_at=_iso(cron_module.next_fire(normalized, now)),
        created_at=_iso(now),
        updated_at=_iso(now),
    )
    session.add(schedule)
    session.commit()
    return schedule


def update(
    session: Session,
    schedule: Schedule,
    *,
    expression: str | None = None,
    enabled: bool | None = None,
    min_age_hours: int | None = None,
) -> Schedule:
    if expression is not None:
        normalized = cron_module.validate(expression)
        schedule.cron = normalized
        schedule.next_run_at = _iso(cron_module.next_fire(normalized, datetime.now(UTC)))
    if enabled is not None:
        schedule.enabled = 1 if enabled else 0
    if min_age_hours is not None:
        schedule.min_age_hours = max(1, min(int(min_age_hours), 720))
    schedule.updated_at = _iso()
    session.commit()
    return schedule


def list_all(session: Session) -> list[Schedule]:
    return list(session.execute(select(Schedule).order_by(Schedule.created_at.desc())).scalars().all())


def symbol_of(session: Session, instrument_id: str) -> str | None:
    instrument = session.get(Instrument, instrument_id)
    return instrument.symbol if instrument else None


__all__ = [
    "MAX_FAILURES",
    "Scheduler",
    "create",
    "list_all",
    "new_schedule_id",
    "symbol_of",
    "update",
]
