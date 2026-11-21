"""Research-workflow templates, drafts, versions and the default pointer — **the single writer**
(spec `report-workflow-graph` §3.7). Routers never touch the three tables; reads are shaped by
`workflow_views`.

**Concurrency discipline (RW-1 / C-13).** Routes are plain `def`, so FastAPI runs them in a thread
pool: two admins really do race. SQLite ignores `SELECT … FOR UPDATE`, so nothing here relies on a
row lock. Correctness rests on three things only:

1. compare-and-swap writes — `UPDATE … WHERE draft_revision = :read_rev` and a `rowcount` check;
2. DB constraints — `UNIQUE(template_id, version)` and the partial unique index `ux_workflow_default`;
3. one short transaction per write (read → compute → CAS write → audit → commit), with no `aragon`
   call and no network IO inside it.

Every public write function therefore **commits itself** (unlike most services, which leave the
commit to the route): the retry-on-`IntegrityError` loop must own the transaction boundary.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso
from ..errors import (
    WorkflowArchived,
    WorkflowBuiltinReadonly,
    WorkflowDocCorrupt,
    WorkflowInvalid,
    WorkflowInvalidOps,
    WorkflowIsDefault,
    WorkflowLimitReached,
    WorkflowNotFound,
    WorkflowNotPublished,
    WorkflowRevisionConflict,
    WorkflowUnchanged,
)
from ..logging import get_logger
from ..models import WorkflowChatMessage, WorkflowTemplate, WorkflowVersion
from ..schemas.workflow import (
    NON_STRUCTURAL_OPS,
    ValidationReport,
    WorkflowDoc,
    doc_from_json,
    doc_to_json,
)
from ..security.principal import Principal
from . import audit_service, settings_service, workflow_views
from . import workflow_contract as C
from .audit_service import AuditTarget
from .workflow_builtin import builtin_doc
from .workflow_compiler import compile_workflow, sample_context, validate_with_plan
from .workflow_ops import apply_ops as apply_ops_pure
from .workflow_ops import create_anchor_only_doc
from .workflow_validator import content_hash

logger = get_logger("workflow")

T = TypeVar("T")

#: `UPDATE … WHERE` retries for a position-only batch that raced another writer.
_MOVE_RETRIES = 3


def new_id() -> str:
    return uuid.uuid4().hex


# ── loading ──────────────────────────────────────────────────────────────────


def get_row(db: Session, template_id: str) -> WorkflowTemplate:
    row = db.get(WorkflowTemplate, template_id)
    if row is None:
        raise WorkflowNotFound()
    return row


def _writable(db: Session, template_id: str) -> WorkflowTemplate:
    """The row, refused if it is built in or archived (drafts of those never change)."""
    row = get_row(db, template_id)
    if row.is_builtin:
        raise WorkflowBuiltinReadonly()
    if row.status != "active":
        raise WorkflowArchived()
    return row


def _parse(row: WorkflowTemplate) -> WorkflowDoc:
    try:
        return doc_from_json(row.draft_doc_json)
    except ValueError as exc:
        raise WorkflowDocCorrupt(detail={"revision": row.draft_revision}) from exc


def _conflict(db: Session, template_id: str, conflicts: list[dict[str, Any]] | None = None) -> WorkflowRevisionConflict:
    """A 409 carrying the **authoritative** draft so the editor can re-align (spec §5.1)."""
    db.rollback()
    row = get_row(db, template_id)
    detail: dict[str, Any] = {"currentRevision": row.draft_revision, "conflicts": conflicts or []}
    try:
        detail["doc"] = doc_from_json(row.draft_doc_json).model_dump(mode="json")
    except ValueError:
        detail["doc"] = None
    return WorkflowRevisionConflict(detail=detail)


def validate_doc(doc: WorkflowDoc) -> ValidationReport:
    return validate_with_plan(doc)


# ── reading façade (what the routes call) ────────────────────────────────────


def detail(db: Session, template_id: str):  # noqa: ANN201
    row = get_row(db, template_id)
    doc = _parse(row)
    return workflow_views.detail(db, row, validate_doc(doc), doc)


def validate(db: Session, template_id: str, doc: WorkflowDoc | None) -> ValidationReport:
    row = get_row(db, template_id)
    return validate_doc(doc if doc is not None else _parse(row))


def preview(db: Session, template_id: str) -> dict[str, Any]:
    row = get_row(db, template_id)
    doc = _parse(row)
    latest = db.get(WorkflowVersion, row.current_version_id) if row.current_version_id else None
    return workflow_views.preview(doc, row.name, latest.version if latest else None, validate_doc(doc))


def get_version(db: Session, template_id: str, version: int) -> WorkflowVersion:
    get_row(db, template_id)
    found = db.execute(
        select(WorkflowVersion).where(WorkflowVersion.template_id == template_id, WorkflowVersion.version == version)
    ).scalar_one_or_none()
    if found is None:
        raise WorkflowNotFound("该版本不存在")
    return found


# ── templates ────────────────────────────────────────────────────────────────


def _active_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(WorkflowTemplate).where(WorkflowTemplate.status == "active")
        ).scalar_one()
    )


def _source_doc(db: Session, source: str | dict[str, str]) -> WorkflowDoc:
    if source == "blank":
        return create_anchor_only_doc()
    if source == "builtin":
        return builtin_doc()
    return _parse(get_row(db, str(source["templateId"])))  # type: ignore[index]


def create(db: Session, principal: Principal, name: str, description: str, source: str | dict[str, str]):  # noqa: ANN201
    """`from = blank | builtin | {templateId}`. Node ids are kept so a clone stays comparable."""
    if _active_count(db) >= C.MAX_ACTIVE_TEMPLATES:
        raise WorkflowLimitReached(f"活动流程已达上限（{C.MAX_ACTIVE_TEMPLATES}）")
    doc = _source_doc(db, source)
    now = iso()
    row = WorkflowTemplate(
        id=new_id(),
        name=name.strip(),
        description=description,
        is_builtin=0,
        is_default=0,
        status="active",
        draft_doc_json=doc_to_json(doc),
        draft_revision=1,
        created_by=principal.user_id,
        created_at=now,
        updated_by=principal.user_id,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    origin = source if isinstance(source, str) else source["templateId"]
    audit_service.record(db, principal, "workflow.create", AuditTarget("workflow", row.id, row.name), {"from": origin})
    db.commit()
    return detail(db, row.id)


def patch_meta(db: Session, principal: Principal, template_id: str, name: str | None, description: str | None):  # noqa: ANN201
    row = get_row(db, template_id)
    if row.is_builtin:
        raise WorkflowBuiltinReadonly()
    if name is not None:
        row.name = name.strip()
    if description is not None:
        row.description = description
    row.updated_at = iso()
    row.updated_by = principal.user_id
    db.commit()
    return workflow_views.summarize(db, [row])[0]


def archive(db: Session, principal: Principal, template_id: str) -> None:
    row = get_row(db, template_id)
    if row.is_builtin:
        raise WorkflowBuiltinReadonly()
    if row.is_default:
        raise WorkflowIsDefault()
    if row.status == "active":
        row.status = "archived"
        row.updated_at = iso()
        row.updated_by = principal.user_id
        audit_service.record(db, principal, "workflow.archive", AuditTarget("workflow", row.id, row.name))
    db.commit()


# ── draft writes (CAS) ───────────────────────────────────────────────────────


def _cas_revision(db: Session, row: WorkflowTemplate, doc: WorkflowDoc, principal: Principal) -> bool:
    """Structural write: `WHERE draft_revision = :read_rev`; `False` = lost the race."""
    result = db.execute(
        update(WorkflowTemplate)
        .where(WorkflowTemplate.id == row.id, WorkflowTemplate.draft_revision == row.draft_revision)
        .values(
            draft_doc_json=doc_to_json(doc),
            draft_revision=row.draft_revision + 1,
            updated_at=iso(),
            updated_by=principal.user_id,
        )
    )
    return bool(result.rowcount)


def _cas_content(db: Session, row: WorkflowTemplate, doc: WorkflowDoc) -> bool:
    """Position/viewport-only write. It does not raise `draftRevision`, so the CAS compares the
    stored JSON itself: a stale batch can never overwrite someone's structural edit."""
    result = db.execute(
        update(WorkflowTemplate)
        .where(WorkflowTemplate.id == row.id, WorkflowTemplate.draft_doc_json == row.draft_doc_json)
        .values(draft_doc_json=doc_to_json(doc))
    )
    return bool(result.rowcount)


