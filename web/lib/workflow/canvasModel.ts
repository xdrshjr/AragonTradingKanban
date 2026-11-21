/**
 * 画布编辑器的纯函数模型（M63）：从 `WorkflowDoc` 派生展示用的键、连线判定、占位符扫描、
 * 章节覆盖与回合估算。**不含任何写入**——写入一律经 store.dispatch 的 op。
 *
 * 键（`S1`/`G1`）的分配与服务端编译器一致：按（层级，文档序）。这里只用于展示，
 * 权威仍是 `workflow.md` 里的键（预览页签）。
 */

import { EFFORT_WEIGHTS, GATE_WEIGHT, GLOBAL_CEILING, LAYOUT, PLACEHOLDERS, SECTIONS } from './contract';
import { layoutLevels } from './layout';
import { wouldCreateCycle } from './ops';
import type { ValidationReport, VersionDiffSummary, WorkflowDoc, WorkflowNode } from './types';

export type ConnectRejection =
  | 'self_loop'
  | 'duplicate_edge'
  | 'cycle'
  | 'note'
  | 'start_input'
  | 'end_output'
  | 'unknown';

export type ConnectVerdict = { ok: true } | { ok: false; reason: ConnectRejection };

/** 连线合法性预判（服务端仍是权威；这里让非法目标即时被拒）。 */
export function connectionVerdict(doc: WorkflowDoc, source: string, target: string): ConnectVerdict {
  const from = doc.nodes.find((node) => node.id === source);
  const to = doc.nodes.find((node) => node.id === target);
  if (!from || !to) return { ok: false, reason: 'unknown' };
  if (source === target) return { ok: false, reason: 'self_loop' };
  if (from.kind === 'note' || to.kind === 'note') return { ok: false, reason: 'note' };
  if (from.kind === 'end') return { ok: false, reason: 'end_output' };
  if (to.kind === 'start') return { ok: false, reason: 'start_input' };
  if (doc.edges.some((edge) => edge.source === source && edge.target === target)) {
    return { ok: false, reason: 'duplicate_edge' };
  }
  if (wouldCreateCycle(doc, source, target)) return { ok: false, reason: 'cycle' };
  return { ok: true };
}

/** 展示用的 `S1…`/`G1…` 键与阶段号（按层级、文档序）。start/end/note 不分配键。 */
export interface PlanKeyInfo {
  key: string;
  level: number;
}

export function planKeys(doc: WorkflowDoc): Record<string, PlanKeyInfo> {
  const points = layoutLevels(doc);
  const depthOf = (id: string): number => {
    const point = points[id];
    return point ? Math.round((point.x - LAYOUT.originX) / LAYOUT.stepX) : 0;
  };
  const entries = doc.nodes
    .map((node, index) => ({ node, index, depth: depthOf(node.id) }))
    .filter(({ node }) => node.kind === 'step' || node.kind === 'gate')
    .sort((a, b) => a.depth - b.depth || a.index - b.index);
  const counters = { S: 0, G: 0 };
  const result: Record<string, PlanKeyInfo> = {};
  for (const { node, depth } of entries) {
    const letter = node.kind === 'gate' ? 'G' : 'S';
    counters[letter] += 1;
    result[node.id] = { key: `${letter}${counters[letter]}`, level: Math.max(1, depth) };
  }
  return result;
}

/** 键盘导航顺序：层级 → 同层按纵坐标。包含 start/end，不含 note（note 排在末尾）。 */
export function navigationOrder(doc: WorkflowDoc): string[] {
  const points = layoutLevels(doc);
  const placed = doc.nodes.filter((node) => node.kind !== 'note' && points[node.id]);
  placed.sort((a, b) => {
    const pa = points[a.id];
    const pb = points[b.id];
    return (pa?.x ?? 0) - (pb?.x ?? 0) || (pa?.y ?? 0) - (pb?.y ?? 0);
  });
  return [...placed, ...doc.nodes.filter((node) => node.kind === 'note')].map((node) => node.id);
}

export interface NodeIssueCount {
  errors: number;
  warnings: number;
}

export function issuesByEntity(report: ValidationReport | null | undefined): Record<string, NodeIssueCount> {
  const out: Record<string, NodeIssueCount> = {};
  for (const issue of report?.issues ?? []) {
    if (!issue.entityId) continue;
    const slot = (out[issue.entityId] ??= { errors: 0, warnings: 0 });
    if (issue.severity === 'error') slot.errors += 1;
    else slot.warnings += 1;
  }
  return out;
}

// ── 占位符（词法）────────────────────────────────────────────────────────────

export interface PlaceholderToken {
  start: number;
  end: number;
  name: string;
  /** `\{{x}}` 转义：字面量，不算占位符。 */
  escaped: boolean;
  defined: boolean;
}

const PLACEHOLDER_RE = /(\\)?\{\{\s*([^{}\s]+)\s*\}\}/g;

