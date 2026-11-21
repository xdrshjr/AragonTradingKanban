"""`/admin/workflows` — research-workflow templates, drafts, versions and the default pointer
(spec `report-workflow-graph` §5.1).

Thin by design: parse, call `workflow_service` (which owns the transaction and the CAS retry),
shape the response. The permission (`admin.workflow.manage`) is the policy table's job — a `user`
gets 403, a `guest` 401, the narrowed `service` principal 403. Every `/admin` response carries
`Cache-Control: private, no-store` (set centrally in `main.py`).

**Route order matters.** The literal paths `/enabled` and `/default-summary` are registered before
the `/{id}` family, otherwise `GET /default-summary` would be read as a template id. (The assistant's
`/assists/…` routes live in `workflows_assist` and are included first by `main.py`.)
The path parameter is `{template_id}` (policy-table convention: real parameter names, never `{id}`).
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.workflow import (
    ChatMessageOut,
    CreateTemplateRequest,
    EnabledRequest,
    FromTemplate,
    OpsBatch,
    PatchTemplateRequest,
    PublishRequest,
    ReplaceDraftRequest,
    RollbackRequest,
    ValidateRequest,
)
from ..security import deps
from ..security.principal import Principal
from ..services import workflow_service, workflow_views

router = APIRouter(prefix="/admin/workflows", tags=["workflows"])

PrincipalDep = Annotated[Principal, Depends(deps.get_principal)]
DbDep = Annotated[Session, Depends(get_db)]
TemplateId = Annotated[str, Path(min_length=1, max_length=64)]


def _chat_out(row: Any) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        role=row.role,
        content=row.content,
        applied=json.loads(row.applied_json) if row.applied_json else None,
        assistId=row.assist_id,
        createdAt=row.created_at,
    )


# ── literal paths first ──────────────────────────────────────────────────────


@router.get("")
def list_templates(
    db: DbDep, status: Annotated[str, Query(pattern="^(active|archived)$")] = "active"
) -> dict[str, Any]:
    items = workflow_views.list_summaries(db, status=status)
    default = workflow_service.default_summary(db)
    return {
        "enabled": default["enabled"],
        "defaultTemplateId": default["templateId"],
        "items": items,
    }


@router.post("", status_code=201)
def create_template(payload: CreateTemplateRequest, db: DbDep, principal: PrincipalDep):  # noqa: ANN201
    source: str | dict[str, str] = (
        {"templateId": payload.from_.templateId} if isinstance(payload.from_, FromTemplate) else payload.from_
    )
    return workflow_service.create(db, principal, payload.name, payload.description, source)


@router.put("/enabled")
def put_enabled(payload: EnabledRequest, db: DbDep, principal: PrincipalDep) -> dict[str, bool]:
    return {"enabled": workflow_service.set_enabled(db, principal, payload.enabled)}


@router.get("/default-summary")
def get_default_summary(db: DbDep) -> dict[str, Any]:
    return workflow_service.default_summary(db)


# ── one template ─────────────────────────────────────────────────────────────


@router.get("/{template_id}")
def get_template(template_id: TemplateId, db: DbDep):  # noqa: ANN201
    return workflow_service.detail(db, template_id)


@router.patch("/{template_id}")
def patch_template(template_id: TemplateId, payload: PatchTemplateRequest, db: DbDep, principal: PrincipalDep):  # noqa: ANN201
    return workflow_service.patch_meta(db, principal, template_id, payload.name, payload.description)


@router.delete("/{template_id}")
def archive_template(template_id: TemplateId, db: DbDep, principal: PrincipalDep) -> dict[str, str]:
    workflow_service.archive(db, principal, template_id)
    return {"status": "archived"}


@router.post("/{template_id}/ops")
def post_ops(template_id: TemplateId, payload: OpsBatch, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    return workflow_service.apply_ops(db, principal, template_id, payload.baseRevision, payload.ops)


@router.put("/{template_id}/draft")
def put_draft(
    template_id: TemplateId, payload: ReplaceDraftRequest, db: DbDep, principal: PrincipalDep
) -> dict[str, Any]:
    return workflow_service.replace_draft(db, principal, template_id, payload.baseRevision, payload.doc)


@router.post("/{template_id}/validate")
def post_validate(template_id: TemplateId, db: DbDep, payload: ValidateRequest | None = None):  # noqa: ANN201
    return workflow_service.validate(db, template_id, payload.doc if payload else None)


@router.get("/{template_id}/preview")
def get_preview(template_id: TemplateId, db: DbDep) -> dict[str, Any]:
    return workflow_service.preview(db, template_id)


@router.post("/{template_id}/publish", status_code=201)
def post_publish(
    template_id: TemplateId, payload: PublishRequest, db: DbDep, principal: PrincipalDep
) -> dict[str, Any]:
    return workflow_service.publish(
        db, principal, template_id, payload.baseRevision, payload.changeNote.strip(), payload.setDefault
    )


@router.post("/{template_id}/set-default")
def post_set_default(template_id: TemplateId, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    return workflow_service.set_default(db, principal, template_id)


# ── versions ─────────────────────────────────────────────────────────────────


@router.get("/{template_id}/versions")
def list_versions(
    template_id: TemplateId,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    workflow_service.get_row(db, template_id)
    items, next_cursor = workflow_views.list_versions(db, template_id, limit=limit, before=cursor)
    return {"items": items, "next": next_cursor}


@router.get("/{template_id}/versions/{version}")
def get_version(template_id: TemplateId, version: Annotated[int, Path(ge=1)], db: DbDep) -> dict[str, Any]:
    return workflow_views.version_with_diff(db, workflow_service.get_version(db, template_id, version))


@router.post("/{template_id}/versions/{version}/restore")
def restore_version(
    template_id: TemplateId, version: Annotated[int, Path(ge=1)], db: DbDep, principal: PrincipalDep
) -> dict[str, Any]:
    return workflow_service.restore_to_draft(db, principal, template_id, version)


@router.post("/{template_id}/versions/{version}/rollback", status_code=201)
def rollback_version(
    template_id: TemplateId,
    version: Annotated[int, Path(ge=1)],
    db: DbDep,
    principal: PrincipalDep,
    payload: RollbackRequest | None = None,
):  # noqa: ANN201
    note = payload.changeNote if payload else ""
    return workflow_service.rollback(db, principal, template_id, version, note)


@router.get("/{template_id}/usage")
def get_usage(template_id: TemplateId, db: DbDep, days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, Any]:
    workflow_service.get_row(db, template_id)
    return workflow_views.usage(db, template_id, days)


# ── assistant chat history ───────────────────────────────────────────────────


@router.get("/{template_id}/chat")
def get_chat(template_id: TemplateId, db: DbDep, principal: PrincipalDep) -> dict[str, Any]:
    rows = workflow_service.list_chat(db, template_id, principal.user_id)
    return {"items": [_chat_out(row) for row in rows]}


@router.delete("/{template_id}/chat", status_code=204)
def delete_chat(template_id: TemplateId, db: DbDep, principal: PrincipalDep) -> Response:
    workflow_service.clear_chat(db, template_id, principal.user_id)
    return Response(status_code=204)
