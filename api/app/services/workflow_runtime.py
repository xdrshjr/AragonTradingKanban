"""Run-time side of the research workflow (spec `report-workflow-graph` §3.6).

`run_manager` only gets a handful of call sites; every decision lives here:

* `resolve_for_run` — at `start()`: which published version does this run freeze onto?
  Never raises (an admin's configuration problem must not 500 a user's generation).
* `prepare` — in the `prepare` stage: read the frozen document, compile it, fall back to the
  code-owned built-in on *any* trouble. A workflow is an enhancement, never a gate (§3.6.6).
* `WorkflowTracker` — observation only: turns progress markers into `run.workflow` events and a
  monotonic progress bar, and summarises adherence. It can never fail a run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..logging import get_logger
from ..models import Run, WorkflowTemplate, WorkflowVersion
from ..schemas.run import STAGE_BOUNDS, STAGE_START
from ..schemas.workflow import WorkflowDoc, doc_from_json
from . import settings_service
from . import workflow_contract as C
from .workflow_builtin import builtin_doc
from .workflow_compiler import CompileContext, CompiledWorkflow, WorkflowCompileError, compile_workflow
from .workflow_trace import TraceParser, TraceSummary, strip_markers

logger = get_logger("workflow_runtime")

BUILTIN_CODE_LABEL = f"{C.BUILTIN_NAME}（内置）"
_TIMEFRAME = "日线"
#: Fallback reasons recorded in `quality.workflow.fallbackReason` (§3.6.6).
FALLBACK_DOC_CORRUPT = "doc_corrupt"
FALLBACK_COMPILE_FAILED = "compile_failed"
FALLBACK_PLAN_TOO_LARGE = "plan_too_large"


@dataclass(frozen=True)
class ResolvedWorkflow:
    """What `start()` freezes onto the run row."""

    version_id: str | None  # None ⇒ the code-owned built-in
    label: str  # "标准五段式研究 v3" (denormalised for lists and the overlay)


def resolve_for_run(db: Session) -> ResolvedWorkflow | None:
    """`None` only when `workflow.enabled != "1"`; otherwise always a result (§3.6.1, RW-2).

    "No default row" is **not** "disabled": it is a fallback to the code-owned built-in.
    """
    try:
        if not settings_service.workflow_enabled(db):
            return None
        row = db.execute(
            select(WorkflowTemplate, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.id == WorkflowTemplate.current_version_id)
            .where(WorkflowTemplate.is_default == 1, WorkflowTemplate.status == "active")
        ).first()
        if row is not None:
            template, version = row
            return ResolvedWorkflow(version.id, f"{template.name} v{version.version}"[:80])
    except Exception:  # noqa: BLE001 - never turn a workflow problem into a failed start
        logger.exception("resolve_for_run failed; falling back to the built-in workflow")
        db.rollback()
    return ResolvedWorkflow(None, BUILTIN_CODE_LABEL)


# ── prepare ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreparedWorkflow:
    compiled: CompiledWorkflow
    template_id: str | None
    template_name: str
    version: int | None
    version_id: str | None
    source: str  # published | builtin-code
    fallback_reason: str | None
    max_turns: int
    max_duration_ms: int
    markers: bool

    @property
    def plan_md(self) -> str:
        return self.compiled.plan_md

    @property
    def inline_index(self) -> str:
        return self.compiled.inline_index

    @property
    def manifest(self) -> dict[str, Any]:
        """`workflow.json` — the agent-readable key map (no prompt text, no secrets)."""
        return {
            "schema": "aragon.workflow.run/v1",
            "templateName": self.template_name,
            "version": self.version,
            "sha1": self.compiled.sha1,
            "items": [
                {"key": i.key, "kind": i.kind, "title": i.title, "nodeId": i.node_id} for i in self.compiled.items
            ],
            "key_to_node": dict(self.compiled.key_to_node),
            "policy": {"progressMarkers": self.markers},
        }

    def plan_payload(self) -> dict[str, Any]:
        """The `run.workflow{kind:"plan"}` event — titles, roles, levels only, never prompt text."""
        items = []
        for item in self.compiled.items:
            entry: dict[str, Any] = {
                "key": item.key,
                "kind": item.kind,
                "title": item.title,
                "level": item.level,
                "upstream": list(item.upstream),
            }
            if item.kind == "step":
                entry["role"] = item.role
                entry["sections"] = list(item.sections)
            items.append(entry)
        return {
            "kind": "plan",
            "templateName": self.template_name,
            "version": self.version,
            "hash": self.compiled.sha1,
            "fallback": self.source != "published",
            "total": len(items),
            "items": items,
        }


def _context(snapshot: dict[str, Any]) -> CompileContext:
    instrument = snapshot.get("instrument") or {}
    return CompileContext(
        symbol=str(instrument.get("symbol") or ""),
        name=str(instrument.get("name") or ""),
        market=str(instrument.get("market") or ""),
        currency=str(instrument.get("currency") or ""),
        sector=str(instrument.get("sector") or ""),
        as_of=str(snapshot.get("asOf") or ""),
        timeframe=_TIMEFRAME,
    )


def _load_frozen(db: Session, version_id: str) -> tuple[WorkflowDoc, WorkflowTemplate, WorkflowVersion]:
    version = db.get(WorkflowVersion, version_id)
    template = db.get(WorkflowTemplate, version.template_id) if version is not None else None
    if version is None or template is None:
        raise LookupError(f"workflow version {version_id} is missing")
    return doc_from_json(version.doc_json), template, version


def _effective_limits(doc: WorkflowDoc) -> tuple[int, int]:
    """`min(policy, site-wide)` — a workflow can only tighten the ceilings (§3.6.2)."""
    settings = get_settings()
    turns, duration = settings.aragon_max_turns, settings.aragon_max_duration_ms
    if doc.policy.maxTurns:
        turns = min(turns, doc.policy.maxTurns)
    if doc.policy.maxDurationMinutes:
        duration = min(duration, doc.policy.maxDurationMinutes * 60_000)
    return turns, duration


def _fallback_reason(error: Exception) -> str:
    if isinstance(error, WorkflowCompileError):
        codes = {issue.code for issue in error.report.issues}
        return FALLBACK_PLAN_TOO_LARGE if "plan_too_large" in codes else FALLBACK_COMPILE_FAILED
    return FALLBACK_DOC_CORRUPT


def prepare(db: Session, run: Run, snapshot: dict[str, Any]) -> PreparedWorkflow | None:
    """Compile the run's frozen workflow. `None` ⇒ legacy prompt (`workflow.enabled=0`, or the
    built-in itself would not compile — logged at error level, guarded by a test)."""
    if run.workflow_label is None:
        return None
    context = _context(snapshot)
    reason: str | None = None
    if run.workflow_version_id:
        try:
            doc, template, version = _load_frozen(db, run.workflow_version_id)
            compiled = compile_workflow(doc, context, template_name=template.name, version=version.version)
            turns, duration = _effective_limits(doc)
            return PreparedWorkflow(
                compiled, template.id, template.name, version.version, version.id, "published", None,
                turns, duration, doc.policy.progressMarkers,
            )  # fmt: skip
        except (LookupError, ValueError, ValidationError, WorkflowCompileError) as error:
            reason = _fallback_reason(error)
            logger.warning("workflow_fallback run=%s reason=%s detail=%s", run.id, reason, error)
    return _prepare_builtin(run, context, reason)


def _prepare_builtin(run: Run, context: CompileContext, reason: str | None) -> PreparedWorkflow | None:
    doc = builtin_doc()
    try:
        compiled = compile_workflow(doc, context, template_name=C.BUILTIN_NAME, version=None)
    except WorkflowCompileError:  # pragma: no cover - a test guards "the built-in always compiles"
        logger.error("workflow_fallback run=%s: built-in workflow does not compile; legacy prompt", run.id)
        return None
    turns, duration = _effective_limits(doc)
    return PreparedWorkflow(
        compiled, C.BUILTIN_TEMPLATE_ID, C.BUILTIN_NAME, None, None, "builtin-code", reason,
        turns, duration, doc.policy.progressMarkers,
    )  # fmt: skip


# ── observation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TracedText:
    """What one `text` delta turns into."""

    text_payload: dict[str, Any] | None  # `None` ⇒ pure markers; write no empty `agent.text`
    step_payloads: tuple[dict[str, Any], ...]
    pct: int | None


class WorkflowTracker:
    """Per-run observation state. Pure bookkeeping — it never raises into the run."""

    def __init__(self, prepared: PreparedWorkflow) -> None:
        self.prepared = prepared
        self._parser = TraceParser(prepared.compiled.key_to_node)
        self._total = len(prepared.compiled.items)
        self._keys = tuple(item.key for item in prepared.compiled.items)

    def _pct(self) -> int | None:
        summary = self._parser.summary()
        if not summary.markers_seen or not self._total:
            return None
        finished = len(summary.finished_keys)
        in_flight = len(set(summary.started_keys) - set(summary.finished_keys))
        research = STAGE_START["research"]
        span = STAGE_BOUNDS["research"][0] - research
        value = research + int(span * (finished + 0.5 * in_flight) / self._total)
        return min(STAGE_BOUNDS["research"][0] - 1, value)

    def _step_payloads(self, events: Any) -> tuple[dict[str, Any], ...]:
        done = len(self._parser.summary().finished_keys)
        out = []
        for event in events:
            payload: dict[str, Any] = {
                "kind": "step",
                "key": event.key,
                "state": event.state,
                "doneCount": done,
                "total": self._total,
            }
            if event.note:
                payload["note"] = event.note
            out.append(payload)
        return tuple(out)

    def feed(self, payload: dict[str, Any]) -> TracedText:
        """Strip markers from an `agent.text` payload (keeping its other keys, e.g. `final`)."""
        chunk = self._parser.feed(str(payload.get("delta", payload.get("text", ""))))
        return self._wrap(payload, chunk)

    def _wrap(self, payload: dict[str, Any], chunk: Any) -> TracedText:
        key = "delta" if "delta" in payload or "text" not in payload else "text"
        text_payload = {**payload, key: chunk.text} if chunk.text else None
        return TracedText(text_payload, self._step_payloads(chunk.events), self._pct() if chunk.events else None)

    def summary(self) -> TraceSummary:
        return self._parser.summary()

    def adherence(self) -> str:
        if not self.prepared.markers:
            return "unreported"
        summary = self._parser.summary()
        if not summary.markers_seen:
            return "none"
        if self._total and len(summary.finished_keys) >= self._total:
            return "full"
        return "partial"

    def quality_payload(self) -> dict[str, Any]:
        """`quality.workflow` (§3.6.5) — producer side of the three-place landing."""
        summary = self._parser.summary()
        gates = [i for i in self.prepared.compiled.items if i.kind == "gate"]
        steps = [i for i in self.prepared.compiled.items if i.kind == "step"]
        finished = set(summary.finished_keys)
        prepared = self.prepared
        return {
            "templateId": prepared.template_id,
            "templateName": prepared.template_name,
            "version": prepared.version,
            "versionId": prepared.version_id,
            "hash": prepared.compiled.sha1,
            "source": prepared.source,
            "fallbackReason": prepared.fallback_reason,
            "steps": {
                "total": len(steps),
                "started": sum(1 for i in steps if i.key in summary.started_keys),
                "done": sum(1 for i in steps if i.key in summary.done_keys),
                "skipped": sum(1 for i in steps if i.key in summary.skipped_keys),
            },
            "gates": {"total": len(gates), "done": sum(1 for g in gates if g.key in finished)},
            "adherence": self.adherence(),
            "markersSeen": summary.markers_seen,
            "unknownMarkers": summary.unknown_markers,
        }


def close_trace(tracker: WorkflowTracker | None, result: Any) -> list[tuple[str, dict[str, Any]]]:
    """Run end (§3.6.3 收尾顺序): flush the parser, then strip markers from `result.text` so the
    prose rescue and the provisional report never see them (RW-3). Returns events to record."""
    if tracker is None:
        return []
    chunk = tracker._parser.finish()  # noqa: SLF001 - same module family, deliberate
    events: list[tuple[str, dict[str, Any]]] = []
    traced = tracker._wrap({"delta": ""}, chunk)  # noqa: SLF001
    if traced.text_payload is not None:
        events.append(("agent.text", traced.text_payload))
    events.extend(("run.workflow", step) for step in traced.step_payloads)
    if result is not None and getattr(result, "text", None):
        result.text = strip_markers(result.text)
    return events


# ── run_manager glue (so `run_manager.py` only carries call sites, §4 / 评审条件 4) ─────────

#: `record(event_type, payload, touch_run=...)` — `RunManager._record` bound to (session, run).
Recorder = Callable[..., Any]


def begin(
    trackers: dict[str, WorkflowTracker], run: Run, prepared: PreparedWorkflow | None, record: Recorder
) -> None:
    """Register the run's tracker and announce the plan (and a fallback, if there was one)."""
    if prepared is None:
        return
    trackers[run.id] = WorkflowTracker(prepared)
    if prepared.fallback_reason:
        message = f"研究流程已回退为内置流程（{prepared.fallback_reason}）"
        record("run.log", {"level": "warn", "code": "workflow_fallback", "message": message}, touch_run=False)
    record("run.workflow", prepared.plan_payload(), touch_run=False)


def forward_text(tracker: WorkflowTracker | None, payload: dict[str, Any], record: Recorder) -> None:
    """One `agent.text` delta. Without a tracker it is forwarded untouched."""
    if tracker is None:
        record("agent.text", payload, touch_run=False)
        return
    traced = tracker.feed(payload)
    if traced.text_payload is not None:  # a pure-marker delta writes no empty `agent.text` (RW-12)
        record("agent.text", traced.text_payload, touch_run=False)
    for step in traced.step_payloads:
        record("run.workflow", step, touch_run=False)
    if traced.pct is not None:  # `_record` keeps `run.pct` monotonic
        record("run.progress", {"pct": traced.pct, "elapsedMs": 0})


def end_trace(tracker: WorkflowTracker | None, run: Run, result: Any, record: Recorder) -> None:
    """Flush the parser, strip markers from `result.text`, stamp `runs.workflow_adherence`.
    Called on success *and* failure/cancel paths (RW-12)."""
    if tracker is None:
        return
    for event_type, payload in close_trace(tracker, result):
        record(event_type, payload, touch_run=False)
    run.workflow_adherence = tracker.adherence()
