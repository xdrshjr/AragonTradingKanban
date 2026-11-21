"""Read side of the research-workflow admin API (spec `report-workflow-graph` §5.1).

Everything here is a **pure read**: it shapes rows into the wire payloads (`TemplateSummary`,
`VersionSummary`, usage, version diff, plan preview). All writes live in `workflow_service`
(the single writer, spec §3.7); the split only keeps both files under the size threshold.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import iso, utcnow
from ..models import Run, RunEvent, User, WorkflowTemplate, WorkflowVersion
from ..schemas.workflow import (
    DraftState,
    TemplateDetail,
    TemplateSummary,
    ValidationReport,
    VersionSummary,
    WorkflowDoc,
    doc_from_json,
)
from . import workflow_contract as C
from .workflow_validator import content_hash

#: Worst-case extra bytes the `base_digest` of a follow-up run adds to `prompt.md` (spec §3.5.4).
BASE_DIGEST_RESERVE = 2048

#: Terminal run statuses that count as a failed generation in the usage table.
_FAILED_STATUSES = ("failed", "timeout", "budget_exceeded")


def parse_draft(row: WorkflowTemplate) -> WorkflowDoc:
    """The row's draft as a model; raises whatever pydantic/json raise (callers map it)."""
    return doc_from_json(row.draft_doc_json)


def count_steps(doc: WorkflowDoc) -> int:
    return sum(1 for node in doc.nodes if node.kind == "step")


def _user_names(db: Session, ids: Iterable[str | None]) -> dict[str, str]:
    wanted = {item for item in ids if item}
    if not wanted:
        return {}
    rows = db.execute(select(User.id, User.username, User.display_name).where(User.id.in_(wanted))).all()
    return {row.id: (row.display_name or row.username) for row in rows}


def _latest_versions(db: Session, rows: Sequence[WorkflowTemplate]) -> dict[str, WorkflowVersion]:
    ids = {row.current_version_id for row in rows if row.current_version_id}
    if not ids:
        return {}
    versions = db.execute(select(WorkflowVersion).where(WorkflowVersion.id.in_(ids))).scalars().all()
    return {version.id: version for version in versions}


def _runs_since(db: Session, template_ids: Iterable[str], days: int) -> dict[str, int]:
    cutoff = iso(utcnow() - timedelta(days=days))
    rows = db.execute(
        select(WorkflowVersion.template_id, func.count(Run.id))
        .join(Run, Run.workflow_version_id == WorkflowVersion.id)
        .where(Run.started_at >= cutoff, WorkflowVersion.template_id.in_(list(template_ids)))
        .group_by(WorkflowVersion.template_id)
    ).all()
    return {template_id: int(count) for template_id, count in rows}


def _draft_facts(row: WorkflowTemplate, latest: WorkflowVersion | None) -> tuple[int, bool]:
    """`(stepCount, dirty)`; a corrupt draft reports `(0, False)` rather than failing a whole list."""
    try:
        doc = parse_draft(row)
    except ValueError:
        return 0, False
    return count_steps(doc), latest is None or content_hash(doc) != latest.doc_sha1


def summarize(db: Session, rows: Sequence[WorkflowTemplate]) -> list[TemplateSummary]:
    """`TemplateSummary` for each row with three batched lookups (no N+1)."""
    latest = _latest_versions(db, rows)
    names = _user_names(db, (row.updated_by for row in rows))
    runs = _runs_since(db, (row.id for row in rows), 30)
    out: list[TemplateSummary] = []
    for row in rows:
        version = latest.get(row.current_version_id or "")
        step_count, dirty = _draft_facts(row, version)
        out.append(
            TemplateSummary(
                id=row.id,
                name=row.name,
                description=row.description or "",
                isDefault=bool(row.is_default),
                isBuiltin=bool(row.is_builtin),
                status=row.status,  # type: ignore[arg-type]
                latestVersion=version.version if version else None,
                stepCount=step_count,
                dirty=dirty,
                updatedAt=row.updated_at,
                updatedByName=names.get(row.updated_by or ""),
                runs30d=runs.get(row.id, 0),
            )
        )
    return out


def list_summaries(db: Session, *, status: str = "active") -> list[TemplateSummary]:
    rows = (
        db.execute(
            select(WorkflowTemplate)
            .where(WorkflowTemplate.status == status)
            .order_by(WorkflowTemplate.is_default.desc(), WorkflowTemplate.updated_at.desc())
        )
        .scalars()
        .all()
    )
    return summarize(db, rows)


