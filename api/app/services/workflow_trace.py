"""Progress-marker parsing (spec §3.6.3) — **observation, never a gate**.

The agent reports progress with lines like `[[wf:S2:done]]`. `TraceParser` turns a stream of text
deltas into (display text with the markers removed, step events). Everything here is a pure
function of the characters fed in: any chunking of the same string yields the same result.

Soft by design: markers may be repeated (idempotent), out of order, missing, or for unknown keys
(counted, ignored). Nothing in this module can fail a run.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import workflow_contract as C

_OPENER = "[[wf:"


@dataclass(frozen=True)
class StepEvent:
    key: str
    state: str  # start | done | skip
    note: str | None
    node_id: str | None


@dataclass(frozen=True)
class TraceChunk:
    text: str
    events: tuple[StepEvent, ...] = ()


@dataclass(frozen=True)
class TraceSummary:
    """Counts are per **key** (deduplicated). `markers_seen` counts every recognised marker for a
    known key, duplicates included; `unknown_markers` counts markers for keys not in the plan."""

    started_keys: tuple[str, ...]
    done_keys: tuple[str, ...]
    skipped_keys: tuple[str, ...]
    markers_seen: int
    unknown_markers: int

    @property
    def started(self) -> int:
        return len(self.started_keys)

    @property
    def done(self) -> int:
        return len(self.done_keys)

    @property
    def skipped(self) -> int:
        return len(self.skipped_keys)

    @property
    def finished_keys(self) -> tuple[str, ...]:
        """Keys reported `done` or `skip` (a key reported both counts once)."""
        return tuple(dict.fromkeys((*self.done_keys, *self.skipped_keys)))


def _holdable(tail: str) -> bool:
    """Could `tail` (starting at a `[`) still become a marker once more text arrives?"""
    if "\n" in tail or len(tail) > C.MARKER_BUFFER_MAX:
        return False
    if len(tail) < len(_OPENER):
        return _OPENER.startswith(tail)
    return tail.startswith(_OPENER) and "]]" not in tail


def _split_pending(tail: str) -> tuple[str, str]:
    """Split the unmatched remainder into (text that is final, suffix that may be a partial marker).

    The **earliest** holdable `[` wins: a note may legally contain `[`, so a later `[[wf:` does
    not make an earlier unfinished marker impossible (this is what keeps chunking invisible)."""
    for index in range(max(0, len(tail) - C.MARKER_BUFFER_MAX), len(tail)):
        if tail[index] == "[" and _holdable(tail[index:]):
            return tail[:index], tail[index:]
    return tail, ""


class TraceParser:
    """Incremental marker parser. Feed deltas with `feed`, call `finish` once at the end."""

    def __init__(self, key_to_node: dict[str, str]) -> None:
        self._key_to_node = dict(key_to_node)
        self._pending = ""
        self._line_start = True
        self._swallow_newline = False
        self._seen: set[tuple[str, str]] = set()
        self._started: dict[str, None] = {}
        self._done: dict[str, None] = {}
        self._skipped: dict[str, None] = {}
        self._markers_seen = 0
        self._unknown = 0

    # ── public ───────────────────────────────────────────────────────────

    def feed(self, delta: str) -> TraceChunk:
        return self._process(self._pending + delta, final=False)

    def finish(self) -> TraceChunk:
        return self._process(self._pending, final=True)

    def summary(self) -> TraceSummary:
        return TraceSummary(
            started_keys=tuple(self._started),
            done_keys=tuple(self._done),
            skipped_keys=tuple(self._skipped),
            markers_seen=self._markers_seen,
            unknown_markers=self._unknown,
        )

    # ── internals ────────────────────────────────────────────────────────

    def _process(self, data: str, *, final: bool) -> TraceChunk:
        out: list[str] = []
        events: list[StepEvent] = []
        cursor = 0
        for match in C.MARKER_PATTERN.finditer(data):
            self._emit(data[cursor : match.start()], out)
            self._on_marker(match.group(1), match.group(2), match.group(3), events)
            cursor = match.end()
        tail = data[cursor:]
        keep = ""
        if not final:
            tail, keep = _split_pending(tail)
        self._emit(tail, out)
        self._pending = keep
        return TraceChunk("".join(out), tuple(events))

    def _emit(self, text: str, out: list[str]) -> None:
        """Append plain text; one newline directly after a line-leading marker is dropped so a
        marker on its own line leaves no blank line behind."""
        if self._swallow_newline and text:
            if text.startswith("\n"):
                text = text[1:]
            self._swallow_newline = False
        if text:
            out.append(text)
            self._line_start = text.endswith("\n")

    def _on_marker(self, key: str, state: str, note: str | None, events: list[StepEvent]) -> None:
        at_line_start = self._line_start
        self._swallow_newline = at_line_start
        if key not in self._key_to_node:
            self._unknown += 1
            return
        self._markers_seen += 1
        if state == "done" and (key, "start") not in self._seen:
            self._record(key, "start", None, events)
        self._record(key, state, (note or "").strip() or None, events)

    def _record(self, key: str, state: str, note: str | None, events: list[StepEvent]) -> None:
        if (key, state) in self._seen:
            return
        self._seen.add((key, state))
        target = {"start": self._started, "done": self._done, "skip": self._skipped}[state]
        target[key] = None
        events.append(StepEvent(key, state, note, self._key_to_node.get(key)))


def strip_markers(text: str) -> str:
    """Stateless: delete every marker from a whole text, with the same blank-line rule as the
    streaming parser (unknown keys are removed too)."""
    parser = TraceParser({})
    return parser.feed(text).text + parser.finish().text
