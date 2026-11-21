/**
 * 研究流程编辑器的几何真源（spec §3.10.2）：
 *
 * * `layoutLevels` —— 分层布局，与 Python `workflow_ops.layout_levels` **逐点一致**
 *   （`contracts/workflow_ops.golden.json` 的 `layout` 组两端断言）；
 * * 70/30 外层栅格：`computeSplit`；
 * * 左栏内层让位规则：`computeInner`（画布是主角，其余面板给它让位，RW-5）。
 *
 * 这里是这些常量的**唯一**出处：编辑器把结果写回 `--wf-left-w / --wf-right-w`，样式只读变量。
 */

import { LAYOUT } from './contract';
import type { WorkflowDoc } from './types';

// ── 70/30 外层 ───────────────────────────────────────────────────────────────

export const SPLIT_DEFAULT = 70;
export const SPLIT_MIN = 55;
export const SPLIT_MAX = 80;
export const LEFT_MIN_PX = 560;
export const RIGHT_MIN_PX = 320;
/** 低于此视口宽度右栏收为抽屉（⌘J）。 */
export const BP_DRAWER = 1100;
/** 低于此视口宽度进入只读查看。 */
export const BP_READONLY = 768;
export const SPLIT_STORAGE_KEY = 'aragon.workflow.split.v1';
export const PANES_STORAGE_KEY = 'aragon.workflow.panes.v1';

export interface SplitWidths {
  leftPx: number;
  rightPx: number;
  /** 实际落地的左栏百分比（含像素兜底后）。 */
  leftPct: number;
}

export function clampPct(pct: number): number {
  if (!Number.isFinite(pct)) return SPLIT_DEFAULT;
  return Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, pct));
}

/**
 * 在百分比之外加像素兜底：右栏至少 `RIGHT_MIN_PX`（更窄则右栏固定 320、左栏取余）。
 * 1280px：30% = 384 ✔；1100px：30% = 330 ✔。
 */
export function computeSplit(containerPx: number, pct: number = SPLIT_DEFAULT): SplitWidths {
  const total = Math.max(0, Math.round(containerPx));
  const wanted = Math.round((total * (100 - clampPct(pct))) / 100);
  const rightPx = Math.min(total, Math.max(wanted, RIGHT_MIN_PX));
  const leftPx = total - rightPx;
  return { leftPx, rightPx, leftPct: total > 0 ? (leftPx / total) * 100 : SPLIT_DEFAULT };
}

/** 拖动分隔条：把增量（px）换算成新的左栏百分比（已夹取）。 */
export function dragSplit(containerPx: number, currentPct: number, deltaPx: number): number {
  if (containerPx <= 0) return clampPct(currentPct);
  return clampPct(currentPct + (deltaPx / containerPx) * 100);
}

export function toCssVariables(widths: SplitWidths): Record<string, string> {
  return { '--wf-left-w': `${widths.leftPx}px`, '--wf-right-w': `${widths.rightPx}px` };
}

/** 读取持久化的左栏百分比；读不到、损坏、越界都回落到默认 70。 */
export function loadSplit(): number {
  try {
    const raw = window.localStorage.getItem(SPLIT_STORAGE_KEY);
    if (raw === null) return SPLIT_DEFAULT;
    const value = Number(raw);
    return Number.isFinite(value) ? clampPct(value) : SPLIT_DEFAULT;
  } catch {
    return SPLIT_DEFAULT;
  }
}

export function saveSplit(pct: number): void {
  try {
    window.localStorage.setItem(SPLIT_STORAGE_KEY, String(Math.round(clampPct(pct) * 10) / 10));
  } catch {
    /* 隐私模式 / 配额：偏好丢了不影响使用 */
  }
}

// ── 左栏内层让位 ─────────────────────────────────────────────────────────────

export const STRUCTURE_W = 200;
export const STRUCTURE_ICON_W = 28;
export const INSPECTOR_W = 288;
export const CANVAS_MIN_W = 400;
export const DOCK_H = 220;
export const DOCK_BAR_H = 28;
export const CANVAS_MIN_H = 320;

/** 用户手动展开/收起的偏好；`undefined` = 跟随默认。偏好不得突破 `CANVAS_MIN_W`。 */
export interface PanePrefs {
  structure?: boolean;
  inspector?: boolean;
  dock?: boolean;
}

export type StructureMode = 'docked' | 'icon';
export type InspectorMode = 'docked' | 'overlay';
export type DockMode = 'collapsed' | 'docked' | 'overlay';

export interface InnerLayout {
  structure: StructureMode;
  inspector: InspectorMode;
  dock: DockMode;
  /** 画布实际宽度（并排的情况下）。 */
  canvasW: number;
}

