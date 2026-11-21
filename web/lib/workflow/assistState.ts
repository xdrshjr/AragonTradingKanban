/**
 * AI 助手面板的**纯**状态机（spec §3.8.6 / §3.10.7）。
 *
 * 与 `useAssistStream.ts`（SSE + 副作用）分开：reducer 纯函数，测试可以用脚本化事件序列驱动。
 * 服务端在 `assist.ops` 之前**已经**把整批 op 原子写进了草稿——这里只做展示状态，
 * 绝不会把这些 op 再发一遍。
 */

import type { AssistSummary, ChatMessage, ValidationReport, WorkflowOp } from './types';

export type AssistEventType =
  | 'assist.start'
  | 'assist.text'
  | 'assist.round'
  | 'assist.ops'
  | 'assist.reply'
  | 'assist.conflict'
  | 'assist.end'
  | 'ping';

export const ASSIST_EVENT_TYPES: readonly AssistEventType[] = [
  'assist.start',
  'assist.text',
  'assist.round',
  'assist.ops',
  'assist.reply',
  'assist.conflict',
  'assist.end',
  'ping',
];

export interface AssistEvent {
  seq: number;
  type: AssistEventType;
  payload: Record<string, unknown>;
}

export interface ValidationCounts {
  errorCount: number;
  warningCount: number;
}

/** 一次 `assist.ops` 的投影：变更卡片所需的全部信息。 */
export interface AppliedChange {
  applied: boolean;
  revision: number | null;
  summary: AssistSummary;
  validation: ValidationCounts | null;
  ops: WorkflowOp[];
  /** 受影响的节点 id（【定位】用）。 */
  affectedIds: string[];
  /** store 给出的撤销句柄；刷新页面后没有，卡片据此隐藏【撤销】。 */
  undoHandle: string | null;
  undone: boolean;
  reason: string | null;
}

export interface AssistUiMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  kind: 'text' | 'conflict' | 'interrupted' | 'error';
  assistId: string | null;
  createdAt: string | null;
  applied: AppliedChange | null;
  openQuestions: string[];
  conflictIds: string[];
  /** 用户消息：用于【重试】/【基于最新画布重试】重发的原文。 */
  retryText: string | null;
}

export interface AssistState {
  messages: AssistUiMessage[];
  busy: boolean;
  assistId: string | null;
  thinking: string;
  round: number;
  /** `409 workflow_assist_unavailable`：输入框禁用并解释。 */
  unavailable: boolean;
  error: string | null;
  loaded: boolean;
  /** `assist.ops` 到达、`assist.reply` 尚未到达时暂存。 */
  pending: AppliedChange | null;
}

export const initialAssistState: AssistState = {
  messages: [],
  busy: false,
  assistId: null,
  thinking: '',
  round: 1,
  unavailable: false,
  error: null,
  loaded: false,
  pending: null,
};

export type AssistAction =
  | { type: 'loaded'; items: ChatMessage[] }
  | { type: 'send'; localId: string; text: string }
  | { type: 'started'; assistId: string }
  | { type: 'event'; event: AssistEvent; undoHandle?: string | null }
  | { type: 'failed'; error: string; unavailable?: boolean }
  | { type: 'cancelled' }
  | { type: 'undone'; messageId: string }
  | { type: 'cleared' };

const MAX_MESSAGES = 100;
const MAX_THINKING = 400;

function clip(messages: AssistUiMessage[]): AssistUiMessage[] {
  return messages.length > MAX_MESSAGES ? messages.slice(messages.length - MAX_MESSAGES) : messages;
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function summaryOf(value: unknown): AssistSummary {
  const raw = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  const num = (key: string) => (typeof raw[key] === 'number' ? (raw[key] as number) : 0);
  return { added: num('added'), updated: num('updated'), deleted: num('deleted'), edges: num('edges') };
}

function validationOf(value: unknown): ValidationCounts | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Partial<ValidationReport>;
  return {
    errorCount: typeof raw.errorCount === 'number' ? raw.errorCount : 0,
    warningCount: typeof raw.warningCount === 'number' ? raw.warningCount : 0,
  };
}

/** 从一批已展开的基本 op 里找出受影响的节点 id（去重，保持出现顺序）。 */
export function affectedNodeIds(ops: readonly WorkflowOp[]): string[] {
  const ids: string[] = [];
  const push = (id: string | undefined) => {
    if (id && !ids.includes(id)) ids.push(id);
  };
  for (const op of ops) {
    if (op.op === 'add_node') push(op.node.id);
    else if (op.op === 'update_node' || op.op === 'move_node') push(op.id);
  }
  return ids;
}

/** `assist.ops` 载荷 → 变更投影（`applied` 此时必为 true：服务端只有应用成功才发它）。 */
export function changeFromOps(payload: Record<string, unknown>, undoHandle: string | null): AppliedChange {
  const ops = Array.isArray(payload.ops) ? (payload.ops as WorkflowOp[]) : [];
  return {
    applied: true,
    revision: typeof payload.revision === 'number' ? payload.revision : null,
    summary: summaryOf(payload.summary),
    validation: validationOf(payload.validation),
    ops,
    affectedIds: affectedNodeIds(ops),
    undoHandle,
    undone: false,
    reason: null,
  };
}

