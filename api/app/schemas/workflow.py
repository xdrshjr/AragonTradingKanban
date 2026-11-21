"""Research workflow shapes (spec `report-workflow-graph` §3.2 / §5).

**The single type source** for the workflow graph; `web/lib/workflow/types.ts` mirrors it
(architecture invariant 3: backend first, then the frontend). camelCase on the wire like every
other schema here.

Two layers of strictness, on purpose:

* **Shape** (this file): `extra="forbid"`, id charset, per-kind `config` keys and string
  lengths. A document that fails here cannot even be stored (`PUT …/draft` -> 422) and an op
  whose patch fails here is rejected with `reason="bad_patch"`.
* **Meaning** (`services/workflow_validator.py`): cycles, reachability, section coverage…
  These only block *publishing* — a draft may always be saved while it is still wrong.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..services import workflow_contract as C

NodeKind = Literal["start", "step", "gate", "note", "end"]
Role = Literal["data", "analysis", "synthesis", "review", "custom"]
Effort = Literal["light", "normal", "deep"]
Severity = Literal["error", "warning"]
EntityKind = Literal["graph", "node", "edge"]
Adherence = Literal["full", "partial", "none", "unreported"]

_ID_RE = re.compile(C.ID_PATTERN)

#: Which keys a node's `config` may carry, per kind (anything else is `bad_patch`).
CONFIG_KEYS: dict[str, frozenset[str]] = {
    "start": frozenset(),
    "end": frozenset(),
    "step": frozenset({"role", "prompt", "files", "sections", "successCriteria", "effort"}),
    "gate": frozenset({"checklist"}),
    "note": frozenset({"body"}),
}
_STR_LIMITS = {
    "prompt": C.PROMPT_MAX,
    "successCriteria": C.CRITERIA_MAX,
    "checklist": C.CHECKLIST_MAX,
    "body": C.NOTE_MAX,
}


class WorkflowSchemaError(ValueError):
    """A shape violation with a machine-readable `reason` (the ops layer maps it to `bad_patch`)."""

    def __init__(self, message: str, reason: str = "bad_patch") -> None:
        super().__init__(message)
        self.reason = reason


def check_id(value: str) -> str:
    if value in C.RESERVED_IDS or not _ID_RE.match(value):
        raise WorkflowSchemaError(f"非法 id：{value!r}", "reserved_id")
    return value


def check_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate one node `config` mapping against its kind (type, enum, length). Returns it."""
    allowed = CONFIG_KEYS.get(kind)
    if allowed is None:
        raise WorkflowSchemaError(f"未知节点类型：{kind!r}", "bad_kind")
    for key, value in config.items():
        if key not in allowed:
            raise WorkflowSchemaError(f"{kind} 节点不支持配置项 {key!r}")
        _check_config_value(key, value)
    return config