def version_summaries(db: Session, versions: Sequence[WorkflowVersion]) -> list[VersionSummary]:
    names = _user_names(db, (version.published_by for version in versions))
    return [
        VersionSummary(
            id=version.id,
            version=version.version,
            hash=version.doc_sha1,
            changeNote=version.change_note or "",
            stepCount=version.step_count,
            planBytes=version.plan_bytes,
            publishedAt=version.published_at,
            publishedByName=names.get(version.published_by or ""),
        )
        for version in versions
    ]


def list_versions(
    db: Session, template_id: str, *, limit: int = 50, before: int | None = None
) -> tuple[list[VersionSummary], int | None]:
    """Newest first; `next` is the cursor (a version number) for the following page, or `None`."""
    query = select(WorkflowVersion).where(WorkflowVersion.template_id == template_id)
    if before is not None:
        query = query.where(WorkflowVersion.version < before)
    rows = db.execute(query.order_by(WorkflowVersion.version.desc()).limit(limit + 1)).scalars().all()
    page = rows[:limit]
    next_cursor = page[-1].version if len(rows) > limit and page else None
    return version_summaries(db, page), next_cursor


def detail(db: Session, row: WorkflowTemplate, validation: ValidationReport, doc: WorkflowDoc) -> TemplateDetail:
    """`TemplateDetail` for a template whose draft already parsed and validated."""
    meta = summarize(db, [row])[0]
    versions, _next = list_versions(db, row.id, limit=C.MAX_VERSIONS_PER_TEMPLATE)
    return TemplateDetail(
        meta=meta,
        draft=DraftState(doc=doc, revision=row.draft_revision, validation=validation, dirty=meta.dirty),
        versions=versions,
    )


# ── version diff ─────────────────────────────────────────────────────────────


def _node_fingerprint(node: Any) -> tuple[Any, ...]:
    return (node.kind, node.title, repr(sorted(node.config.items())))


def diff_docs(old: WorkflowDoc | None, new: WorkflowDoc) -> dict[str, list[dict[str, str]]]:
    """Structural diff `old -> new` (positions and viewport are not content, spec §3.7).

    Nodes are matched by id; edges by `(source, target)` (an edge's identity is its endpoints).
    """
    old_nodes = {node.id: node for node in (old.nodes if old else [])}
    new_nodes = {node.id: node for node in new.nodes}
    old_edges = {(edge.source, edge.target): edge for edge in (old.edges if old else [])}
    new_edges = {(edge.source, edge.target): edge for edge in new.edges}

    def tag(node: Any) -> dict[str, str]:
        return {"id": node.id, "title": node.title}

    def edge_tag(edge: Any) -> dict[str, str]:
        return {"id": edge.id, "source": edge.source, "target": edge.target}

    return {
        "addedNodes": [tag(n) for key, n in new_nodes.items() if key not in old_nodes and n.kind != "note"],
        "removedNodes": [tag(n) for key, n in old_nodes.items() if key not in new_nodes and n.kind != "note"],
        "changedNodes": [
            tag(n)
            for key, n in new_nodes.items()
            if key in old_nodes and n.kind != "note" and _node_fingerprint(n) != _node_fingerprint(old_nodes[key])
        ],
        "addedEdges": [edge_tag(e) for key, e in new_edges.items() if key not in old_edges],
        "removedEdges": [edge_tag(e) for key, e in old_edges.items() if key not in new_edges],
    }


def version_with_diff(db: Session, version: WorkflowVersion) -> dict[str, Any]:
    """`{summary, doc, diffVsPrevious}` for `GET …/versions/{version}`."""
    previous = db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.template_id == version.template_id, WorkflowVersion.version < version.version)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    doc = doc_from_json(version.doc_json)
    before = doc_from_json(previous.doc_json) if previous else None
    return {
        "summary": version_summaries(db, [version])[0],
        "doc": doc,
        "diffVsPrevious": diff_docs(before, doc),
    }


# ── usage ────────────────────────────────────────────────────────────────────