/** 落库的聊天消息 → UI 消息。助手消息的 `applied` 形如 `{ops, summary, baseRevision, revision, applied}`。 */
export function messageFromChat(row: ChatMessage): AssistUiMessage {
  const raw = row.applied;
  let applied: AppliedChange | null = null;
  if (raw && typeof raw === 'object') {
    const ops = Array.isArray(raw.ops) ? (raw.ops as WorkflowOp[]) : [];
    applied = {
      applied: raw.applied === true,
      revision: typeof raw.revision === 'number' ? raw.revision : null,
      summary: summaryOf(raw.summary),
      validation: validationOf(raw.validation),
      ops,
      affectedIds: affectedNodeIds(ops),
      undoHandle: null,
      undone: false,
      reason: typeof raw.reason === 'string' ? raw.reason : null,
    };
  }
  return {
    id: `chat-${row.id}`,
    role: row.role,
    content: row.content,
    kind: 'text',
    assistId: row.assistId,
    createdAt: row.createdAt,
    applied,
    openQuestions: asStringArray(raw?.openQuestions),
    conflictIds: [],
    retryText: row.role === 'user' ? row.content : null,
  };
}

/**
 * 进程重启 / 网络中断会留下"最后一条是用户消息、没有助手回复"：标记为被中断，面板显示【重试】。
 */
export function markInterrupted(messages: AssistUiMessage[]): AssistUiMessage[] {
  const last = messages[messages.length - 1];
  if (!last || last.role !== 'user') return messages;
  const note: AssistUiMessage = {
    id: `interrupted-${last.id}`,
    role: 'assistant',
    content: '',
    kind: 'interrupted',
    assistId: last.assistId,
    createdAt: null,
    applied: null,
    openQuestions: [],
    conflictIds: [],
    retryText: last.content,
  };
  return [...messages, note];
}

function makeMessage(partial: Partial<AssistUiMessage> & Pick<AssistUiMessage, 'id' | 'role'>): AssistUiMessage {
  return {
    content: '',
    kind: 'text',
    assistId: null,
    createdAt: null,
    applied: null,
    openQuestions: [],
    conflictIds: [],
    retryText: null,
    ...partial,
  };
}

function reduceEvent(state: AssistState, event: AssistEvent, undoHandle: string | null): AssistState {
  const { payload } = event;
  switch (event.type) {
    case 'assist.start':
      return { ...state, busy: true, round: 1, thinking: '', pending: null };
    case 'assist.text': {
      const merged = `${state.thinking}${asString(payload.delta)}`;
      return { ...state, thinking: merged.slice(-MAX_THINKING) };
    }
    case 'assist.round':
      return { ...state, round: typeof payload.round === 'number' ? payload.round : 2, thinking: '' };
    case 'assist.ops':
      return { ...state, pending: changeFromOps(payload, undoHandle) };
    case 'assist.reply': {
      const reply = makeMessage({
        id: `a-${asString(payload.messageId) || event.seq}`,
        role: 'assistant',
        content: asString(payload.reply),
        assistId: state.assistId,
        applied: state.pending,
        openQuestions: asStringArray(payload.openQuestions),
      });
      return { ...state, thinking: '', pending: null, messages: clip([...state.messages, reply]) };
    }
    case 'assist.conflict': {
      const lastUser = [...state.messages].reverse().find((m) => m.role === 'user');
      const card = makeMessage({
        id: `c-${state.assistId ?? event.seq}`,
        role: 'assistant',
        kind: 'conflict',
        assistId: state.assistId,
        conflictIds: asStringArray(payload.entityIds),
        retryText: lastUser?.content ?? null,
      });
      return { ...state, thinking: '', pending: null, messages: clip([...state.messages, card]) };
    }
    case 'assist.end':
      return reduceEnd(state, payload);
    default:
      return state;
  }
}

/** `assist.end`：失败且没有任何回复时补一条原因，否则面板会只剩一条悬空的用户消息。 */
function reduceEnd(state: AssistState, payload: Record<string, unknown>): AssistState {
  const base: AssistState = { ...state, busy: false, thinking: '', assistId: null, pending: null };
  const endState = asString(payload.state);
  const replied = state.messages.at(-1)?.role === 'assistant';
  if (replied || endState === 'cancelled') return base;
  const reason = asString(payload.reason) || (endState === 'failed' ? 'failed' : 'default');
  const note = makeMessage({
    id: `e-${state.assistId ?? state.messages.length}`,
    role: 'assistant',
    kind: 'error',
    content: reason,
    assistId: state.assistId,
    applied: { ...changeFromOps({}, null), applied: false, reason },
  });
  return { ...base, messages: clip([...base.messages, note]) };
}

export function assistReducer(state: AssistState, action: AssistAction): AssistState {
  switch (action.type) {
    case 'loaded': {
      const mapped = action.items.map(messageFromChat);
      return { ...state, messages: markInterrupted(mapped), loaded: true };
    }
    case 'send': {
      // 重试时去掉尾部"被中断/冲突/错误"的提示卡，避免对话里堆叠过期提示。
      const kept = state.messages.filter((m) => m.kind === 'text' || m.kind === 'conflict');
      const user = makeMessage({ id: action.localId, role: 'user', content: action.text, retryText: action.text });
      return { ...state, error: null, busy: true, thinking: '', round: 1, messages: clip([...kept, user]) };
    }
    case 'started':
      return { ...state, assistId: action.assistId };
    case 'event':
      return reduceEvent(state, action.event, action.undoHandle ?? null);
    case 'failed':
      return {
        ...state,
        busy: false,
        thinking: '',
        assistId: null,
        error: action.error,
        unavailable: action.unavailable ? true : state.unavailable,
      };
    case 'cancelled': {
      return { ...state, busy: false, thinking: '', assistId: null, pending: null };
    }
    case 'undone':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === action.messageId && m.applied ? { ...m, applied: { ...m.applied, undone: true } } : m,
        ),
      };
    case 'cleared':
      return { ...initialAssistState, loaded: true, unavailable: state.unavailable };
    default:
      return state;
  }
}