def _guard_write(db: Session, fn: Callable[[], T], template_id: str) -> T:
    """A busy/locked database during a CAS is a lost race, not a 500."""
    try:
        return fn()
    except OperationalError as exc:
        logger.warning("workflow write lost a lock race: %s", exc)
        raise _conflict(db, template_id) from exc


def _raise_op_failure(db: Session, template_id: str, result: Any) -> None:
    if result.conflicts:
        raise _conflict(db, template_id, [c.model_dump() for c in result.conflicts])
    failure = result.failure
    raise WorkflowInvalidOps(
        f"流程修改不合法：{failure.message}" if failure.message else None,
        detail={"opIndex": failure.opIndex, "reason": failure.reason, "entityId": failure.entityId},
    )


def _only_non_structural(ops: list[dict[str, Any]]) -> bool:
    return bool(ops) and all(op.get("op") in NON_STRUCTURAL_OPS for op in ops)


def _try_apply(
    db: Session, principal: Principal, template_id: str, base_revision: int, ops: list[dict[str, Any]], relaxed: bool
) -> dict[str, Any] | None:
    """One read -> compute -> CAS attempt. `None` = lost the CAS race (caller may retry)."""
    row = _writable(db, template_id)
    if not relaxed and base_revision != row.draft_revision:
        raise _conflict(db, template_id)
    read_revision = row.draft_revision
    before = _parse(row)
    result = apply_ops_pure(before, ops)
    if not result.ok:
        _raise_op_failure(db, template_id, result)
    doc = result.doc
    assert doc is not None
    if doc == before:
        return {"revision": read_revision, "validation": validate_doc(doc), "changed": False}
    cas = (
        (lambda: _cas_revision(db, row, doc, principal)) if result.structural else (lambda: _cas_content(db, row, doc))
    )
    if not _guard_write(db, cas, template_id):
        db.rollback()
        return None
    db.commit()
    revision = read_revision + (1 if result.structural else 0)
    return {"revision": revision, "validation": validate_doc(doc), "changed": True}


