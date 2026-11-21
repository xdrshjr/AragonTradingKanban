/**
 * 研究流程的限额 / 枚举 / 占位符（只读 `contracts/workflow.json`，后端同读一份）。
 * `tests/workflow.contract.test.ts` 与 `api/tests/test_contract_workflow.py` 双向守卫。
 */

import contract from '../../../contracts/workflow.json';

interface FileEntry {
  name: string;
  label: string;
  hint: string;
}
interface SectionEntry {
  id: string;
  label: string;
}

interface WorkflowContract {
  schemaVersion: number;
  limits: Record<string, number>;
  nodeKinds: string[];
  roles: string[];
  efforts: string[];
  effortWeights: Record<string, number>;
  gateWeight: number;
  budgetTightRatio: number;
  files: FileEntry[];
  sections: SectionEntry[];
  placeholders: string[];
  reservedIds: string[];
  idPattern: string;
  adherence: string[];
  issueCodes: { errors: string[]; warnings: string[] };
  opNames: string[];
  opRejectReasons: string[];
  layout: { originX: number; stepX: number; originY: number; stepY: number };
  global: { aragonMaxTurns: number; aragonMaxDurationMinutes: number };
  builtin: { templateId: string; name: string };
}

export const WORKFLOW_CONTRACT = contract as unknown as WorkflowContract;

const LIMIT_TABLE = WORKFLOW_CONTRACT.limits;
const limit = (key: string): number => LIMIT_TABLE[key] ?? 0;

export const LIMITS = {
  MAX_NODES: limit('MAX_NODES'),
  MAX_STEPS: limit('MAX_STEPS'),
  MAX_EDGES: limit('MAX_EDGES'),
  MAX_DOC_BYTES: limit('MAX_DOC_BYTES'),
  TITLE_MAX: limit('TITLE_MAX'),
  PROMPT_MAX: limit('PROMPT_MAX'),
  CRITERIA_MAX: limit('CRITERIA_MAX'),
  CHECKLIST_MAX: limit('CHECKLIST_MAX'),
  NOTE_MAX: limit('NOTE_MAX'),
  EDGE_LABEL_MAX: limit('EDGE_LABEL_MAX'),
  NAME_MAX: limit('NAME_MAX'),
  DESCRIPTION_MAX: limit('DESCRIPTION_MAX'),
  CHANGE_NOTE_MAX: limit('CHANGE_NOTE_MAX'),
  ASSIST_MESSAGE_MAX: limit('ASSIST_MESSAGE_MAX'),
  ZOOM_MIN: limit('ZOOM_MIN'),
  ZOOM_MAX: limit('ZOOM_MAX'),
  LONG_PROMPT: limit('LONG_PROMPT'),
} as const;

export const NODE_KINDS = WORKFLOW_CONTRACT.nodeKinds;
export const ROLES = WORKFLOW_CONTRACT.roles;
export const EFFORTS = WORKFLOW_CONTRACT.efforts;
export const EFFORT_WEIGHTS = WORKFLOW_CONTRACT.effortWeights;
export const GATE_WEIGHT = WORKFLOW_CONTRACT.gateWeight;
export const FILES = WORKFLOW_CONTRACT.files;
export const SECTIONS = WORKFLOW_CONTRACT.sections;
export const PLACEHOLDERS = WORKFLOW_CONTRACT.placeholders;
export const RESERVED_IDS: ReadonlySet<string> = new Set(WORKFLOW_CONTRACT.reservedIds);
export const ID_RE = new RegExp(WORKFLOW_CONTRACT.idPattern);
export const LAYOUT = WORKFLOW_CONTRACT.layout;
export const GLOBAL_CEILING = WORKFLOW_CONTRACT.global;
export const BUILTIN_TEMPLATE_ID = WORKFLOW_CONTRACT.builtin.templateId;
