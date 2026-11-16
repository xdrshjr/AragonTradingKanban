"""Per-ask lifecycle registry (★V4-P1-5).

`RunManager` is run-shaped from top to bottom: `_tasks` / `_cancels` / `is_active`
/ `replay` are all keyed by `run_id` and the terminal-state check reads
`run_events`. Re-using it for a `deep` ask produced two concrete defects (§18.3.2):
a `POST /asks/{id}/cancel` that is silently a no-op, and an SSE loop that can
never know when to close.

Hence a small **parallel but separate** registry keyed by `askId`. An ask never
writes a `runs` row — that would pollute the generation audit trail and collide
with `ux_runs_active` (C-12).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from ..logging import get_logger

logger = get_logger("ask_registry")


class AskRegistry:
    """Tracks the live `deep` tasks so cancel and stream close are decidable."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancels: dict[str, Callable[[], Any]] = {}

    def register(
        self,
        ask_id: str,
        task: asyncio.Task[None],
        cancel: Callable[[], Any] | None = None,
    ) -> None:
        self._tasks[ask_id] = task
        if cancel is not None:
            self._cancels[ask_id] = cancel
        task.add_done_callback(lambda finished: self._forget(ask_id, finished))

    def cancel(self, ask_id: str) -> bool:
        """Ask the task to stop. Returns False when there was nothing live.

        The cancellation callback may be a coroutine (killing a process tree is
        async). If we are on the event loop it is scheduled as a task; if we are
        not (e.g. a sync router running in a worker thread), the ask task's own
        `finally` still kills the child — so the kill can never be lost to a
        "coroutine was never awaited" warning.
        """
        task = self._tasks.get(ask_id)
        if task is None or task.done():
            return False
        cancellation = self._cancels.get(ask_id)
        if cancellation is not None:
            try:
                if inspect.iscoroutinefunction(cancellation):
                    # Killing a process tree is async. Scheduling it onto the task's
                    # own loop works from any thread (the router may be in a worker
                    # thread), and never creates an un-awaited coroutine.
                    asyncio.run_coroutine_threadsafe(cancellation(), task.get_loop())
                else:
                    cancellation()
            except RuntimeError:  # pragma: no cover - loop already closed
                logger.info("ask cancel callback deferred to the task itself id=%s", ask_id)
            except Exception:  # pragma: no cover - the task also gets `.cancel()`
                logger.exception("ask cancel callback failed id=%s", ask_id)
        # `Task.cancel()` is not thread-safe: this may be a sync route running in a
        # worker thread, so cancelling it directly would race the loop's ready queue
        # (and can be lost). Hop back onto the owning loop instead.
        try:
            task.get_loop().call_soon_threadsafe(task.cancel)
        except RuntimeError:  # pragma: no cover - loop already closed
            logger.info("ask loop already closed id=%s", ask_id)
        return True

    def is_active(self, ask_id: str) -> bool:
        task = self._tasks.get(ask_id)
        return task is not None and not task.done()

    def forget(self, ask_id: str) -> None:
        self._tasks.pop(ask_id, None)
        self._cancels.pop(ask_id, None)

    def _forget(self, ask_id: str, task: asyncio.Task[None]) -> None:
        self.forget(ask_id)
        try:
            task.result()
        except asyncio.CancelledError:  # pragma: no cover - expected on cancel
            pass
        except Exception:  # pragma: no cover - already recorded on the ask row
            logger.exception("ask task crashed id=%s", ask_id)

    def active_ids(self) -> list[str]:
        return [ask_id for ask_id, task in self._tasks.items() if not task.done()]


_registry: AskRegistry | None = None


def get_ask_registry() -> AskRegistry:
    global _registry
    if _registry is None:
        _registry = AskRegistry()
    return _registry


def reset_ask_registry() -> None:
    """Test hook."""
    global _registry
    _registry = None


__all__ = ["AskRegistry", "get_ask_registry", "reset_ask_registry"]