def apply_ops(
    db: Session, principal: Principal, template_id: str, base_revision: int, ops: list[dict[str, Any]]
) -> dict[str, Any]:
    """Atomically apply an op batch to the draft. Returns `{revision, validation, changed}`.

    A batch of only `move_node` / `set_viewport` skips the `baseRevision` check (dragging must not
    fail because someone else edited another node), never raises `draftRevision`, and is retried
    on a lost race (it is re-applied to the fresh draft); a structural batch loses with a 409.
    """
    relaxed = _only_non_structural(ops)
    for _attempt in range(_MOVE_RETRIES if relaxed else 1):
        out = _try_apply(db, principal, template_id, base_revision, ops, relaxed)
        if out is not None:
            return out
    raise _conflict(db, template_id)


def _check_replacement(doc: WorkflowDoc) -> None:
    """`PUT …/draft`: shape is pydantic's job; the limits and id uniqueness are checked here."""
    ids = [node.id for node in doc.nodes]
    edge_ids = [edge.id for edge in doc.edges]
    if len(ids) != len(set(ids)) or len(edge_ids) != len(set(edge_ids)):
        raise WorkflowInvalidOps("流程文档含重复 id", detail={"opIndex": 0, "reason": "duplicate_id"})
    too_big = (
        len(doc.nodes) > C.MAX_NODES
        or len(doc.edges) > C.MAX_EDGES
        or len(doc_to_json(doc).encode("utf-8")) > C.MAX_DOC_BYTES
    )
    if too_big:
        raise WorkflowInvalidOps("流程文档超出限额", detail={"opIndex": 0, "reason": "limit_exceeded"})


def replace_draft(
    db: Session, principal: Principal, template_id: str, base_revision: int, doc: WorkflowDoc
) -> dict[str, Any]:
    """Reset a (possibly corrupt) draft wholesale. Not an import feature (§1.5): same limits, same
    CAS, no audit. A corrupt stored draft is exactly why this must not parse the old document."""
    row = _writable(db, template_id)
    if base_revision != row.draft_revision:
        raise _conflict(db, template_id)
    _check_replacement(doc)
    read_revision = row.draft_revision
    if not _guard_write(db, lambda: _cas_revision(db, row, doc, principal), template_id):
        raise _conflict(db, template_id)
    db.commit()
    return {"revision": read_revision + 1, "validation": validate_doc(doc), "changed": True}


# ── publish / rollback ───────────────────────────────────────────────────────


def _latest_version(db: Session, template_id: str) -> WorkflowVersion | None:
    return db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.template_id == template_id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def _plan_bytes(doc: WorkflowDoc, name: str, version: int) -> int:
    plan = compile_workflow(doc, sample_context(), template_name=name, version=version)
    return len(plan.plan_md.encode("utf-8"))


