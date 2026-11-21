/**
 * 研究流程的操作代数（spec §3.3）——`api/app/services/workflow_ops.py` 的**前端镜像**。
 *
 * 画布手势与 AI 助手的所有写入都是一批 `WorkflowOp`，经 `applyOps` 整批原子应用。两端的一致性由
 * `contracts/workflow_ops.golden.json` 逐条守卫（`tests/workflow.ops.test.ts` 与
 * `api/tests/test_workflow_ops.py` 读同一份文件）。改规则 = 先改 Python，再同步这里与 golden。
 *
 * 三条不变量：`set_viewport` / `move_node` 不动任何 `rev`（也不提升草稿 revision，见 `structural`）；
 * 整批原子；`expectRev` 与**批首**的实体 rev 比较（批内新增的实体视为 rev 1）。
 */

import {
  EFFORTS,
  ID_RE,
  LIMITS,
  NODE_KINDS,
  RESERVED_IDS,
  ROLES,
} from './contract';
import type {
  ApplyResult,
  NodeConfig,
  NodeKind,
  OpConflict,
  OpFailure,
  Position,
  WorkflowDoc,
  WorkflowEdge,
  WorkflowNode,
  WorkflowOp,
} from './types';
import { NON_STRUCTURAL_OPS } from './types';

export const CONFIG_KEYS: Record<string, readonly string[]> = {
  start: [],
  end: [],
  step: ['role', 'prompt', 'files', 'sections', 'successCriteria', 'effort'],
  gate: ['checklist'],
  note: ['body'],
};

const STRING_LIMITS: Record<string, number> = {
  prompt: LIMITS.PROMPT_MAX,
  successCriteria: LIMITS.CRITERIA_MAX,
  checklist: LIMITS.CHECKLIST_MAX,
  body: LIMITS.NOTE_MAX,
};

const ADDABLE_KINDS: readonly string[] = ['step', 'gate', 'note'];
const IMMUTABLE_NODE = ['kind', 'id', 'position', 'rev'];
const IMMUTABLE_EDGE = ['id', 'source', 'target', 'rev'];
const POLICY_KEYS = ['maxTurns', 'maxDurationMinutes', 'progressMarkers'];
const GROWING_OPS = new Set(['add_node', 'update_node', 'add_edge', 'update_edge']);

export const REV_CONFLICT = 'rev_conflict';

// ── 拒绝 ─────────────────────────────────────────────────────────────────────

class Reject extends Error {
  constructor(
    readonly reason: string,
    message: string,
    readonly entityId: string | null = null,
    readonly conflict: OpConflict | null = null,
  ) {
    super(message);
  }
}

// ── 形状校验（对应 Pydantic 的 extra="forbid" 与字段约束）────────────────────

type Raw = Record<string, unknown>;

const isObject = (value: unknown): value is Raw =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isInt = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value);
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function bad(message: string, entityId: string | null = null): never {
  throw new Reject('bad_patch', message, entityId);
}

function checkKeys(raw: Raw, allowed: readonly string[], what: string): void {
  for (const key of Object.keys(raw)) {
    if (!allowed.includes(key)) bad(`${what} 不支持字段 ${key}`);
  }
}

function checkId(value: unknown, what: string): string {
  if (typeof value !== 'string') bad(`${what} 须为字符串`);
  const id = value as string;
  if (RESERVED_IDS.has(id) || !ID_RE.test(id)) throw new Reject('reserved_id', `非法 id：${id}`, id);
  return id;
}

function checkPosition(value: unknown): Position {
  if (!isObject(value) || !isFiniteNumber(value.x) || !isFiniteNumber(value.y)) bad('position 须为有限坐标');
  const { x, y } = value as { x: number; y: number };
  return { x, y };
}

function checkConfigValue(key: string, value: unknown): void {
  const cap = STRING_LIMITS[key];
  if (cap !== undefined) {
    if (typeof value !== 'string' || value.length > cap) bad(`${key} 须为不超过 ${cap} 字的文本`);
  } else if (key === 'role') {
    if (typeof value !== 'string' || !ROLES.includes(value)) bad('role 取值非法');
  } else if (key === 'effort') {
    if (typeof value !== 'string' || !EFFORTS.includes(value)) bad('effort 取值非法');
  } else if (key === 'files' || key === 'sections') {
    if (!Array.isArray(value) || value.length > 20 || !value.every((item) => typeof item === 'string')) {
      bad(`${key} 须为字符串数组`);
    }
  }
}

