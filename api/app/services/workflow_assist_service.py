"""AI workflow-design assistant: guards, one-or-two-round pipeline, atomic commit (spec §3.8).

The assistant gets **one** structured submission (`submit_result`), never a tool loop:

    guard → snapshot → workspace → round 1 → expand + trial-apply + validate
          → (one repair round, only if the batch is invalid or introduces new errors)
          → CAS commit to the draft → chat + ledger → events

It can only ever write the **draft**: there is no publish / set-default op in its vocabulary
(`workflow_assist_expand` refuses them, the JSON schema does not contain them). A commit uses the
same compare-and-swap discipline as a canvas write (§3.7 "并发纪律"): the UPDATE is conditioned on
the snapshot's `draft_revision` *and* the stored JSON, so an edit made while the model was
thinking is never overwritten — the whole batch is dropped with `assist.conflict` instead (R-W6).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..agent import assist_runner
from ..agent.cost_model import resolve_cost
from ..agent.runner_base import AgentError, AgentEvent, AgentEventType, AgentResult, AgentUsage
from ..config import get_settings
from ..db.base import iso
from ..db.session import SessionLocal
from ..errors import (
    MonthlyBudgetExceeded,
    WorkflowArchived,
    WorkflowAssistBusy,
    WorkflowAssistUnavailable,
    WorkflowBuiltinReadonly,
    WorkflowDocCorrupt,
    WorkflowInvalidOps,
    WorkflowRevisionConflict,
)
from ..logging import get_logger
from ..models import WorkflowTemplate
from ..schemas.workflow import AssistRequest, ValidationReport, WorkflowDoc, doc_from_json, doc_to_json
from ..security import ratelimit
from ..security.principal import Principal
from . import cost_service, workflow_assist_context, workflow_service
from . import workflow_contract as C
from .workflow_assist_expand import Expansion, expand, summarize
from .workflow_assist_registry import get_assist_registry
from .workflow_compiler import validate_with_plan
from .workflow_ops import apply_ops

logger = get_logger("workflow_assist")

TEXT_DELTA_MAX = 400
MAX_ROUNDS = 2
COST_KIND = "workflow_assist"


@dataclass(frozen=True)
class Snapshot:
    """ "What the assistant saw": the draft, its revision and the errors it already had."""

    revision: int
    doc: WorkflowDoc
    report: ValidationReport
    baseline: frozenset[tuple[str, str | None, str]]


@dataclass
class Job:
    assist_id: str
    template_id: str
    user_id: str | None
    message: str
    focus_ids: list[str]
    snapshot: Snapshot
    workspace: Path
    history: list[dict[str, str]] = field(default_factory=list)
    runner: Any = None
    results: list[AgentResult] = field(default_factory=list)


@dataclass
class Verdict:
    """The model's batch after expansion + trial apply + validation."""

    problem: str | None = None
    lines: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    expansion: Expansion | None = None
    report: ValidationReport | None = None
    reply: str = ""
    questions: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    state: str  # done | failed | cancelled
    applied: bool = False
    reason: str | None = None
    reply: str = ""
    questions: list[str] = field(default_factory=list)
    ops: tuple[dict[str, Any], ...] = ()
    revision: int | None = None
    report: ValidationReport | None = None
    conflict_ids: list[str] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────────────────────


def _issue_keys(report: ValidationReport) -> frozenset[tuple[str, str | None, str]]:
    return frozenset(
        (i.code, i.entityId, repr(sorted(i.params.items(), key=lambda kv: kv[0])))
        for i in report.issues
        if i.severity == "error"
    )


def _emit(job: Job, event_type: str, payload: dict[str, Any]) -> None:
    get_assist_registry().emit(job.assist_id, event_type, payload)


def _stats(doc: WorkflowDoc, report: ValidationReport) -> dict[str, int]:
    return {
        "steps": sum(1 for n in doc.nodes if n.kind == "step"),
        "gates": sum(1 for n in doc.nodes if n.kind == "gate"),
        "edges": len(doc.edges),
        "errors": report.errorCount,
        "warnings": report.warningCount,
    }