def _insert_version(
    db: Session, row: WorkflowTemplate, doc: WorkflowDoc, note: str, principal: Principal
) -> WorkflowVersion:
    """Append the next immutable version (raises `WorkflowUnchanged` / `WorkflowLimitReached`).
    `UNIQUE(template_id, version)` is the race backstop: a loser raises `IntegrityError` on flush."""
    latest = _latest_version(db, row.id)
    digest = content_hash(doc)
    if latest is not None and latest.doc_sha1 == digest:
        raise WorkflowUnchanged()
    count = int(
        db.execute(
            select(func.count()).select_from(WorkflowVersion).where(WorkflowVersion.template_id == row.id)
        ).scalar_one()
    )
    if count >= C.MAX_VERSIONS_PER_TEMPLATE:
        raise WorkflowLimitReached(f"版本数已达上限（{C.MAX_VERSIONS_PER_TEMPLATE}）")
    number = (latest.version if latest else 0) + 1
    version = WorkflowVersion(
        id=new_id(),
        template_id=row.id,
        version=number,
        doc_json=doc_to_json(doc, with_viewport=False),
        doc_sha1=digest,
        change_note=note[: C.CHANGE_NOTE_MAX],
        step_count=workflow_views.count_steps(doc),
        plan_bytes=_plan_bytes(doc, row.name, number),
        published_by=principal.user_id,
        published_at=iso(),
    )
    db.add(version)
    db.flush()
    return version


def _point_to_version(db: Session, row: WorkflowTemplate, version: WorkflowVersion, expect_revision: int) -> bool:
    """CAS the template's `current_version_id` on the same `draft_revision` the publish read."""
    result = db.execute(
        update(WorkflowTemplate)
        .where(WorkflowTemplate.id == row.id, WorkflowTemplate.draft_revision == expect_revision)
        .values(current_version_id=version.id, updated_at=iso(), updated_by=version.published_by)
    )
    return bool(result.rowcount)


def _require_publishable(doc: WorkflowDoc) -> None:
    report = validate_doc(doc)
    if not report.publishable:
        raise WorkflowInvalid(detail={"report": report.model_dump(mode="json")})


def _retry_once(db: Session, template_id: str, fn: Callable[[], T]) -> T:
    """Run a write transaction; on an integrity/lock race roll back and re-read **once**, then 409."""
    for attempt in (1, 2):
        try:
            return fn()
        except (IntegrityError, OperationalError):
            db.rollback()
            if attempt == 2:
                raise _conflict(db, template_id) from None
    raise AssertionError("unreachable")  # pragma: no cover


def publish(db: Session, principal: Principal, template_id: str, base_revision: int, note: str, set_default_flag: bool):  # noqa: ANN201
    """Draft → new immutable version (spec §3.7 `publish`, steps ①–⑦)."""

    def attempt():  # noqa: ANN202
        row = _writable(db, template_id)
        if base_revision != row.draft_revision:
            raise _conflict(db, template_id)
        doc = _parse(row)
        _require_publishable(doc)
        version = _insert_version(db, row, doc, note, principal)
        if not _point_to_version(db, row, version, base_revision):
            raise _conflict(db, template_id)
        previous = _swap_default(db, row) if set_default_flag and not row.is_default else None
        _audit_publish(db, principal, row, version, "workflow.publish", note, previous)
        db.commit()
        return version

    version = _retry_once(db, template_id, attempt)
    row = get_row(db, template_id)
    summary = workflow_views.version_summaries(db, [version])[0].model_dump()
    summary["isDefault"] = bool(row.is_default)
    return summary


def _audit_publish(
    db: Session,
    principal: Principal,
    row: WorkflowTemplate,
    version: WorkflowVersion,
    action: str,
    note: str,
    previous_default: str | None,
) -> None:
    detail_ = {"version": version.version, "hash": version.doc_sha1, "note": note, "previousDefault": previous_default}
    audit_service.record(db, principal, action, AuditTarget("workflow", row.id, row.name), detail_)


def rollback(db: Session, principal: Principal, template_id: str, version_number: int, note: str):  # noqa: ANN201
    """Publish an old version's document as a **new** version and reset the draft to it."""
    source = get_version(db, template_id, version_number)
    text = note.strip() or f"回滚到 v{version_number}"

    def attempt():  # noqa: ANN202
        row = _writable(db, template_id)
        doc = doc_from_json(source.doc_json)
        _require_publishable(doc)
        version = _insert_version(db, row, doc, text, principal)
        if not _reset_draft_and_point(db, row, doc, version, principal):
            raise _conflict(db, template_id)
        _audit_publish(db, principal, row, version, "workflow.rollback", text, None)
        db.commit()
        return version

    version = _retry_once(db, template_id, attempt)
    return workflow_views.version_summaries(db, [version])[0]