export function checkConfig(kind: string, config: Raw): void {
  const allowed = CONFIG_KEYS[kind];
  if (!allowed) throw new Reject('bad_kind', `未知节点类型：${kind}`);
  for (const [key, value] of Object.entries(config)) {
    if (!allowed.includes(key)) bad(`${kind} 节点不支持配置项 ${key}`);
    checkConfigValue(key, value);
  }
}

function parseNode(raw: unknown): WorkflowNode {
  if (!isObject(raw)) bad('node 须为对象');
  const node = raw as Raw;
  const id = checkId(node.id, 'node.id');
  const kind = node.kind;
  if (typeof kind !== 'string' || !NODE_KINDS.includes(kind)) {
    throw new Reject('bad_kind', `未知节点类型：${String(kind)}`);
  }
  checkKeys(node, ['id', 'kind', 'title', 'position', 'rev', 'config'], 'node');
  const title = node.title;
  if (typeof title !== 'string' || title.length > LIMITS.TITLE_MAX) bad('title 须为不超过上限的文本');
  const rev = node.rev === undefined ? 1 : node.rev;
  if (!isInt(rev) || rev < 1) bad('rev 须为正整数');
  const config = node.config === undefined ? {} : node.config;
  if (!isObject(config)) bad('config 须为对象');
  checkConfig(kind, config as Raw);
  return {
    id,
    kind: kind as NodeKind,
    title: title as string,
    position: checkPosition(node.position),
    rev: rev as number,
    config: { ...(config as NodeConfig) },
  };
}

function parseEdge(raw: unknown): WorkflowEdge {
  if (!isObject(raw)) bad('edge 须为对象');
  const edge = raw as Raw;
  const id = checkId(edge.id, 'edge.id');
  checkKeys(edge, ['id', 'source', 'target', 'label', 'rev'], 'edge');
  const label = edge.label === undefined ? '' : edge.label;
  if (typeof label !== 'string' || label.length > LIMITS.EDGE_LABEL_MAX) bad('label 须为不超过上限的文本');
  const rev = edge.rev === undefined ? 1 : edge.rev;
  if (!isInt(rev) || rev < 1) bad('rev 须为正整数');
  return {
    id,
    source: checkId(edge.source, 'edge.source'),
    target: checkId(edge.target, 'edge.target'),
    label: label as string,
    rev: rev as number,
  };
}

// ── 工作状态 ─────────────────────────────────────────────────────────────────

interface State {
  nodes: Map<string, WorkflowNode>;
  edges: Map<string, WorkflowEdge>;
  policy: WorkflowDoc['policy'];
  viewport: WorkflowDoc['viewport'];
  baseNodeRevs: Map<string, number>;
  baseEdgeRevs: Map<string, number>;
}

function initialState(doc: WorkflowDoc): State {
  const copy = structuredClone(doc);
  return {
    nodes: new Map(copy.nodes.map((node) => [node.id, node])),
    edges: new Map(copy.edges.map((edge) => [edge.id, edge])),
    policy: copy.policy,
    viewport: copy.viewport,
    baseNodeRevs: new Map(doc.nodes.map((node) => [node.id, node.rev])),
    baseEdgeRevs: new Map(doc.edges.map((edge) => [edge.id, edge.rev])),
  };
}

function snapshot(state: State): WorkflowDoc {
  return {
    schemaVersion: 1,
    nodes: [...state.nodes.values()],
    edges: [...state.edges.values()],
    policy: state.policy,
    viewport: state.viewport,
  };
}

function docBytes(state: State): number {
  return new TextEncoder().encode(JSON.stringify(snapshot(state))).length;
}

function needNode(state: State, id: string): WorkflowNode {
  const node = state.nodes.get(id);
  if (!node) throw new Reject('unknown_node', `节点不存在：${id}`, id);
  return node;
}

function needEdge(state: State, id: string): WorkflowEdge {
  const edge = state.edges.get(id);
  if (!edge) throw new Reject('unknown_edge', `连线不存在：${id}`, id);
  return edge;
}

function checkRev(base: Map<string, number>, id: string, expected: number): void {
  const actual = base.get(id) ?? 1;
  if (actual !== expected) {
    throw new Reject(REV_CONFLICT, `${id} 已被修改（期望 rev ${expected}，实际 ${actual}）`, id, {
      entityId: id,
      expected,
      actual,
    });
  }
}

// ── 图辅助 ───────────────────────────────────────────────────────────────────