def _check_config_value(key: str, value: Any) -> None:
    if key in _STR_LIMITS:
        if not isinstance(value, str) or len(value) > _STR_LIMITS[key]:
            raise WorkflowSchemaError(f"{key} 须为不超过 {_STR_LIMITS[key]} 字的文本")
    elif key == "role":
        if value not in C.ROLES:
            raise WorkflowSchemaError(f"role 取值须在 {list(C.ROLES)} 内")
    elif key == "effort":
        if value not in C.EFFORTS:
            raise WorkflowSchemaError(f"effort 取值须在 {list(C.EFFORTS)} 内")
    elif key in ("files", "sections") and (
        not isinstance(value, list) or not all(isinstance(item, str) for item in value) or len(value) > 20
    ):
        raise WorkflowSchemaError(f"{key} 须为字符串数组")


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _finite(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("坐标必须是有限数")
        return value


class Viewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = 0
    y: float = 0
    zoom: float = Field(default=1, ge=C.ZOOM_MIN, le=C.ZOOM_MAX)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: NodeKind
    title: str = Field(max_length=C.TITLE_MAX)
    position: Position
    rev: int = Field(default=1, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return check_id(value)

    @model_validator(mode="after")
    def _config(self) -> Node:
        check_config(self.kind, self.config)
        return self


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    label: str = Field(default="", max_length=C.EDGE_LABEL_MAX)
    rev: int = Field(default=1, ge=1)

    @field_validator("id", "source", "target")
    @classmethod
    def _id(cls, value: str) -> str:
        return check_id(value)


class Policy(BaseModel):
    """`null` = follow the site-wide value; a number can only **tighten** it (spec §3.2)."""

    model_config = ConfigDict(extra="forbid")

    maxTurns: int | None = None
    maxDurationMinutes: int | None = None
    progressMarkers: bool = True


class WorkflowDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1] = 1
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    policy: Policy = Field(default_factory=Policy)
    viewport: Viewport = Field(default_factory=Viewport)


# ── operation algebra (§3.3) ─────────────────────────────────────────────────


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddNode(_Op):
    op: Literal["add_node"] = "add_node"
    node: Node


class UpdateNode(_Op):
    op: Literal["update_node"] = "update_node"
    id: str
    expectRev: int
    patch: dict[str, Any]


class MoveNode(_Op):
    op: Literal["move_node"] = "move_node"
    id: str
    position: Position


class DeleteNode(_Op):
    op: Literal["delete_node"] = "delete_node"
    id: str
    expectRev: int


class AddEdge(_Op):
    op: Literal["add_edge"] = "add_edge"
    edge: Edge


class UpdateEdge(_Op):
    op: Literal["update_edge"] = "update_edge"
    id: str
    expectRev: int
    patch: dict[str, Any]


class DeleteEdge(_Op):
    op: Literal["delete_edge"] = "delete_edge"
    id: str
    expectRev: int


class SetPolicy(_Op):
    op: Literal["set_policy"] = "set_policy"
    patch: dict[str, Any]


class SetViewport(_Op):
    op: Literal["set_viewport"] = "set_viewport"
    viewport: Viewport


WorkflowOp = Union[  # noqa: UP007 - pydantic needs a runtime union for the discriminator
    AddNode, UpdateNode, MoveNode, DeleteNode, AddEdge, UpdateEdge, DeleteEdge, SetPolicy, SetViewport
]

#: Ops that never raise `draftRevision` / `rev` (§3.3 invariants 1 and 2).
NON_STRUCTURAL_OPS = frozenset({"move_node", "set_viewport"})


class OpsBatch(BaseModel):
    """`POST /admin/workflows/{id}/ops` body. Ops stay raw dicts here: the algebra parses them
    one by one so a bad op is reported **with its index** (`workflow_invalid_ops`)."""

    model_config = ConfigDict(extra="forbid")

    baseRevision: int = Field(ge=1)
    ops: list[dict[str, Any]] = Field(max_length=200)


class OpConflict(BaseModel):
    entityId: str
    expected: int
    actual: int | None


class OpFailure(BaseModel):
    opIndex: int
    reason: str
    message: str = ""
    entityId: str | None = None


# ── validation report (§3.4) ─────────────────────────────────────────────────


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    entityKind: EntityKind
    entityId: str | None = None
    params: dict[str, str | int | float | list[str]] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    errorCount: int = 0
    warningCount: int = 0
    publishable: bool = False
    blank: bool = False


# ── templates / versions (§5.1) ──────────────────────────────────────────────


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    isDefault: bool
    isBuiltin: bool
    status: Literal["active", "archived"]
    latestVersion: int | None = None
    stepCount: int = 0
    dirty: bool = False
    updatedAt: str
    updatedByName: str | None = None
    runs30d: int = 0


class VersionSummary(BaseModel):
    id: str
    version: int
    hash: str
    changeNote: str = ""
    stepCount: int
    planBytes: int
    publishedAt: str
    publishedByName: str | None = None


class DraftState(BaseModel):
    doc: WorkflowDoc
    revision: int
    validation: ValidationReport
    dirty: bool


class TemplateDetail(BaseModel):
    meta: TemplateSummary
    draft: DraftState
    versions: list[VersionSummary]


class FromTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templateId: str = Field(min_length=1, max_length=32)


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=C.NAME_MAX)
    description: str = Field(default="", max_length=C.DESCRIPTION_MAX)
    #: `"blank" | "builtin" | {"templateId": …}`
    from_: str | FromTemplate = Field(default="blank", alias="from")

    @field_validator("from_")
    @classmethod
    def _from(cls, value: str | FromTemplate) -> str | FromTemplate:
        if isinstance(value, str) and value not in ("blank", "builtin"):
            raise ValueError('from 须为 "blank"、"builtin" 或 {templateId}')
        return value


class PatchTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=C.NAME_MAX)
    description: str | None = Field(default=None, max_length=C.DESCRIPTION_MAX)


class ReplaceDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseRevision: int = Field(ge=1)
    doc: WorkflowDoc


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: WorkflowDoc | None = None


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseRevision: int = Field(ge=1)
    changeNote: str = Field(default="", max_length=C.CHANGE_NOTE_MAX)
    setDefault: bool = False


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changeNote: str = Field(default="", max_length=C.CHANGE_NOTE_MAX)


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class AssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=C.ASSIST_MESSAGE_MAX)
    baseRevision: int = Field(ge=1)
    focusNodeIds: list[str] = Field(default_factory=list, max_length=C.MAX_FOCUS_NODES)


class ChatMessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    applied: dict[str, Any] | None = None
    assistId: str | None = None
    createdAt: str


def doc_to_json(doc: WorkflowDoc, *, with_viewport: bool = True) -> str:
    """Canonical storage form (`draft_doc_json` keeps the viewport, `doc_json` of a version
    does not)."""
    data = doc.model_dump(mode="json")
    if not with_viewport:
        data.pop("viewport", None)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def doc_from_json(raw: str) -> WorkflowDoc:
    return WorkflowDoc.model_validate(json.loads(raw))