export interface InnerInput {
  leftPx: number;
  heightPx: number;
  prefs?: PanePrefs;
  /** 有发布阻断错误时底部工具窗自动展开。 */
  hasErrors?: boolean;
}

/**
 * 让位规则（按序判定）：
 * ① 左栏 ≥ 结构树 + 检查器 + 画布最小宽（888）时三栏并排；
 * ② 否则结构树收为 28px 图标条；
 * ③ 仍 < 检查器 + 画布最小宽（688）时检查器改为画布右侧浮层；
 * ④ 底部工具窗默认收起为标题条，仅在有错误 / 用户展开时展开；高度不够则降为浮层。
 */
export function computeInner(input: InnerInput): InnerLayout {
  const { leftPx, heightPx, prefs = {}, hasErrors = false } = input;
  const wantStructure = prefs.structure !== false;
  const wantInspector = prefs.inspector !== false;

  const roomForAll = leftPx >= STRUCTURE_W + INSPECTOR_W + CANVAS_MIN_W;
  const structure: StructureMode = wantStructure && roomForAll ? 'docked' : 'icon';
  const reserved = structure === 'docked' ? STRUCTURE_W : STRUCTURE_ICON_W;
  const inspector: InspectorMode =
    wantInspector && leftPx - reserved >= INSPECTOR_W + CANVAS_MIN_W ? 'docked' : 'overlay';

  const canvasW = Math.max(0, leftPx - reserved - (inspector === 'docked' ? INSPECTOR_W : 0));
  const dockWanted = prefs.dock === true || (prefs.dock === undefined && hasErrors);
  let dock: DockMode = 'collapsed';
  if (dockWanted) dock = heightPx - DOCK_H >= CANVAS_MIN_H ? 'docked' : 'overlay';
  return { structure, inspector, dock, canvasW };
}

export function loadPanePrefs(): PanePrefs {
  try {
    const raw = window.localStorage.getItem(PANES_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return {};
    const source = parsed as Record<string, unknown>;
    const out: PanePrefs = {};
    for (const key of ['structure', 'inspector', 'dock'] as const) {
      if (typeof source[key] === 'boolean') out[key] = source[key];
    }
    return out;
  } catch {
    return {};
  }
}

export function savePanePrefs(prefs: PanePrefs): void {
  try {
    window.localStorage.setItem(PANES_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* 同上 */
  }
}

// ── 分层布局（与 Python 共享 golden）──────────────────────────────────────────

export interface LayoutPoint {
  x: number;
  y: number;
}

/** Kahn 分层：层级 = 到该节点的最长路径；环内（及其后）节点归入末尾多出的一层，保持文档序。 */
function levelsOf(doc: WorkflowDoc): string[][] {
  const order = doc.nodes.filter((node) => node.kind !== 'note').map((node) => node.id);
  const known = new Set(order);
  const out = new Map<string, string[]>(order.map((id) => [id, []]));
  const indegree = new Map<string, number>(order.map((id) => [id, 0]));
  const seen = new Set<string>();
  for (const edge of doc.edges) {
    const key = `${edge.source}\u0000${edge.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (known.has(edge.source) && known.has(edge.target) && edge.source !== edge.target) {
      out.get(edge.source)?.push(edge.target);
      indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    }
  }
  const index = new Map(order.map((id, i) => [id, i]));
  const levels: string[][] = [];
  const placed = new Set<string>();
  let frontier = order.filter((id) => indegree.get(id) === 0);
  while (frontier.length > 0) {
    levels.push(frontier);
    frontier.forEach((id) => placed.add(id));
    const next: string[] = [];
    for (const id of frontier) {
      for (const target of out.get(id) ?? []) {
        const remaining = (indegree.get(target) ?? 0) - 1;
        indegree.set(target, remaining);
        if (remaining === 0) next.push(target);
      }
    }
    frontier = next.sort((a, b) => (index.get(a) ?? 0) - (index.get(b) ?? 0));
  }
  const leftover = order.filter((id) => !placed.has(id));
  if (leftover.length > 0) levels.push(leftover);
  return levels;
}

/** `{nodeId: {x, y}}`，note 不参与。每层相对最高的一层垂直居中。 */
export function layoutLevels(doc: WorkflowDoc): Record<string, LayoutPoint> {
  const levels = levelsOf(doc);
  const tallest = levels.reduce((max, level) => Math.max(max, level.length), 0);
  const result: Record<string, LayoutPoint> = {};
  levels.forEach((level, depth) => {
    const pad = (tallest - level.length) / 2;
    level.forEach((id, index) => {
      result[id] = {
        x: LAYOUT.originX + depth * LAYOUT.stepX,
        y: LAYOUT.originY + (index + pad) * LAYOUT.stepY,
      };
    });
  });
  return result;
}