def _load_template(db: Session, template_id: str) -> tuple[WorkflowTemplate, WorkflowDoc]:
    row = workflow_service.get_row(db, template_id)
    if row.is_builtin:
        raise WorkflowBuiltinReadonly()
    if row.status != "active":
        raise WorkflowArchived()
    try:
        return row, doc_from_json(row.draft_doc_json)
    except ValueError as exc:
        raise WorkflowDocCorrupt(detail={"revision": row.draft_revision}) from exc


def _check_focus(doc: WorkflowDoc, focus_ids: list[str]) -> None:
    known = {n.id for n in doc.nodes}
    unknown = [item for item in focus_ids if item not in known]
    if unknown or len(focus_ids) > C.MAX_FOCUS_NODES:
        raise WorkflowInvalidOps(
            "focusNodeIds 必须是该草稿里已有的节点 id（至多 20 个）",
            detail={"reason": "unknown_focus", "ids": unknown[:5]},
        )


def _guard_start(db: Session, principal: Principal, template_id: str, payload: AssistRequest) -> Snapshot:
    """Every pre-flight check, in the order that keeps cheap/deterministic errors first."""
    row, doc = _load_template(db, template_id)
    _check_focus(doc, payload.focusNodeIds)
    registry = get_assist_registry()
    if registry.active_for_template(template_id):
        raise WorkflowAssistBusy(detail={"assistId": registry.active_for_template(template_id)})
    if payload.baseRevision != row.draft_revision:
        raise WorkflowRevisionConflict(detail={"currentRevision": row.draft_revision})
    if assist_runner.assist_mode() == "unavailable":
        raise WorkflowAssistUnavailable()
    decision = cost_service.can_start(db, COST_KIND)
    if not decision.allowed:
        raise MonthlyBudgetExceeded(detail=decision.detail)
    ratelimit.check("admin.workflow_assist", principal.user_id or principal.ip_key or "service")
    report = validate_with_plan(doc)
    return Snapshot(row.draft_revision, doc, report, _issue_keys(report))


# ── public entry: start ──────────────────────────────────────────────────────


async def start_assist(db: Session, principal: Principal, template_id: str, payload: AssistRequest) -> str:
    """Run the guards, persist the user's message and schedule the task. Returns the assist id.

    Must run on the event loop: the busy check and the registry slot are taken with no `await`
    in between, so two concurrent POSTs cannot both pass."""
    snapshot = _guard_start(db, principal, template_id, payload)
    assist_id = uuid.uuid4().hex
    history = [
        {"role": m.role, "content": m.content} for m in workflow_service.list_chat(db, template_id, principal.user_id)
    ]
    workflow_service.add_chat_message(db, template_id, principal.user_id, "user", payload.message, assist_id=assist_id)
    db.commit()
    workspace = get_settings().assists_dir / assist_id
    job = Job(
        assist_id,
        template_id,
        principal.user_id,
        payload.message,
        list(payload.focusNodeIds),
        snapshot,
        workspace,
        history,
    )
    registry = get_assist_registry()
    registry.create(assist_id, template_id, principal.user_id)
    registry.attach(assist_id, task=asyncio.create_task(_drive(job), name=f"assist-{assist_id}"))
    return assist_id


# ── the pipeline ─────────────────────────────────────────────────────────────


async def _drive(job: Job) -> None:
    session = SessionLocal()
    registry = get_assist_registry()
    outcome = Outcome(state="failed", reason="internal")
    try:
        _emit(job, "assist.start", {"assistId": job.assist_id, "baseRevision": job.snapshot.revision, "round": 1})
        job.runner = await asyncio.to_thread(assist_runner.build_assist_runner)
        registry.attach(job.assist_id, cancel=job.runner.cancel)
        outcome = await _converge(job, session)
    except asyncio.CancelledError:
        outcome = Outcome(state="cancelled", reason="cancelled", reply="已取消，流程保持不变。")
        raise
    except AgentError as exc:
        outcome = Outcome(state="failed", reason=exc.code, reply=f"助手没有运行：{exc.message}")
    except Exception as exc:  # pragma: no cover - defensive: the stream must always terminate
        logger.exception("assist crashed id=%s", job.assist_id)
        outcome = Outcome(state="failed", reason="internal", reply=f"助手遇到内部错误：{type(exc).__name__}")
    finally:
        await _close(job, session, outcome)


