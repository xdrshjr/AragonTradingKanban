"""Constants of the research-workflow feature, read from `contracts/workflow.json`.

The JSON file is the single source shared with `web/lib/workflow/contract.ts`; this module only
exposes it as Python constants (same pattern as `security/permissions.py`).
`tests/test_contract_workflow.py` pins both directions.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

#: `app/services/workflow_contract.py` -> repo root is three parents up (`api/app/services`).
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "workflow.json"


@lru_cache(maxsize=1)
def load_contract() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


_C = load_contract()
_L = _C["limits"]

SCHEMA_VERSION: int = _C["schemaVersion"]

MAX_NODES: int = _L["MAX_NODES"]
MAX_STEPS: int = _L["MAX_STEPS"]
MAX_EDGES: int = _L["MAX_EDGES"]
MAX_DOC_BYTES: int = _L["MAX_DOC_BYTES"]
TITLE_MAX: int = _L["TITLE_MAX"]
PROMPT_MAX: int = _L["PROMPT_MAX"]
CRITERIA_MAX: int = _L["CRITERIA_MAX"]
CHECKLIST_MAX: int = _L["CHECKLIST_MAX"]
NOTE_MAX: int = _L["NOTE_MAX"]
EDGE_LABEL_MAX: int = _L["EDGE_LABEL_MAX"]
NAME_MAX: int = _L["NAME_MAX"]
DESCRIPTION_MAX: int = _L["DESCRIPTION_MAX"]
CHANGE_NOTE_MAX: int = _L["CHANGE_NOTE_MAX"]
MAX_ACTIVE_TEMPLATES: int = _L["MAX_ACTIVE_TEMPLATES"]
MAX_VERSIONS_PER_TEMPLATE: int = _L["MAX_VERSIONS_PER_TEMPLATE"]
MAX_PLAN_BYTES: int = _L["MAX_PLAN_BYTES"]
WARN_PLAN_BYTES: int = _L["WARN_PLAN_BYTES"]
INLINE_INDEX_MAX_BYTES: int = _L["INLINE_INDEX_MAX_BYTES"]
ASSIST_MAX_OPS: int = _L["ASSIST_MAX_OPS"]
ASSIST_MESSAGE_MAX: int = _L["ASSIST_MESSAGE_MAX"]
CHAT_KEPT: int = _L["CHAT_KEPT"]
ZOOM_MIN: float = _L["ZOOM_MIN"]
ZOOM_MAX: float = _L["ZOOM_MAX"]
HIGH_FANOUT: int = _L["HIGH_FANOUT"]
LONG_PROMPT: int = _L["LONG_PROMPT"]
MAX_FOCUS_NODES: int = _L["MAX_FOCUS_NODES"]
PLACEHOLDER_VALUE_MAX: int = _L["PLACEHOLDER_VALUE_MAX"]

NODE_KINDS: tuple[str, ...] = tuple(_C["nodeKinds"])
ROLES: tuple[str, ...] = tuple(_C["roles"])
EFFORTS: tuple[str, ...] = tuple(_C["efforts"])
EFFORT_WEIGHTS: dict[str, int] = dict(_C["effortWeights"])
GATE_WEIGHT: int = _C["gateWeight"]
BUDGET_TIGHT_RATIO: float = _C["budgetTightRatio"]
FILES: tuple[str, ...] = tuple(f["name"] for f in _C["files"])
SECTIONS: tuple[str, ...] = tuple(s["id"] for s in _C["sections"])
SECTION_LABELS: dict[str, str] = {s["id"]: s["label"] for s in _C["sections"]}
FILE_LABELS: dict[str, str] = {f["name"]: f["label"] for f in _C["files"]}
PLACEHOLDERS: tuple[str, ...] = tuple(_C["placeholders"])
RESERVED_IDS: frozenset[str] = frozenset(_C["reservedIds"])
ID_PATTERN: str = _C["idPattern"]
MARKER_PATTERN: re.Pattern[str] = re.compile(_C["marker"]["pattern"])
MARKER_BUFFER_MAX: int = _C["marker"]["bufferMax"]
MARKER_NOTE_MAX: int = _C["marker"]["noteMax"]
ADHERENCE: tuple[str, ...] = tuple(_C["adherence"])
ERROR_CODES: tuple[str, ...] = tuple(_C["issueCodes"]["errors"])
WARNING_CODES: tuple[str, ...] = tuple(_C["issueCodes"]["warnings"])
OP_NAMES: tuple[str, ...] = tuple(_C["opNames"])
OP_REJECT_REASONS: tuple[str, ...] = tuple(_C["opRejectReasons"])
LAYOUT_ORIGIN_X: int = _C["layout"]["originX"]
LAYOUT_STEP_X: int = _C["layout"]["stepX"]
LAYOUT_ORIGIN_Y: int = _C["layout"]["originY"]
LAYOUT_STEP_Y: int = _C["layout"]["stepY"]
SAMPLE_CONTEXT: dict[str, str] = dict(_C["sampleContext"])
BUILTIN_TEMPLATE_ID: str = _C["builtin"]["templateId"]
BUILTIN_NAME: str = _C["builtin"]["name"]
BUILTIN_VERSION_NOTE: str = _C["builtin"]["versionNote"]