function reaches(edges: Iterable<WorkflowEdge>, start: string, goal: string): boolean {
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    const list = adjacency.get(edge.source);
    if (list) list.push(edge.target);
    else adjacency.set(edge.source, [edge.target]);
  }
  const stack = [start];
  const seen = new Set([start]);
  while (stack.length > 0) {
    const current = stack.pop() as string;
    if (current === goal) return true;
    for (const next of adjacency.get(current) ?? []) {
      if (!seen.has(next)) {
        seen.add(next);
        stack.push(next);
      }
    }
  }
  return false;
}

/** 连线时的即时预判：自环或 `target` 已能到达 `source` 都会成环。 */
export function wouldCreateCycle(doc: WorkflowDoc, source: string, target: string): boolean {
  if (source === target) return true;
  return reaches(doc.edges, target, source);
}

// ── op 处理 ──────────────────────────────────────────────────────────────────

type Handler = (state: State, raw: Raw) => void;

function opAddNode(state: State, raw: Raw): void {
  const node = parseNode(raw.node);
  if (!ADDABLE_KINDS.includes(node.kind)) {
    throw new Reject('bad_kind', `不能新增 ${node.kind} 节点（起止锚点只由空白文档创建）`, node.id);
  }
  if (state.nodes.has(node.id)) throw new Reject('duplicate_id', `节点 id 重复：${node.id}`, node.id);
  const steps = [...state.nodes.values()].filter((n) => n.kind === 'step').length;
  if (state.nodes.size >= LIMITS.MAX_NODES || (node.kind === 'step' && steps >= LIMITS.MAX_STEPS)) {
    throw new Reject('limit_exceeded', '节点或步骤数量已达上限', node.id);
  }
  node.rev = 1;
  state.nodes.set(node.id, node);
  state.baseNodeRevs.set(node.id, 1);
}

function mergeConfig(node: WorkflowNode, patch: unknown, id: string): NodeConfig {
  if (patch === undefined || patch === null) return node.config;
  if (!isObject(patch)) bad('config 须为对象', id);
  const merged: Raw = { ...node.config };
  for (const [key, value] of Object.entries(patch as Raw)) {
    if (value === null) delete merged[key];
    else merged[key] = value;
  }
  try {
    checkConfig(node.kind, merged);
  } catch (error) {
    if (error instanceof Reject) throw new Reject('bad_patch', error.message, id);
    throw error;
  }
  return merged as NodeConfig;
}

function opUpdateNode(state: State, raw: Raw): void {
  const id = checkId(raw.id, 'id');
  const patch = raw.patch;
  if (!isInt(raw.expectRev) || !isObject(patch)) bad('update_node 格式不合法');
  const node = needNode(state, id);
  checkRev(state.baseNodeRevs, id, raw.expectRev as number);
  for (const key of Object.keys(patch as Raw)) {
    if (IMMUTABLE_NODE.includes(key)) throw new Reject('immutable_field', `${key} 不可修改`, id);
    if (key !== 'title' && key !== 'config') bad(`不支持的字段：${key}`, id);
  }
  const title = (patch as Raw).title ?? node.title;
  if (typeof title !== 'string' || title.length > LIMITS.TITLE_MAX) bad('标题长度超限', id);
  node.config = mergeConfig(node, (patch as Raw).config, id);
  node.title = title;
  node.rev += 1;
}

function opMoveNode(state: State, raw: Raw): void {
  const id = checkId(raw.id, 'id');
  const position = checkPosition(raw.position);
  needNode(state, id).position = position;
}

function opDeleteNode(state: State, raw: Raw): void {
  const id = checkId(raw.id, 'id');
  if (!isInt(raw.expectRev)) bad('delete_node 格式不合法');
  const node = needNode(state, id);
  if (node.kind === 'start' || node.kind === 'end') throw new Reject('protected_node', '起点与终点不可删除', id);
  checkRev(state.baseNodeRevs, id, raw.expectRev as number);
  state.nodes.delete(id);
  for (const [edgeId, edge] of [...state.edges]) {
    if (edge.source === id || edge.target === id) state.edges.delete(edgeId);
  }
}

function checkEndpoints(state: State, source: string, target: string): void {
  const src = needNode(state, source);
  const dst = needNode(state, target);
  if (source === target) throw new Reject('invalid_edge', '不能连接到自身', source);
  if (src.kind === 'note' || dst.kind === 'note') {
    throw new Reject('invalid_edge', '注释节点不能连线', src.kind === 'note' ? source : target);
  }
  if (dst.kind === 'start') throw new Reject('invalid_edge', '起点不能有入边', target);
  if (src.kind === 'end') throw new Reject('invalid_edge', '终点不能有出边', source);
}

