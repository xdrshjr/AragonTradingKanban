/**
 * 研究流程的类型镜像（真源：`api/app/schemas/workflow.py`，架构不变量 3：先改后端再同步这里）。
 */

export type NodeKind = 'start' | 'step' | 'gate' | 'note' | 'end';
export type Role = 'data' | 'analysis' | 'synthesis' | 'review' | 'custom';
export type Effort = 'light' | 'normal' | 'deep';
export type Severity = 'error' | 'warning';
export type EntityKind = 'graph' | 'node' | 'edge';
export type Adherence = 'full' | 'partial' | 'none' | 'unreported';

export interface Position {
  x: number;
  y: number;
}

export interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

/** 节点配置：按 kind 取子集（见 ops.ts 的 `CONFIG_KEYS`）。 */
export interface NodeConfig {
  role?: Role;
  prompt?: string;
  files?: string[];
  sections?: string[];
  successCriteria?: string;
  effort?: Effort;
  checklist?: string;
  body?: string;
}

export interface WorkflowNode {
  id: string;
  kind: NodeKind;
  title: string;
  position: Position;
  rev: number;
  config: NodeConfig;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  rev: number;
}

export interface WorkflowPolicy {
  maxTurns: number | null;
  maxDurationMinutes: number | null;
  progressMarkers: boolean;
}

export interface WorkflowDoc {
  schemaVersion: 1;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  policy: WorkflowPolicy;
  viewport: Viewport;
}

/** patch 里值为 `null` = 清除该可选键。 */
export type ConfigPatch = { [K in keyof NodeConfig]?: NodeConfig[K] | null };
export type PolicyPatch = { [K in keyof WorkflowPolicy]?: WorkflowPolicy[K] | null };

export type WorkflowOp =
  | { op: 'add_node'; node: WorkflowNode }
  | { op: 'update_node'; id: string; expectRev: number; patch: { title?: string; config?: ConfigPatch } }
  | { op: 'move_node'; id: string; position: Position }
  | { op: 'delete_node'; id: string; expectRev: number }
  | { op: 'add_edge'; edge: WorkflowEdge }
  | { op: 'update_edge'; id: string; expectRev: number; patch: { label?: string | null } }
  | { op: 'delete_edge'; id: string; expectRev: number }
  | { op: 'set_policy'; patch: PolicyPatch }
  | { op: 'set_viewport'; viewport: Viewport };

export type WorkflowOpName = WorkflowOp['op'];

/** 本批不提升 `draftRevision` 的两种 op（拖动与平移）。 */
export const NON_STRUCTURAL_OPS: ReadonlySet<string> = new Set(['move_node', 'set_viewport']);

export interface OpFailure {
  opIndex: number;
  reason: string;
  message: string;
  entityId: string | null;
}

export interface OpConflict {
  entityId: string;
  expected: number;
  actual: number | null;
}

export type ApplyResult =
  | { ok: true; doc: WorkflowDoc; structural: boolean }
  | { ok: false; failure: OpFailure; conflicts: OpConflict[] };

export interface ValidationIssue {
  code: string;
  severity: Severity;
  entityKind: EntityKind;
  entityId: string | null;
  params?: Record<string, string | number | string[]>;
}

export interface ValidationReport {
  issues: ValidationIssue[];
  errorCount: number;
  warningCount: number;
  publishable: boolean;
  blank: boolean;
}

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  isDefault: boolean;
  isBuiltin: boolean;
  status: 'active' | 'archived';
  latestVersion: number | null;
  stepCount: number;
  dirty: boolean;
  updatedAt: string;
  updatedByName: string | null;
  runs30d: number;
}

export interface VersionSummary {
  id: string;
  version: number;
  hash: string;
  changeNote: string;
  stepCount: number;
  planBytes: number;
  publishedAt: string;
  publishedByName: string | null;
}

export interface DraftState {
  doc: WorkflowDoc;
  revision: number;
  validation: ValidationReport;
  dirty: boolean;
}

export interface TemplateDetail {
  meta: TemplateSummary;
  draft: DraftState;
  versions: VersionSummary[];
}

export interface TemplateList {
  enabled: boolean;
  defaultTemplateId: string | null;
  items: TemplateSummary[];
}

export type CreateFrom = 'blank' | 'builtin' | { templateId: string };

export interface CreateTemplateBody {
  name: string;
  description?: string;
  from: CreateFrom;
}

export interface OpsResponse {
  revision: number;
  validation: ValidationReport;
  changed: boolean;
}

/** `409 workflow_revision_conflict` 的 `detail`：权威文档用于整体对齐。 */
export interface RevisionConflictDetail {
  currentRevision: number;
  doc: WorkflowDoc;
  conflicts?: OpConflict[];
}

export interface PublishBody {
  baseRevision: number;
  changeNote?: string;
  setDefault?: boolean;
}

export interface VersionDiffSummary {
  addedNodes: { id: string; title: string }[];
  removedNodes: { id: string; title: string }[];
  changedNodes: { id: string; title: string }[];
  addedEdges: number;
  removedEdges: number;
}

export interface VersionDetail {
  summary: VersionSummary;
  doc: WorkflowDoc;
  diffVsPrevious: VersionDiffSummary | null;
}

export interface PlanPreview {
  planMd: string;
  inlineIndex: string;
  levels: string[][];
  bytes: number;
  sha1: string;
  budget: { promptTotal: number; limit: number };
}

export interface UsageRow {
  version: number;
  runs: number;
  succeeded: number;
  failed: number;
  adherence: { full: number; partial: number; none: number };
  fallbacks: number;
}

export interface UsageResponse {
  byVersion: UsageRow[];
}

export interface DefaultSummary {
  templateId: string;
  name: string;
  version: number | null;
  hash: string | null;
  enabled: boolean;
}

export interface AssistBody {
  message: string;
  baseRevision: number;
  focusNodeIds?: string[];
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  applied: Record<string, unknown> | null;
  assistId: string | null;
  createdAt: string;
}

export interface AssistSummary {
  added: number;
  updated: number;
  deleted: number;
  edges: number;
}

/** 编辑器底部工具窗页签。 */
export type DockTab = 'problems' | 'plan' | 'versions' | 'usage';