def _reset_draft_and_point(
    db: Session, row: WorkflowTemplate, doc: WorkflowDoc, version: WorkflowVersion, principal: Principal
) -> bool:
    result = db.execute(
        update(WorkflowTemplate)
        .where(WorkflowTemplate.id == row.id, WorkflowTemplate.draft_revision == row.draft_revision)
        .values(
            draft_doc_json=doc_to_json(doc),
            draft_revision=row.draft_revision + 1,
            current_version_id=version.id,
            updated_at=iso(),
            updated_by=principal.user_id,
        )
    )
    return bool(result.rowcount)


def restore_to_draft(db: Session, principal: Principal, template_id: str, version_number: int) -> dict[str, Any]:
    """Copy a version's document back into the draft (no publish); `draftRevision` +1."""
    source = get_version(db, template_id, version_number)
    row = _writable(db, template_id)
    doc = doc_from_json(source.doc_json)
    read_revision = row.draft_revision
    if not _guard_write(db, lambda: _cas_revision(db, row, doc, principal), template_id):
        raise _conflict(db, template_id)
    db.commit()
    return {"revision": read_revision + 1}


# ── default pointer ──────────────────────────────────────────────────────────


def _swap_default(db: Session, row: WorkflowTemplate) -> str | None:
    """Clear the old default and set `row` — same transaction; `ux_workflow_default` is the judge."""
    previous = db.execute(select(WorkflowTemplate.id).where(WorkflowTemplate.is_default == 1)).scalar_one_or_none()
    db.execute(update(WorkflowTemplate).where(WorkflowTemplate.is_default == 1).values(is_default=0))
    db.execute(update(WorkflowTemplate).where(WorkflowTemplate.id == row.id).values(is_default=1))
    db.flush()
    db.refresh(row)
    return previous


def set_default(db: Session, principal: Principal, template_id: str) -> dict[str, Any]:
    """Make an active, published template the site default. In-flight runs keep their frozen
    version (the binding happens in `RunManager.start`)."""

    def attempt() -> tuple[WorkflowTemplate, str | None]:
        row = get_row(db, template_id)
        if row.status != "active" or not row.current_version_id:
            raise WorkflowNotPublished()
        previous = _swap_default(db, row)
        version = db.get(WorkflowVersion, row.current_version_id)
        audit_service.record(
            db,
            principal,
            "workflow.default.set",
            AuditTarget("workflow", row.id, row.name),
            {"version": version.version if version else None, "previousDefault": previous},
        )
        db.commit()
        return row, previous

    row, _previous = _retry_once(db, template_id, attempt)
    version = db.get(WorkflowVersion, row.current_version_id)
    return {"defaultTemplateId": row.id, "version": version.version if version else None}


def default_summary(db: Session) -> dict[str, Any]:
    row = db.execute(select(WorkflowTemplate).where(WorkflowTemplate.is_default == 1)).scalar_one_or_none()
    enabled = settings_service.workflow_enabled(db)
    version = db.get(WorkflowVersion, row.current_version_id) if row and row.current_version_id else None
    return {
        "templateId": row.id if row else None,
        "name": row.name if row else None,
        "version": version.version if version else None,
        "hash": version.doc_sha1 if version else None,
        "enabled": enabled,
    }


def set_enabled(db: Session, principal: Principal, enabled: bool) -> bool:
    before = settings_service.workflow_enabled(db)
    settings_service.set_str(db, settings_service.KEY_WORKFLOW_ENABLED, "1" if enabled else "0")
    if before != enabled:
        audit_service.record(
            db,
            principal,
            "settings.update",
            AuditTarget("settings", None, None),
            {"key": "workflow.enabled", "changes": {"workflow.enabled": {"from": before, "to": enabled}}},
        )
    db.commit()
    return enabled


# ── chat (the assistant writes through these) ────────────────────────────────