function opAddEdge(state: State, raw: Raw): void {
  const edge = parseEdge(raw.edge);
  if (state.edges.has(edge.id)) throw new Reject('duplicate_id', `连线 id 重复：${edge.id}`, edge.id);
  if (state.edges.size >= LIMITS.MAX_EDGES) throw new Reject('limit_exceeded', '连线数量已达上限', edge.id);
  checkEndpoints(state, edge.source, edge.target);
  const duplicate = [...state.edges.values()].some((e) => e.source === edge.source && e.target === edge.target);
  if (duplicate) throw new Reject('invalid_edge', '连线已存在', edge.id);
  if (reaches(state.edges.values(), edge.target, edge.source)) {
    throw new Reject('invalid_edge', '连线会形成环', edge.id);
  }
  edge.rev = 1;
  state.edges.set(edge.id, edge);
  state.baseEdgeRevs.set(edge.id, 1);
}

function opUpdateEdge(state: State, raw: Raw): void {
  const id = checkId(raw.id, 'id');
  const patch = raw.patch;
  if (!isInt(raw.expectRev) || !isObject(patch)) bad('update_edge 格式不合法');
  const edge = needEdge(state, id);
  checkRev(state.baseEdgeRevs, id, raw.expectRev as number);
  for (const key of Object.keys(patch as Raw)) {
    if (IMMUTABLE_EDGE.includes(key)) throw new Reject('immutable_field', `${key} 不可修改`, id);
    if (key !== 'label') bad(`不支持的字段：${key}`, id);
  }
  const label = (patch as Raw).label ?? (Object.hasOwn(patch as Raw, 'label') ? '' : edge.label);
  if (typeof label !== 'string' || label.length > LIMITS.EDGE_LABEL_MAX) bad('标签长度超限', id);
  edge.label = label;
  edge.rev += 1;
}

function opDeleteEdge(state: State, raw: Raw): void {
  const id = checkId(raw.id, 'id');
  if (!isInt(raw.expectRev)) bad('delete_edge 格式不合法');
  needEdge(state, id);
  checkRev(state.baseEdgeRevs, id, raw.expectRev as number);
  state.edges.delete(id);
}

function setPolicyKey(policy: Raw, key: string, value: unknown): void {
  if (key === 'progressMarkers') {
    if (value !== null && typeof value !== 'boolean') bad('progressMarkers 须为布尔值');
    policy[key] = value ?? true;
    return;
  }
  if (value !== null && !isInt(value)) bad(`${key} 须为整数或 null`);
  policy[key] = value;
}

function opSetPolicy(state: State, raw: Raw): void {
  if (!isObject(raw.patch)) bad('set_policy 格式不合法');
  for (const [key, value] of Object.entries(raw.patch as Raw)) {
    if (!POLICY_KEYS.includes(key)) bad(`不支持的策略项：${key}`);
    setPolicyKey(state.policy as unknown as Raw, key, value);
  }
}

function opSetViewport(state: State, raw: Raw): void {
  const view = raw.viewport;
  if (!isObject(view)) bad('viewport 须为对象');
  const { x, y, zoom } = view as Raw;
  const zoomOk = isFiniteNumber(zoom) && zoom >= LIMITS.ZOOM_MIN && zoom <= LIMITS.ZOOM_MAX;
  if (!isFiniteNumber(x) || !isFiniteNumber(y) || !zoomOk) bad('viewport 取值非法');
  state.viewport = { x: x as number, y: y as number, zoom: zoom as number };
}

const HANDLERS: Record<string, Handler> = {
  add_node: opAddNode,
  update_node: opUpdateNode,
  move_node: opMoveNode,
  delete_node: opDeleteNode,
  add_edge: opAddEdge,
  update_edge: opUpdateEdge,
  delete_edge: opDeleteEdge,
  set_policy: opSetPolicy,
  set_viewport: opSetViewport,
};

// ── 入口 ─────────────────────────────────────────────────────────────────────

const FIELDS_BY_OP: Record<string, readonly string[]> = {
  add_node: ['op', 'node'],
  update_node: ['op', 'id', 'expectRev', 'patch'],
  move_node: ['op', 'id', 'position'],
  delete_node: ['op', 'id', 'expectRev'],
  add_edge: ['op', 'edge'],
  update_edge: ['op', 'id', 'expectRev', 'patch'],
  delete_edge: ['op', 'id', 'expectRev'],
  set_policy: ['op', 'patch'],
  set_viewport: ['op', 'viewport'],
};

