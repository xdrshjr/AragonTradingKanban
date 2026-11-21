"""In-memory lifecycle + event ring of the AI workflow assistant (spec §3.8.6).

Deliberately **not** shaped like `ask_registry` (which has no buffer and no resume): each assist
keeps a bounded `deque(maxlen=200)` of events with a monotonic sequence number, so an SSE client
that reconnects with `?after=<seq>` gets exactly the events it missed. Process-local on purpose —
the single-worker invariant (`assert_single_worker`) is what makes that sound; after a restart the
registry is empty and the half-finished conversation is recognised from the chat table (a `user`
message with no `assistant` reply).
"""

from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from ..logging import get_logger

logger = get_logger("workflow_assist")

RING_SIZE = 200
#: How many finished assists stay replayable (a late reconnect still sees `assist.end`).
KEEP_FINISHED = 64
TERMINAL_EVENT = "assist.end"


@dataclass
class AssistState:
    assist_id: str
    template_id: str
    user_id: str | None
    ring: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=RING_SIZE))
    seq: int = 0
    terminal: bool = False
    cancel_requested: bool = False
    task: asyncio.Task[None] | None = None
    cancel: Callable[[], Any] | None = None


class AssistRegistry:
    def __init__(self) -> None:
        self._live: dict[str, AssistState] = {}
        self._finished: OrderedDict[str, AssistState] = OrderedDict()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def active_for_template(self, template_id: str) -> str | None:
        for state in self._live.values():
            if state.template_id == template_id and not state.terminal:
                return state.assist_id
        return None

    def create(self, assist_id: str, template_id: str, user_id: str | None) -> AssistState:
        state = AssistState(assist_id=assist_id, template_id=template_id, user_id=user_id)
        self._live[assist_id] = state
        return state

    def get(self, assist_id: str) -> AssistState | None:
        return self._live.get(assist_id) or self._finished.get(assist_id)

    def attach(
        self, assist_id: str, task: asyncio.Task[None] | None = None, cancel: Callable[[], Any] | None = None
    ) -> None:
        state = self._live.get(assist_id)
        if state is None:
            return
        if task is not None:
            state.task = task
        if cancel is not None:
            state.cancel = cancel

    def finish(self, assist_id: str) -> None:
        state = self._live.pop(assist_id, None)
        if state is None:
            return
        state.terminal = True
        self._finished[assist_id] = state
        while len(self._finished) > KEEP_FINISHED:
            self._finished.popitem(last=False)

    def reset(self) -> None:
        self._live.clear()
        self._finished.clear()

    # ── events ───────────────────────────────────────────────────────────────
    def emit(self, assist_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        state = self.get(assist_id)
        if state is None:
            return None
        state.seq += 1
        event = {"seq": state.seq, "type": event_type, "payload": payload}
        state.ring.append(event)
        if event_type == TERMINAL_EVENT:
            state.terminal = True
        return event

    def events_after(self, assist_id: str, after: int) -> list[dict[str, Any]]:
        state = self.get(assist_id)
        return [event for event in state.ring if event["seq"] > after] if state else []

    async def stream(
        self, assist_id: str, after: int = 0, *, poll_interval: float = 0.1, keepalive_sec: float = 15.0
    ) -> AsyncIterator[dict[str, Any] | None]:
        """Yield events after `after`, `None` as a keep-alive tick, and return once terminal."""
        cursor, idle = after, 0.0
        while True:
            events = self.events_after(assist_id, cursor)
            for event in events:
                cursor = int(event["seq"])
                yield event
            state = self.get(assist_id)
            if state is None or (state.terminal and not events):
                return
            idle = 0.0 if events else idle + poll_interval
            if idle >= keepalive_sec:
                idle = 0.0
                yield None
            await asyncio.sleep(poll_interval)

    # ── cancel (callable from a worker thread, like `AskRegistry.cancel`) ────
    def cancel(self, assist_id: str) -> bool:
        state = self._live.get(assist_id)
        if state is None or state.task is None or state.task.done():
            return False
        state.cancel_requested = True
        loop = state.task.get_loop()
        if state.cancel is not None:
            try:
                if inspect.iscoroutinefunction(state.cancel):
                    asyncio.run_coroutine_threadsafe(state.cancel(), loop)
                else:
                    state.cancel()
            except RuntimeError:  # pragma: no cover - loop already closed
                logger.info("assist cancel callback deferred to the task itself id=%s", assist_id)
        try:
            loop.call_soon_threadsafe(state.task.cancel)
        except RuntimeError:  # pragma: no cover - loop already closed
            logger.info("assist loop already closed id=%s", assist_id)
        return True

    def active_ids(self) -> list[str]:
        return [aid for aid, state in self._live.items() if state.task is not None and not state.task.done()]


_registry: AssistRegistry | None = None


def get_assist_registry() -> AssistRegistry:
    global _registry
    if _registry is None:
        _registry = AssistRegistry()
    return _registry


def reset_assist_registry() -> None:
    """Test hook."""
    global _registry
    _registry = None


__all__ = ["AssistRegistry", "AssistState", "get_assist_registry", "reset_assist_registry"]