async def _close(job: Job, session: Session, outcome: Outcome) -> None:
    """Whatever ended the task: kill the child, book cost, store the chat, emit `assist.end`."""
    try:
        if job.runner is not None:
            try:
                await job.runner.cancel()
            except Exception:  # pragma: no cover - best effort
                logger.warning("assist runner cancel failed id=%s", job.assist_id, exc_info=True)
        session.rollback()
        message_id = _persist(job, session, outcome)
        if outcome.state != "failed" or outcome.reply:
            _emit(
                job,
                "assist.reply",
                {"reply": outcome.reply, "openQuestions": outcome.questions, "messageId": message_id},
            )
        usage = _book_cost(job, session)
        end: dict[str, Any] = {"state": outcome.state, "applied": outcome.applied}
        if outcome.reason:
            end["reason"] = outcome.reason
        if usage:
            end["usage"] = usage
        _emit(job, "assist.end", end)
    finally:
        session.close()
        get_assist_registry().finish(job.assist_id)


async def _converge(job: Job, session: Session) -> Outcome:
    repair: dict[str, Any] | None = None
    for round_no in range(1, MAX_ROUNDS + 1):
        result = await _run_round(job, round_no, repair)
        if get_assist_registry().get(job.assist_id).cancel_requested:  # type: ignore[union-attr]
            return Outcome(state="cancelled", reason="cancelled", reply="已取消，流程保持不变。")
        if not result.ok or not isinstance(result.doc, dict):
            return _failure_outcome(result)
        verdict = _assess(job, result.doc)
        if verdict.problem is None:
            return await asyncio.to_thread(_commit, job, session, verdict)
        if round_no == MAX_ROUNDS:
            return _gave_up(verdict)
        repair = {"reason": verdict.problem, "lines": verdict.lines[:8]}
        _emit(job, "assist.round", {"round": 2, "reason": verdict.problem, "issues": verdict.issues[:12]})
    raise AssertionError("unreachable")  # pragma: no cover


async def _run_round(job: Job, round_no: int, repair: dict[str, Any] | None) -> AgentResult:
    doc, report = job.snapshot.doc, job.snapshot.report
    workflow_assist_context.write_context(
        job.workspace,
        doc=doc,
        report=report,
        history=job.history,
        request={"message": job.message, "focusNodeIds": job.focus_ids, "repair": bool(repair)},
    )
    focus = [{"id": n.id, "kind": n.kind, "title": n.title} for n in doc.nodes if n.id in job.focus_ids]
    prompt_path = assist_runner.render_assist_prompt(
        job.workspace / f"assist-r{round_no}.md",
        job.workspace,
        message=job.message,
        focus=focus,
        stats=_stats(doc, report),
        repair=repair,
    )
    request = assist_runner.build_assist_request(
        workspace=job.workspace, prompt_path=prompt_path, assist_id=job.assist_id, round_no=round_no
    )

    def on_event(event: AgentEvent) -> None:
        if event.type == AgentEventType.text and event.payload.get("delta"):
            _emit(job, "assist.text", {"delta": str(event.payload["delta"])[:TEXT_DELTA_MAX]})

    result = await job.runner.run(request, on_event)
    job.results.append(result)
    return result


def _failure_outcome(result: AgentResult) -> Outcome:
    code = result.error_code or "assist_failed"
    detail = result.error_message or "助手没有产出可用的结构化结果"
    return Outcome(state="failed", reason=code, reply=f"这次没有改动流程：{detail}")


def _gave_up(verdict: Verdict) -> Outcome:
    detail = "；".join(verdict.lines[:3]) or "产出不满足流程规则"
    return Outcome(
        state="done",
        reason=verdict.problem,
        reply=f"我没能给出可以安全应用的修改（{detail}），流程保持不变。可以换个说法再试一次。",
        questions=verdict.questions,
    )


# ── assess: expand + trial apply + validate ──────────────────────────────────