def usage(db: Session, template_id: str, days: int) -> dict[str, Any]:
    """Per-version run statistics over the last `days` days, aggregated from `runs`.

    `fallbacks` counts the runs that logged `workflow_fallback` (`run.log` event, written when the
    frozen version could not be compiled and the built-in flow was used instead); `runs` has no
    column for it, so it is a subquery over `run_events` (`LIKE '%workflow_fallback%'`, robust to
    `json.dumps` spacing).
    """
    cutoff = iso(utcnow() - timedelta(days=days))
    versions = (
        db.execute(
            select(WorkflowVersion).where(WorkflowVersion.template_id == template_id).order_by(WorkflowVersion.version)
        )
        .scalars()
        .all()
    )
    by_id = {version.id: version for version in versions}
    stats = {version.id: _blank_stats(version.version) for version in versions}
    if by_id:
        rows = db.execute(
            select(Run.workflow_version_id, Run.status, Run.workflow_adherence, func.count(Run.id))
            .where(Run.workflow_version_id.in_(list(by_id)), Run.started_at >= cutoff)
            .group_by(Run.workflow_version_id, Run.status, Run.workflow_adherence)
        ).all()
        for version_id, status, adherence, count in rows:
            _add_stat(stats[version_id], status, adherence, int(count))
        for version_id, count in _fallback_counts(db, list(by_id), cutoff).items():
            stats[version_id]["fallbacks"] = count
    return {"byVersion": [stats[version.id] for version in versions]}


def _fallback_counts(db: Session, version_ids: list[str], cutoff: str) -> dict[str, int]:
    fell_back = (
        select(RunEvent.run_id)
        .where(RunEvent.type == "run.log", RunEvent.payload_json.like("%workflow_fallback%"))
        .distinct()
    )
    rows = db.execute(
        select(Run.workflow_version_id, func.count(Run.id))
        .where(Run.workflow_version_id.in_(version_ids), Run.started_at >= cutoff, Run.id.in_(fell_back))
        .group_by(Run.workflow_version_id)
    ).all()
    return {version_id: int(count) for version_id, count in rows}


def _blank_stats(version: int) -> dict[str, Any]:
    return {
        "version": version,
        "runs": 0,
        "succeeded": 0,
        "failed": 0,
        "adherence": {"full": 0, "partial": 0, "none": 0},
        "fallbacks": 0,
    }


def _add_stat(stat: dict[str, Any], status: str, adherence: str | None, count: int) -> None:
    stat["runs"] += count
    if status == "succeeded":
        stat["succeeded"] += count
    elif status in _FAILED_STATUSES:
        stat["failed"] += count
    if adherence in stat["adherence"]:
        stat["adherence"][adherence] += count


# ── preview ──────────────────────────────────────────────────────────────────


def _prompt_baseline_bytes() -> int:
    """Bytes of the prompt files that share the 32 KiB budget with the inline index (an estimate:
    the task template is measured unrendered)."""
    settings = get_settings()
    schema = settings.prompts_dir.parent / "schemas" / "report_doc.schema.json"
    names = [settings.prompts_dir / "research_system.md", settings.prompts_dir / "research_task.md.j2", schema]
    return sum(path.stat().st_size for path in names if path.exists())


def preview(doc: WorkflowDoc, template_name: str, version: int | None, validation: ValidationReport) -> dict[str, Any]:
    """Compile `doc` against the sample instrument. A graph that cannot compile yields
    `compilable=false` and an empty plan — never an HTTP error — so the editor's preview tab can
    show the problems instead of a toast."""
    from .workflow_compiler import WorkflowCompileError, compile_workflow, sample_context

    limit = get_settings().aragon_max_prompt_bytes
    base = _prompt_baseline_bytes() + BASE_DIGEST_RESERVE
    try:
        plan = compile_workflow(doc, sample_context(), template_name=template_name, version=version)
    except WorkflowCompileError:
        return _preview_payload(None, base, limit, validation)
    return _preview_payload(plan, base, limit, validation)


def _preview_payload(plan: Any, base: int, limit: int, validation: ValidationReport) -> dict[str, Any]:
    if plan is None:
        return {
            "compilable": False,
            "planMd": "",
            "inlineIndex": "",
            "levels": [],
            "bytes": 0,
            "sha1": "",
            "budget": {"promptTotal": base, "limit": limit},
            "validation": validation,
        }
    index_bytes = len(plan.inline_index.encode("utf-8"))
    return {
        "compilable": True,
        "planMd": plan.plan_md,
        "inlineIndex": plan.inline_index,
        "levels": [list(level) for level in plan.levels],
        "bytes": len(plan.plan_md.encode("utf-8")),
        "sha1": plan.sha1,
        "budget": {"promptTotal": base + index_bytes, "limit": limit},
        "validation": validation,
    }