def list_chat(db: Session, template_id: str, user_id: str | None) -> list[WorkflowChatMessage]:
    get_row(db, template_id)
    rows = (
        db.execute(
            select(WorkflowChatMessage)
            .where(WorkflowChatMessage.template_id == template_id, WorkflowChatMessage.user_id == (user_id or ""))
            .order_by(WorkflowChatMessage.id.desc())
            .limit(C.CHAT_KEPT)
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def add_chat_message(
    db: Session,
    template_id: str,
    user_id: str | None,
    role: str,
    content: str,
    *,
    applied: dict[str, Any] | None = None,
    assist_id: str | None = None,
) -> WorkflowChatMessage:
    """Append a message and trim the (template, user) history to the latest `CHAT_KEPT`. Flushes;
    the caller commits."""
    owner = user_id or ""
    row = WorkflowChatMessage(
        template_id=template_id,
        user_id=owner,
        role=role,
        content=content,
        applied_json=json.dumps(applied, ensure_ascii=False, separators=(",", ":")) if applied else None,
        assist_id=assist_id,
        created_at=iso(),
    )
    db.add(row)
    db.flush()
    keep = (
        select(WorkflowChatMessage.id)
        .where(WorkflowChatMessage.template_id == template_id, WorkflowChatMessage.user_id == owner)
        .order_by(WorkflowChatMessage.id.desc())
        .limit(C.CHAT_KEPT)
    )
    db.execute(
        delete(WorkflowChatMessage).where(
            WorkflowChatMessage.template_id == template_id,
            WorkflowChatMessage.user_id == owner,
            WorkflowChatMessage.id.not_in(keep),
        )
    )
    return row


def clear_chat(db: Session, template_id: str, user_id: str | None) -> None:
    get_row(db, template_id)
    db.execute(
        delete(WorkflowChatMessage).where(
            WorkflowChatMessage.template_id == template_id, WorkflowChatMessage.user_id == (user_id or "")
        )
    )
    db.commit()


# ── built-in template ────────────────────────────────────────────────────────


def _has_default(db: Session) -> bool:
    return db.execute(select(WorkflowTemplate.id).where(WorkflowTemplate.is_default == 1)).first() is not None


def _seed_builtin(db: Session) -> None:
    doc = builtin_doc()
    now = iso()
    system = Principal(kind="system", user_id=None, username=None, role="system", permissions=frozenset())
    row = WorkflowTemplate(
        id=C.BUILTIN_TEMPLATE_ID,
        name=C.BUILTIN_NAME,
        description="复现标准五段式研究流程的内置模板（只读，可复制为副本）。",
        is_builtin=1,
        is_default=0,
        status="active",
        draft_doc_json=doc_to_json(doc),
        draft_revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    version = _insert_version(db, row, doc, "内置", system)
    row.current_version_id = version.id
    if not _has_default(db):  # RW-11: never collide with an existing default
        row.is_default = 1


def _refresh_builtin(db: Session, row: WorkflowTemplate) -> None:
    """The code-owned document changed -> append a **new** version, never edit an old one."""
    doc = builtin_doc()
    latest = _latest_version(db, row.id)
    if latest is not None and latest.doc_sha1 == content_hash(doc):
        return
    system = Principal(kind="system", user_id=None, username=None, role="system", permissions=frozenset())
    version = _insert_version(db, row, doc, C.BUILTIN_VERSION_NOTE, system)
    row.current_version_id = version.id
    row.draft_doc_json = doc_to_json(doc)
    row.draft_revision += 1
    row.updated_at = iso()


def ensure_builtin(db: Session) -> None:
    """Idempotent startup hook (after migrations). Never raises into `lifespan`: a failure is
    logged and the run path falls back to the code-owned document anyway (§3.6.1)."""
    try:
        row = db.get(WorkflowTemplate, C.BUILTIN_TEMPLATE_ID)
        if row is None:
            _seed_builtin(db)
        else:
            _refresh_builtin(db, row)
        db.commit()
    except IntegrityError:  # another process seeded it first
        db.rollback()
    except Exception:  # pragma: no cover - startup must not die on the optional feature
        logger.exception("ensure_builtin 失败（运行时将回退到代码内置流程）")
        db.rollback()


def cleanup_assist_workspaces() -> int:
    """Remove assistant workspaces older than the run retention window (best effort)."""
    import shutil
    from datetime import UTC, datetime, timedelta

    settings = get_settings()
    root = settings.assists_dir
    if not root.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=settings.run_retention_days)
    removed = 0
    for child in root.iterdir():
        try:
            stale = child.is_dir() and datetime.fromtimestamp(child.stat().st_mtime, tz=UTC) < cutoff
        except OSError:  # pragma: no cover
            continue
        if stale:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