def _clean_text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _assess(job: Job, doc: dict[str, Any]) -> Verdict:
    verdict = Verdict(
        reply=_clean_text(doc.get("reply"), 1200),
        questions=[q[:200] for q in doc.get("openQuestions") or [] if isinstance(q, str)][:4],
    )
    ops = doc.get("ops")
    if not isinstance(ops, list) or not all(isinstance(op, dict) for op in ops):
        return _problem(verdict, "invalid_ops", ["ops 必须是对象数组"], ["bad_patch"])
    expansion = expand(job.snapshot.doc, ops)
    verdict.expansion = expansion
    if not expansion.ok or expansion.doc is None:
        failure = expansion.failure
        assert failure is not None
        return _problem(verdict, "invalid_ops", [failure.line()], [failure.reason])
    verdict.report = validate_with_plan(expansion.doc)
    introduced = [key for key in _issue_keys(verdict.report) if key not in job.snapshot.baseline]
    if introduced:
        lines = [f"新引入错误 {code}（{entity or '整张图'}）" for code, entity, _ in introduced]
        return _problem(verdict, "introduced_errors", lines, [code for code, _, _ in introduced])
    return verdict


def _problem(verdict: Verdict, problem: str, lines: list[str], issues: list[str]) -> Verdict:
    verdict.problem, verdict.lines, verdict.issues = problem, lines, issues
    return verdict


# ── commit (same CAS discipline as a canvas write) ───────────────────────────


def _touched_ids(ops: tuple[dict[str, Any], ...]) -> set[str]:
    touched: set[str] = set()
    for op in ops:
        for key in ("id", "source", "target"):
            if isinstance(op.get(key), str):
                touched.add(op[key])
        edge = op.get("edge")
        if isinstance(edge, dict):
            touched.update(edge.get(k) for k in ("source", "target") if isinstance(edge.get(k), str))
    return touched


def _conflicting_ids(snapshot: WorkflowDoc, current: WorkflowDoc | None, ops: tuple[dict[str, Any], ...]) -> list[str]:
    if current is None:
        return []
    before = {e.id: e.rev for e in (*snapshot.nodes, *snapshot.edges)}
    after = {e.id: e.rev for e in (*current.nodes, *current.edges)}
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    return sorted((changed & _touched_ids(ops)) or changed)[:20]


def _load_current(session: Session, template_id: str) -> WorkflowTemplate | None:
    stmt = select(WorkflowTemplate).where(WorkflowTemplate.id == template_id).execution_options(populate_existing=True)
    return session.execute(stmt).scalar_one_or_none()


def _conflict(job: Job, current: WorkflowDoc | None, ops: tuple[dict[str, Any], ...]) -> Outcome:
    ids = _conflicting_ids(job.snapshot.doc, current, ops)
    _emit(job, "assist.conflict", {"entityIds": ids})
    return Outcome(
        state="done",
        reason="conflict",
        reply="你在 AI 思考期间改动了画布，已放弃这次修改。可以点击【基于最新画布重试】。",
        conflict_ids=ids,
    )


def _commit(job: Job, session: Session, verdict: Verdict) -> Outcome:
    """Blocking (DB): the caller runs it in a worker thread."""
    assert verdict.expansion is not None and verdict.report is not None
    ops = verdict.expansion.ops
    if not ops:
        return Outcome(
            state="done", reason="no_changes", reply=verdict.reply, questions=verdict.questions, report=verdict.report
        )
    session.rollback()
    row = _load_current(session, job.template_id)
    if row is None or row.is_builtin or row.status != "active":
        return Outcome(state="failed", reason="template_unavailable", reply="该流程已不可编辑，未做任何修改。")
    try:
        current = doc_from_json(row.draft_doc_json)
    except ValueError:
        return Outcome(state="failed", reason="workflow_doc_corrupt", reply="草稿已损坏，未做任何修改。")
    if row.draft_revision != job.snapshot.revision:
        return _conflict(job, current, ops)
    applied = apply_ops(current, ops)
    if not applied.ok or applied.doc is None:
        return _conflict(job, current, ops)
    revision = _write_draft(session, job, row, applied.doc, structural=applied.structural)
    if revision is None:
        return _conflict(job, _reload(session, job.template_id), ops)
    report = validate_with_plan(applied.doc)
    _emit(
        job,
        "assist.ops",
        {
            "revision": revision,
            "ops": list(ops),
            "summary": summarize(ops),
            "validation": {"errorCount": report.errorCount, "warningCount": report.warningCount},
        },
    )
    return Outcome(
        state="done",
        applied=True,
        reply=verdict.reply,
        questions=verdict.questions,
        ops=ops,
        revision=revision,
        report=report,
    )


