"""The asyncio loop that drives `Scheduler` — mounted by `main.py` lifespan.

Order of operations for one tick (★V4-P0-2):

1. `asyncio.to_thread(tick_claim)` — DB only: leases, deferral, due list;
2. back in the loop: `RunManager.start()` + `manager.schedule()` for each claim;
3. bookkeeping writes (`mark_triggered` / `defer` / `record_failure`).

Step 2 **cannot** move into the thread: `schedule()` calls `asyncio.create_task`,
which needs a running loop. AC-43 pins both the positive and the negative case.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from ..config import get_settings
from ..db.session import SessionLocal
from ..errors import RunConflict, TooManyRuns
from ..logging import get_logger
from ..models import Instrument
from .run_manager import get_manager
from .scheduler import Scheduler

logger = get_logger("scheduler_ticker")


class SchedulerTicker:
    """One task, one loop, no second concurrency source (O-3)."""

    def __init__(self, tick_seconds: int | None = None) -> None:
        settings = get_settings()
        self.tick_seconds = settings.scheduler_tick_seconds if tick_seconds is None else tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler-ticker")
        logger.info("scheduler ticker started (every %ss)", self.tick_seconds)

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        logger.info("scheduler ticker stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
                return  # stop requested
            except TimeoutError:
                pass
            try:
                await self.tick_once()
            except Exception:  # pragma: no cover - a bad schedule must not kill the loop
                logger.exception("scheduler tick failed")

    # ── one tick (also the unit-test entry point) ────────────────────────
    async def tick_once(self) -> int:
        """Claim in a thread, trigger in the loop. Returns how many runs started."""
        due = await asyncio.to_thread(self._claim)
        started = 0
        for item in due:
            if await self._trigger(item):
                started += 1
        return started

    def _claim(self) -> list[dict]:
        session = SessionLocal()
        try:
            return Scheduler().tick_claim(session)
        finally:
            session.close()

    async def _trigger(self, item: dict) -> bool:
        """`start()` + `schedule()` — only ever called from the event loop."""
        manager = get_manager()
        session = SessionLocal()
        scheduler = Scheduler()
        try:
            instrument = session.get(Instrument, item["instrument_id"])
            if instrument is None:  # pragma: no cover - instrument deleted
                scheduler.release_lease(session, item["id"])
                return False
            try:
                run = manager.start(session, instrument, reason="scheduled")
            except (TooManyRuns, RunConflict):
                # A manual run got there first:让步 is the specified behaviour (AC-30).
                scheduler.defer(session, item["id"], minutes=5)
                return False
            manager.schedule(run.id)
            scheduler.mark_triggered(session, item["id"], run.id)
            logger.info(
                "scheduled run started schedule=%s run=%s instrument=%s at=%s",
                item["id"],
                run.id,
                instrument.id,
                datetime.now(UTC).isoformat(),
            )
            return True
        except Exception:
            logger.exception("scheduled trigger failed schedule=%s", item["id"])
            scheduler.record_failure(session, item["id"])
            return False
        finally:
            session.close()


_ticker: SchedulerTicker | None = None


def get_ticker() -> SchedulerTicker:
    global _ticker
    if _ticker is None:
        _ticker = SchedulerTicker()
    return _ticker


def reset_ticker() -> None:
    """Test hook."""
    global _ticker
    _ticker = None


__all__ = ["SchedulerTicker", "get_ticker", "reset_ticker"]