function runOp(state: State, raw: unknown): string {
  const name = isObject(raw) ? raw.op : undefined;
  if (typeof name !== 'string' || !HANDLERS[name]) bad(`未知操作：${String(name)}`);
  const op = raw as Raw;
  checkKeys(op, FIELDS_BY_OP[name as string] ?? [], name as string);
  (HANDLERS[name as string] as Handler)(state, op);
  return name as string;
}

function failure(index: number, error: Reject): ApplyResult {
  const detail: OpFailure = { opIndex: index, reason: error.reason, message: error.message, entityId: error.entityId };
  return { ok: false, failure: detail, conflicts: error.conflict ? [error.conflict] : [] };
}

/** 在副本上依次应用 `ops`，整批原子：任一失败则整批不生效，返回首个失败的下标与原因。 */
export function applyOps(doc: WorkflowDoc, ops: readonly (WorkflowOp | Record<string, unknown>)[]): ApplyResult {
  const state = initialState(doc);
  let structural = false;
  for (const [index, raw] of ops.entries()) {
    try {
      const name = runOp(state, raw);
      if (GROWING_OPS.has(name) && docBytes(state) > LIMITS.MAX_DOC_BYTES) {
        throw new Reject('limit_exceeded', '流程文档体积超出上限');
      }
      structural = structural || !NON_STRUCTURAL_OPS.has(name);
    } catch (error) {
      if (error instanceof Reject) return failure(index, error);
      throw error;
    }
  }
  return { ok: true, doc: snapshot(state), structural };
}

// ── 逆操作 / 差分 ────────────────────────────────────────────────────────────

const sameJson = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

function configPatch(from: NodeConfig, to: NodeConfig): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  const source = from as Raw;
  const target = to as Raw;
  for (const key of new Set([...Object.keys(source), ...Object.keys(target)])) {
    if (!(key in target)) patch[key] = null;
    else if (!sameJson(source[key], target[key])) patch[key] = target[key];
  }
  return patch;
}

function nodeOps(from: WorkflowDoc, to: WorkflowDoc): WorkflowOp[] {
  const before = new Map(to.nodes.map((n) => [n.id, n]));
  const ops: WorkflowOp[] = [];
  for (const node of from.nodes) {
    if (!before.has(node.id)) ops.push({ op: 'delete_node', id: node.id, expectRev: node.rev });
  }
  for (const node of to.nodes) {
    if (!from.nodes.some((n) => n.id === node.id)) ops.push({ op: 'add_node', node: structuredClone(node) });
  }
  for (const current of from.nodes) {
    const target = before.get(current.id);
    if (!target) continue;
    const config = configPatch(current.config, target.config);
    const patch: { title?: string; config?: Record<string, unknown> } = {};
    if (current.title !== target.title) patch.title = target.title;
    if (Object.keys(config).length > 0) patch.config = config;
    if (Object.keys(patch).length > 0) {
      ops.push({ op: 'update_node', id: current.id, expectRev: current.rev, patch } as WorkflowOp);
    }
    if (!sameJson(current.position, target.position)) {
      ops.push({ op: 'move_node', id: current.id, position: { ...target.position } });
    }
  }
  return ops;
}

function edgeOps(from: WorkflowDoc, to: WorkflowDoc): WorkflowOp[] {
  const target = new Map(to.edges.map((e) => [e.id, e]));
  const present = new Set(from.edges.map((e) => e.id));
  const removals: WorkflowOp[] = [];
  const edits: WorkflowOp[] = [];
  for (const edge of from.edges) {
    const wanted = target.get(edge.id);
    if (!wanted) removals.push({ op: 'delete_edge', id: edge.id, expectRev: edge.rev });
    else if (wanted.label !== edge.label) {
      edits.push({ op: 'update_edge', id: edge.id, expectRev: edge.rev, patch: { label: wanted.label } });
    }
  }
  const additions: WorkflowOp[] = to.edges
    .filter((edge) => !present.has(edge.id))
    .map((edge) => ({ op: 'add_edge', edge: structuredClone(edge) }));
  return [...removals, ...edits, ...additions];
}