def _reload(session: Session, template_id: str) -> WorkflowDoc | None:
    session.rollback()
    row = _load_current(session, template_id)
    try:
        return doc_from_json(row.draft_doc_json) if row else None
    except ValueError:
        return None


def _write_draft(
    session: Session, job: Job, row: WorkflowTemplate, doc: WorkflowDoc, *, structural: bool
) -> int | None:
    """`UPDATE … WHERE id AND draft_revision=:snapshot AND draft_doc_json=:read`; `None` = lost."""
    values: dict[str, Any] = {"draft_doc_json": doc_to_json(doc), "updated_at": iso(), "updated_by": job.user_id}
    revision = job.snapshot.revision
    if structural:
        revision += 1
        values["draft_revision"] = revision
    stmt = (
        update(WorkflowTemplate)
        .where(
            WorkflowTemplate.id == row.id,
            WorkflowTemplate.draft_revision == job.snapshot.revision,
            WorkflowTemplate.draft_doc_json == row.draft_doc_json,
        )
        .values(**values)
    )
    try:
        won = bool(session.execute(stmt).rowcount)
        if won:
            session.commit()
        else:
            session.rollback()
    except OperationalError:
        session.rollback()
        return None
    return revision if won else None


# ── chat + ledger ────────────────────────────────────────────────────────────


def _persist(job: Job, session: Session, outcome: Outcome) -> int | None:
    applied = {
        "applied": outcome.applied,
        "reason": outcome.reason,
        "baseRevision": job.snapshot.revision,
        "revision": outcome.revision,
        "ops": list(outcome.ops),
        "summary": summarize(outcome.ops) if outcome.ops else None,
        "openQuestions": outcome.questions,
        "conflictEntityIds": outcome.conflict_ids,
    }
    try:
        row = workflow_service.add_chat_message(
            session,
            job.template_id,
            job.user_id,
            "assistant",
            outcome.reply or "（没有回复）",
            applied=applied,
            assist_id=job.assist_id,
        )
        session.commit()
        return int(row.id)
    except Exception:  # pragma: no cover - a chat write failure must not hide `assist.end`
        logger.exception("assist chat persist failed id=%s", job.assist_id)
        session.rollback()
        return None


def _merged_result(results: list[AgentResult]) -> AgentResult:
    """One ledger row per assist (`ux_cost_ledger_ref`), so the rounds are summed."""

    def total(values: list[int | None]) -> int | None:
        known = [v for v in values if v is not None]
        return sum(known) if known else None

    usage = AgentUsage(
        input_tokens=total([r.usage.input_tokens for r in results]),
        output_tokens=total([r.usage.output_tokens for r in results]),
        total_tokens=total([r.usage.total_tokens for r in results]),
    )
    costs = [r.cost_usd for r in results]
    all_known = all(r.cost_known and r.cost_usd is not None for r in results)
    return AgentResult(
        usage=usage,
        cost_usd=sum(c for c in costs if c is not None) if all_known else None,
        cost_known=all_known,
        model=next((r.model for r in reversed(results) if r.model), None),
    )


def _book_cost(job: Job, session: Session) -> dict[str, Any] | None:
    """`kind="workflow_assist"`; **never** touches a user quota (an admin operation)."""
    if not job.results:
        return None
    merged = _merged_result(job.results)
    resolution = resolve_cost(merged)
    try:
        cost_service.record(
            session,
            kind=COST_KIND,
            ref_id=job.assist_id,
            usd=resolution.reported_usd,
            known=resolution.source == "reported",
            usd_est=resolution.estimated_usd,
            source=resolution.source,
            model=merged.model or get_settings().aragon_model or None,
            user_id=job.user_id,
        )
        session.commit()
    except Exception:  # pragma: no cover - the ledger never breaks the assistant
        logger.exception("assist ledger write failed id=%s", job.assist_id)
        session.rollback()
    usage: dict[str, Any] = {"totalTokens": merged.usage.total_tokens}
    if resolution.usd is not None:
        usage["costUsd"] = resolution.usd
    return usage


# ── cancel / events (used by the router) ─────────────────────────────────────


def request_cancel(assist_id: str) -> bool:
    return get_assist_registry().cancel(assist_id)