/** 词法扫描：只做高亮与即时红线，权威判定仍是服务端 `undefined_placeholder`。 */
export function scanPlaceholders(text: string): PlaceholderToken[] {
  const known = new Set(PLACEHOLDERS);
  const tokens: PlaceholderToken[] = [];
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    const name = match[2] ?? '';
    tokens.push({
      start: match.index ?? 0,
      end: (match.index ?? 0) + match[0].length,
      name,
      escaped: match[1] !== undefined,
      defined: known.has(name),
    });
  }
  return tokens;
}

export function undefinedPlaceholders(text: string): string[] {
  const names = scanPlaceholders(text)
    .filter((token) => !token.escaped && !token.defined)
    .map((token) => token.name);
  return [...new Set(names)];
}

// ── 章节覆盖与预算估算 ───────────────────────────────────────────────────────

/** 每个章节由哪些 step 节点服务（只看已连入流程的 step 不现实——此处按声明统计）。 */
export function sectionCoverage(doc: WorkflowDoc): Record<string, string[]> {
  const out: Record<string, string[]> = Object.fromEntries(SECTIONS.map((section) => [section.id, []]));
  for (const node of doc.nodes) {
    if (node.kind !== 'step') continue;
    for (const section of node.config.sections ?? []) out[section]?.push(node.id);
  }
  return out;
}

export function effortOf(node: WorkflowNode): 'light' | 'normal' | 'deep' {
  return node.config.effort ?? 'normal';
}

/** 与服务端 `budget_tight` 同口径：1（读取 workflow.md）+ Σ 权重 + gate 各 1。仅为估算。 */
export function estimateTurns(doc: WorkflowDoc): number {
  let total = 1;
  for (const node of doc.nodes) {
    if (node.kind === 'step') total += EFFORT_WEIGHTS[effortOf(node)] ?? 4;
    else if (node.kind === 'gate') total += GATE_WEIGHT;
  }
  return total;
}

export function effectiveMaxTurns(doc: WorkflowDoc): number {
  const cap = GLOBAL_CEILING.aragonMaxTurns;
  return Math.min(doc.policy.maxTurns ?? cap, cap);
}

export function upstreamOf(doc: WorkflowDoc, id: string): string[] {
  return doc.edges.filter((edge) => edge.target === id).map((edge) => edge.source);
}

export function downstreamOf(doc: WorkflowDoc, id: string): string[] {
  return doc.edges.filter((edge) => edge.source === id).map((edge) => edge.target);
}

/** 节点的展示标题：空标题回落到"未命名"。 */
export function displayTitle(node: WorkflowNode, fallback: string): string {
  return node.title.trim() === '' ? fallback : node.title;
}

// ── 结构 diff（发布对话框 / 版本对比）────────────────────────────────────────


function nodeSignature(node: WorkflowNode): string {
  return JSON.stringify({ kind: node.kind, title: node.title, config: node.config });
}

/**
 * 两份文档的**内容**差异（与服务端内容哈希同口径：忽略位置 / viewport / rev，注释不计）。
 * 用于发布对话框"与上一版本对比"与版本页签"与草稿对比"。
 */
export function diffDocs(from: WorkflowDoc | null, to: WorkflowDoc): VersionDiffSummary {
  const content = (doc: WorkflowDoc) => doc.nodes.filter((node) => node.kind !== 'note');
  const before = new Map(content(from ?? { ...to, nodes: [], edges: [] }).map((node) => [node.id, node]));
  const after = new Map(content(to).map((node) => [node.id, node]));
  const added = [...after.values()].filter((node) => !before.has(node.id));
  const removed = [...before.values()].filter((node) => !after.has(node.id));
  const changed = [...after.values()].filter((node) => {
    const old = before.get(node.id);
    return old !== undefined && nodeSignature(old) !== nodeSignature(node);
  });
  const edgeKey = (edge: { source: string; target: string; label: string }): string => `${edge.source}>${edge.target}|${edge.label}`;
  const oldEdges = new Set((from?.edges ?? []).map(edgeKey));
  const newEdges = new Set(to.edges.map(edgeKey));
  const pick = (node: WorkflowNode) => ({ id: node.id, title: node.title });
  return {
    addedNodes: added.map(pick),
    removedNodes: removed.map(pick),
    changedNodes: changed.map(pick),
    addedEdges: [...newEdges].filter((key) => !oldEdges.has(key)).length,
    removedEdges: [...oldEdges].filter((key) => !newEdges.has(key)).length,
  };
}

export function isEmptyDiff(diff: VersionDiffSummary): boolean {
  return (
    diff.addedNodes.length === 0 &&
    diff.removedNodes.length === 0 &&
    diff.changedNodes.length === 0 &&
    diff.addedEdges === 0 &&
    diff.removedEdges === 0
  );
}