function policyOps(from: WorkflowDoc, to: WorkflowDoc): WorkflowOp[] {
  const patch: Record<string, unknown> = {};
  for (const key of POLICY_KEYS) {
    const a = (from.policy as unknown as Raw)[key];
    const b = (to.policy as unknown as Raw)[key];
    if (a !== b) patch[key] = b;
  }
  return Object.keys(patch).length > 0 ? [{ op: 'set_policy', patch } as WorkflowOp] : [];
}

/**
 * 把 `from` 变成 `to` 的一批 op（`expectRev` 取 `from` 的当前 rev）。视口不参与。
 * 顺序：先删连线与节点，再加节点，再改节点，最后改/加连线，保证中间态始终合法。
 */
export function diffOps(from: WorkflowDoc, to: WorkflowDoc): WorkflowOp[] {
  const edges = edgeOps(from, to);
  const removals = edges.filter((op) => op.op === 'delete_edge');
  const rest = edges.filter((op) => op.op !== 'delete_edge');
  return [...removals, ...nodeOps(from, to), ...rest, ...policyOps(from, to)];
}

/**
 * 撤销用的逆 op：从**应用前**的文档 `before` 与这批 `ops` 算出把结果还原成 `before` 的一批 op。
 * 撤销本身是一次普通写入（走原子应用与冲突检测）。`ops` 在 `before` 上不可应用时返回空数组。
 */
export function invertOps(before: WorkflowDoc, ops: readonly WorkflowOp[]): WorkflowOp[] {
  const result = applyOps(before, ops);
  return result.ok ? diffOps(result.doc, before) : [];
}

// ── 工厂 ─────────────────────────────────────────────────────────────────────

const BASE36 = '0123456789abcdefghijklmnopqrstuvwxyz';

function randomSuffix(): string {
  const bytes = new Uint8Array(8);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => BASE36.charAt(byte % 36)).join('');
}

export const newNodeId = (): string => `n_${randomSuffix()}`;
export const newEdgeId = (): string => `e_${randomSuffix()}`;

const DEFAULT_TITLES: Record<string, string> = { step: '新步骤', gate: '检查点', note: '注释' };

/** 新节点（step 带可发布的最小配置模板，gate/note 为空配置）。 */
export function makeNode(kind: 'step' | 'gate' | 'note', position: Position, title?: string): WorkflowNode {
  const config: NodeConfig = kind === 'step' ? { role: 'analysis', effort: 'normal' } : {};
  return { id: newNodeId(), kind, title: title ?? DEFAULT_TITLES[kind] ?? '节点', position, rev: 1, config };
}

export function makeEdge(source: string, target: string, label = ''): WorkflowEdge {
  return { id: newEdgeId(), source, target, label, rev: 1 };
}

/** 复制节点（新 id、整体平移，不复制连线）。 */
export function cloneNode(node: WorkflowNode, offset: Position = { x: 40, y: 40 }): WorkflowNode {
  return {
    ...structuredClone(node),
    id: newNodeId(),
    rev: 1,
    position: { x: node.position.x + offset.x, y: node.position.y + offset.y },
  };
}

// ── 查询辅助（FB/FC 共用）────────────────────────────────────────────────────

export function nodeById(doc: WorkflowDoc, id: string): WorkflowNode | undefined {
  return doc.nodes.find((node) => node.id === id);
}

export function edgeById(doc: WorkflowDoc, id: string): WorkflowEdge | undefined {
  return doc.edges.find((edge) => edge.id === id);
}

export function countByKind(doc: WorkflowDoc): { steps: number; gates: number } {
  return {
    steps: doc.nodes.filter((node) => node.kind === 'step').length,
    gates: doc.nodes.filter((node) => node.kind === 'gate').length,
  };
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (typeof value !== 'object' || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Raw)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, inner]) => [key, canonical(inner)]),
  );
}

/** 内容哈希无关的"结构摘要"：用于测试比较撤销后与撤销前是否内容相等（忽略 rev / viewport / 键序）。 */
export function contentSignature(doc: WorkflowDoc): string {
  const nodes = doc.nodes
    .filter((node) => node.kind !== 'note')
    .map((node) => ({ id: node.id, kind: node.kind, title: node.title, config: node.config }))
    .sort((a, b) => a.id.localeCompare(b.id));
  const edges = doc.edges
    .map((edge) => ({ source: edge.source, target: edge.target, label: edge.label }))
    .sort((a, b) => `${a.source}>${a.target}`.localeCompare(`${b.source}>${b.target}`));
  return JSON.stringify(canonical({ nodes, edges, policy: doc.policy }));
}
