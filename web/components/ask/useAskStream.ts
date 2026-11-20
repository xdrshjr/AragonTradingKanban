'use client';

import { useCallback, useEffect, useReducer, useRef } from 'react';

import { ApiError, api, sseBase } from '@/lib/api';
import { copy } from '@/lib/copy';
import { retryDelay } from '@/lib/sse';
import type { AskMode, AskOut, AskQueuedResponse, Citation } from '@/lib/types';

/**
 * 追问（ask）的客户端状态机 + SSE 客户端（spec §18.3.2 / §18.5.1 / §18.5.2 / AC-26 / AC-27）。
 *
 * 两档共用一条流水线：
 *   cite → 200 AskOut（毫秒级、零 token），直接渲染命中块原文；
 *   deep → 202 AskQueuedResponse → 订阅 `ask.*` 事件流 → 终态再取一次 AskOut。
 *
 * `lib/sse.ts` 的 `subscribeToRun` 是 run 专属的（URL、事件名都绑在 run 上），
 * 照它的结构写一份 ask 版本，而不是给它加分支——两条流的生命周期互不相干。
 */

export type AskStreamEventType = 'ask.stage' | 'ask.delta' | 'ask.usage' | 'ask.done' | 'ask.error';

export interface AskStreamEvent {
  seq: number;
  type: AskStreamEventType;
  payload: Record<string, unknown>;
  at: number;
}

export interface AskStreamOptions {
  after?: number;
  onEvent: (event: AskStreamEvent) => void;
  onOpen?: () => void;
  onError?: (error: Error, retryInMs: number) => void;
  onReconnect?: (attempt: number) => void;
  /** 与 run 流一致：3 次指数退避后放弃。 */
  maxRetries?: number;
}

const ASK_EVENT_TYPES: readonly AskStreamEventType[] = [
  'ask.stage',
  'ask.delta',
  'ask.usage',
  'ask.done',
  'ask.error',
];

const DEFAULT_MAX_RETRIES = 3;
const MAX_STREAM_TEXT = 4000;

export function buildAskEventsUrl(askId: string, token: string, after: number): string {
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  if (after > 0) params.set('after', String(after));
  const query = params.toString();
  return `${sseBase()}/api/v1/asks/${encodeURIComponent(askId)}/events${query ? `?${query}` : ''}`;
}

/** 返回退订函数；`ask.done` / `ask.error` 是终态，收到即关流。 */
export function subscribeToAsk(askId: string, options: AskStreamOptions): () => void {
  const { onEvent, onOpen, onError, onReconnect, maxRetries = DEFAULT_MAX_RETRIES } = options;
  let after = options.after ?? 0;
  let attempt = 0;
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const close = () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    source?.close();
    source = null;
  };

  const parse = (raw: MessageEvent<string>, type: AskStreamEventType, seq: number) => {
    if (closed) return;
    let payload: Record<string, unknown> = {};
    try {
      payload = raw.data ? (JSON.parse(raw.data) as Record<string, unknown>) : {};
    } catch {
      payload = { raw: raw.data };
    }
    // 按 seq 去重：重连时服务端会从 `after` 重放，同一帧可能来两次。
    if (seq > 0 && seq <= after) return;
    if (seq > after) {
      after = seq;
      attempt = 0;
    }
    onEvent({ seq, type, payload, at: Date.now() });
  };

  const connect = async () => {
    if (closed) return;
    let token: string;
    try {
      const issued = await api.askEventsToken(askId);
      token = issued.token;
    } catch (error) {
      if (closed) return;
      if (error instanceof ApiError && [401, 403].includes(error.status)) {
        close();
        onError?.(error, 0);
        return;
      }
      retry(error instanceof Error ? error : new Error(copy.ask.failed));
      return;
    }
    if (closed) return;

    source?.close();
    source = new EventSource(buildAskEventsUrl(askId, token, after));

    source.onopen = () => {
      onOpen?.();
    };

    for (const type of ASK_EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        const message = event as MessageEvent<string>;
        const seq = Number.parseInt(message.lastEventId || '0', 10) || after;
        parse(message, type, seq);
        if (type === 'ask.done' || type === 'ask.error') close();
      });
    }

    source.onerror = () => retry(new Error(copy.ask.failed));
  };

  const retry = (error: Error) => {
    if (closed) return;
    source?.close();
    source = null;
    if (attempt >= maxRetries) {
      close();
      onError?.(error, 0);
      return;
    }
    const delay = retryDelay(attempt++);
    onReconnect?.(attempt);
    onError?.(error, delay);
    retryTimer = setTimeout(() => void connect(), delay);
  };

  void connect();

  return close;
}

/* ── 状态机 ──────────────────────────────────────────────────────────────── */

export type AskPhase =
  | 'idle' | 'submitting' | 'streaming' | 'cancelling'
  | 'verifying' | 'disconnected' | 'unverified' | 'submission-unknown'
  | 'done' | 'error' | 'cancelled';

/** Uncertain acceptance and unverified results retain the paid-request lock. */
export function isAskActive(phase: AskPhase): boolean {
  return !['idle', 'done', 'error', 'cancelled'].includes(phase);
}

export interface AskStageView {
  stage: string;
  pct: number;
  label: string;
}

export interface AskUsageView {
  turns: number;
  toolCalls: number;
  elapsedMs: number;
}

/** 429 `too_many_asks` 的显式确认态：先问，再切档（AC-27）。 */
export interface AskQuotaAlert {
  limit: number;
  retryAfterMs: number | null;
}

export interface AskStreamState {
  phase: AskPhase;
  mode: AskMode;
  question: string;
  contextAskIds: string[];
  reportId: string;
  askId: string | null;
  result: AskOut | null;
  stage: AskStageView | null;
  streamText: string;
  usage: AskUsageView | null;
  error: { code: string; message: string } | null;
  quota: AskQuotaAlert | null;
  reconnectAttempt: number;
  /** 本会话已消耗的深度追问次数，用于「今日剩余」文案。 */
  deepUsed: number;
}

export const initialAskStreamState: AskStreamState = {
  phase: 'idle',
  mode: 'cite',
  question: '',
  contextAskIds: [],
  reportId: '',
  askId: null,
  result: null,
  stage: null,
  streamText: '',
  usage: null,
  error: null,
  quota: null,
  reconnectAttempt: 0,
  deepUsed: 0,
};

export type AskStreamAction =
  | { type: 'reset'; reportId: string }
  | { type: 'mode'; mode: AskMode }
  | { type: 'question'; question: string }
  | { type: 'submit'; mode: AskMode; question: string; contextAskIds?: string[] }
  | { type: 'queued'; askId: string; contextAskIds?: string[] }
  | { type: 'settled'; result: AskOut }
  | { type: 'exception'; code: string; message: string }
  | { type: 'quota'; limit: number; retryAfterMs: number | null;
      code?: string; message?: string }
  | { type: 'confirmFallback' }
  | { type: 'declineFallback' }
  | { type: 'cancelling' }
  | { type: 'cancelled' }
  | { type: 'reconnect'; attempt: number }
  | { type: 'phase'; phase: AskPhase; error?: AskStreamState['error'] }
  | { type: 'event'; event: AskStreamEvent };

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/** SSE 的 `citations` 与 REST 同形，逐条校验后再进渲染层——UI 不做二次猜测。 */
export function toCitations(value: unknown): Citation[] {
  if (!Array.isArray(value)) return [];
  const out: Citation[] = [];
  for (const item of value) {
    if (typeof item === 'string' && item.length > 0) {
      out.push({ sourceId: item });
      continue;
    }
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      const sourceId = stringOrNull(record.sourceId) ?? stringOrNull(record.id);
      if (!sourceId) continue;
      out.push({
        sourceId,
        title: stringOrNull(record.title),
        publisher: stringOrNull(record.publisher),
      });
    }
  }
  return out;
}

function phaseForResult(result: AskOut): AskPhase {
  if (result.status === 'cancelled') return 'cancelled';
  if (result.status === 'failed') return 'error';
  if (result.status === 'done') return 'done';
  return result.status === 'cancelling' ? 'cancelling' : 'streaming';
}

function resultError(result: AskOut): { code: string; message: string } | null {
  if (result.status !== 'failed' && result.status !== 'cancelled') return null;
  return {
    code: result.errorCode ?? result.status,
    message: result.errorMessage ?? (result.status === 'cancelled' ? copy.ask.cancelled : copy.ask.failed),
  };
}

/** `ask.done` 只带终态摘要，深度答案的正文要靠 `answer.answerText`；先用流里的增量兜底。 */
function syntheticResult(state: AskStreamState, payload: Record<string, unknown>): AskOut {
  return {
    id: state.askId ?? '',
    reportId: state.reportId,
    question: state.question,
    mode: state.mode,
    status: 'done',
    grounded: false,
    contextAskIds: state.contextAskIds,
    confidence: numberOrNull(payload.confidence),
    citationsDropped: numberOrNull(payload.citationsDropped) ?? 0,
    answer: {
      answerText: state.streamText.length > 0 ? state.streamText : null,
      hits: [],
      citations: toCitations(payload.citations),
      hint: null,
    },
    costUsd: numberOrNull(payload.costUsd),
    costKnown: Boolean(payload.costKnown),
    tokensIn: null,
    tokensOut: null,
    latencyMs: null,
    errorCode: null,
    errorMessage: null,
    createdAt: new Date().toISOString(),
    finishedAt: null,
  };
}

export function askStreamReducer(state: AskStreamState, action: AskStreamAction): AskStreamState {
  switch (action.type) {
    case 'reset':
      return { ...initialAskStreamState, reportId: action.reportId, deepUsed: state.deepUsed };

    case 'mode':
      return { ...state, mode: action.mode };

    case 'question':
      return { ...state, question: action.question };

    case 'submit':
      return {
        ...state,
        phase: 'submitting',
        mode: action.mode,
        question: action.question,
        contextAskIds: action.contextAskIds ?? [],
        askId: null,
        result: null,
        stage: null,
        streamText: '',
        usage: null,
        error: null,
        quota: null,
        reconnectAttempt: 0,
      };

    case 'queued':
      return {
        ...state, phase: 'streaming', askId: action.askId,
        contextAskIds: action.contextAskIds ?? state.contextAskIds,
        reconnectAttempt: 0,
      };

    case 'settled': {
      const phase = phaseForResult(action.result);
      const consumed = state.mode === 'deep' && phase === 'done'
        && state.phase !== 'done';
      return {
        ...state,
        phase,
        askId: action.result.id || state.askId,
        result: action.result,
        question: action.result.question,
        mode: action.result.mode,
        contextAskIds: action.result.contextAskIds ?? [],
        error: resultError(action.result),
        quota: null,
        stage: phase === 'done' ? state.stage : null,
        deepUsed: consumed ? state.deepUsed + 1 : state.deepUsed,
      };
    }

    case 'exception':
      return { ...state, phase: 'error', error: { code: action.code, message: action.message }, quota: null };

    case 'quota':
      // 只提示，不改档：降级必须由用户按下确认（AC-27）。
      return {
        ...state,
        phase: 'error',
        error: { code: action.code ?? 'too_many_asks',
          message: action.message ?? copy.ask.quotaTitle },
        quota: { limit: action.limit, retryAfterMs: action.retryAfterMs },
      };

    case 'confirmFallback':
      return { ...state, quota: null, mode: 'cite' };

    case 'declineFallback':
      return { ...state, quota: null,
        phase: state.result ? phaseForResult(state.result) : 'error' };

    case 'cancelling':
      return { ...state, phase: 'cancelling', error: null };

    case 'cancelled':
      return { ...state, phase: 'cancelled', askId: null };

    case 'reconnect':
      return { ...state, reconnectAttempt: action.attempt };

    case 'phase':
      return {
        ...state, phase: action.phase,
        error: action.error === undefined ? state.error : action.error,
      };

    case 'event': {
      const { event } = action;
      const payload = event.payload ?? {};

      switch (event.type) {
        case 'ask.stage': {
          const pct = numberOrNull(payload.pct) ?? state.stage?.pct ?? 0;
          return {
            ...state,
            phase: 'streaming',
            stage: {
              stage: stringOrNull(payload.stage) ?? state.stage?.stage ?? '',
              // pct 单调（§18.5.2）：重放/乱序都不会让进度条倒退。
              pct: Math.max(state.stage?.pct ?? 0, pct),
              label: stringOrNull(payload.label) ?? state.stage?.label ?? copy.ask.thinking,
            },
          };
        }

        case 'ask.delta': {
          const delta = typeof payload.text === 'string' ? payload.text : '';
          const merged = `${state.streamText}${delta}`;
          return {
            ...state,
            phase: 'streaming',
            streamText: merged.length > MAX_STREAM_TEXT ? merged.slice(merged.length - MAX_STREAM_TEXT) : merged,
          };
        }

        case 'ask.usage':
          return {
            ...state,
            usage: {
              turns: numberOrNull(payload.turns) ?? 0,
              toolCalls: numberOrNull(payload.toolCalls) ?? 0,
              elapsedMs: numberOrNull(payload.elapsedMs) ?? 0,
            },
          };

        case 'ask.done':
          return {
            ...state, phase: 'verifying',
            result: syntheticResult(state, payload), error: null,
          };

        case 'ask.error': {
          const code = stringOrNull(payload.code) ?? 'internal';
          return {
            ...state,
            phase: 'verifying',
            error: { code, message: stringOrNull(payload.message) ?? copy.ask.failed },
          };
        }

        default:
          return state;
      }
    }

    default:
      return state;
  }
}

/* ── hook ────────────────────────────────────────────────────────────────── */

export interface UseAskStreamOptions {
  reportId: string;
  identityKey?: string;
  dailyLimit?: number;
  deepAvailable?: boolean;
  onDone?: (result: AskOut) => void;
  onAuthFailure?: () => void;
}

export interface AskSubmitInput {
  question?: string;
  mode?: AskMode;
  contextAskIds?: string[];
}

export interface UseAskStreamResult {
  state: AskStreamState;
  setQuestion: (question: string) => void;
  setMode: (mode: AskMode) => void;
  submit: (input?: AskSubmitInput) => Promise<void>;
  confirmFallback: () => Promise<void>;
  declineFallback: () => void;
  cancel: () => Promise<void>;
  reset: () => void;
  refresh: () => Promise<void>;
  resume: (askId: string) => Promise<void>;
  reconnect: () => Promise<void>;
  acknowledgeDuplicateRisk: () => void;
  isActive: boolean;
  deepRemaining: number;
}

function isQueued(value: AskOut | AskQueuedResponse): value is AskQueuedResponse {
  return 'askId' in value && typeof value.askId === 'string';
}

/** Owns one paid request and its single EventSource, including uncertain states. */
export function useAskStream(options: UseAskStreamOptions): UseAskStreamResult {
  const { reportId, identityKey = 'open', dailyLimit = 0 } = options;
  const { deepAvailable = true, onDone } = options;
  const [state, dispatch] = useReducer(askStreamReducer, initialAskStreamState);
  const current = useRef(state);
  current.current = state;
  const doneCallback = useRef(onDone);
  doneCallback.current = onDone;
  const authCallback = useRef(options.onAuthFailure);
  authCallback.current = options.onAuthFailure;
  const connection = useRef<(() => void) | null>(null);
  const epoch = useRef(0);
  const requestId = useRef<string | null>(null);
  const locked = useRef(false);
  const cancelPending = useRef(false);
  const snapshot = useRef<AskSubmitInput>({});
  const identity = useRef(`${identityKey}:${reportId}`);
  const nextIdentity = `${identityKey}:${reportId}`;
  if (identity.current !== nextIdentity) {
    identity.current = nextIdentity;
    epoch.current += 1;
    requestId.current = null;
    locked.current = false;
    cancelPending.current = false;
  }

  const teardown = useCallback(() => {
    connection.current?.();
    connection.current = null;
  }, []);
  useEffect(() => {
    epoch.current += 1;
    teardown();
    locked.current = false;
    cancelPending.current = false;
    requestId.current = null;
    dispatch({ type: 'reset', reportId });
    return () => { epoch.current += 1; teardown(); };
  }, [identityKey, reportId, teardown]);
  useEffect(() => {
    if (!deepAvailable) dispatch({ type: 'mode', mode: 'cite' });
  }, [deepAvailable]);

  const readResult = useCallback(async (askId: string, generation: number) => {
    const valid = () => epoch.current === generation
      && requestId.current === askId;
    try {
      const result = await api.askById(askId);
      if (!valid()) return null;
      const active = isAskActive(phaseForResult(result));
      locked.current = active;
      dispatch({ type: 'settled', result });
      if (!active) {
        teardown();
        doneCallback.current?.(result);
      }
      return result;
    } catch (error) {
      if (!valid()) return null;
      if (error instanceof ApiError && [401, 403].includes(error.status)) {
        authCallback.current?.();
      }
      locked.current = true;
      dispatch({
        type: 'phase', phase: 'unverified',
        error: {
          code: error instanceof ApiError ? error.code : 'unverified',
          message: copy.ask.unverified,
        },
      });
      return null;
    }
  }, [teardown]);

  const attach = useCallback((askId: string, generation: number) => {
    teardown();
    const valid = () => epoch.current === generation
      && requestId.current === askId;
    connection.current = subscribeToAsk(askId, {
      onEvent: (event) => {
        if (!valid()) return;
        dispatch({ type: 'event', event });
        if (['ask.done', 'ask.error'].includes(event.type)) {
          void readResult(askId, generation).then((result) => {
            // The stream ended, but storage may still report an active task.
            // Keep recovery visible instead of leaving a closed stream "live".
            if (valid() && result && isAskActive(phaseForResult(result))) {
              dispatch({ type: 'phase', phase: 'disconnected' });
            }
          });
        }
      },
      onReconnect: (attempt) => {
        if (valid()) dispatch({ type: 'reconnect', attempt });
      },
      onError: (error, delay) => {
        if (!valid() || delay > 0) return;
        const authFailed = error instanceof ApiError
          && [401, 403].includes(error.status);
        dispatch({ type: 'phase', phase: 'disconnected', error: {
          code: error instanceof ApiError ? error.code : 'stream_closed',
          message: authFailed ? copy.ask.authRequired : copy.ask.disconnected,
        } });
        if (authFailed) authCallback.current?.();
        if (!authFailed) {
          void readResult(askId, generation).then((result) => {
            if (valid() && result && isAskActive(phaseForResult(result))) {
              dispatch({ type: 'phase', phase: 'disconnected' });
            }
          });
        }
      },
    });
  }, [readResult, teardown]);

  const submit = useCallback(async (input: AskSubmitInput = {}) => {
    if (locked.current) return;
    const question = (input.question ?? current.current.question).trim();
    if (!question) return;
    const mode = deepAvailable ? input.mode ?? current.current.mode : 'cite';
    const contextAskIds = mode === 'cite' ? [] : input.contextAskIds ?? [];
    locked.current = true;
    teardown();
    const generation = ++epoch.current;
    cancelPending.current = false;
    requestId.current = null;
    snapshot.current = { question, mode, contextAskIds: [...contextAskIds] };
    dispatch({ type: 'submit', mode, question, contextAskIds });
    try {
      const created = await api.ask(reportId, { question, mode, contextAskIds });
      if (epoch.current !== generation) return;
      requestId.current = isQueued(created) ? created.askId : created.id;
      if (isQueued(created)) {
        dispatch({ type: 'queued', askId: created.askId,
          contextAskIds: created.contextAskIds ?? [] });
        attach(created.askId, generation);
        return;
      }
      locked.current = isAskActive(phaseForResult(created));
      dispatch({ type: 'settled', result: created });
      if (!locked.current) doneCallback.current?.(created);
    } catch (error) {
      if (epoch.current !== generation) return;
      const rejected = error instanceof ApiError && error.status >= 400
        && error.status < 500 && error.status !== 408;
      locked.current = !rejected;
      if (error instanceof ApiError
        && ['too_many_asks', 'quota_exceeded'].includes(error.code)) {
        dispatch({ type: 'quota', limit: dailyLimit,
          retryAfterMs: error.retryAfterMs,
          code: error.code, message: error.message });
        return;
      }
      dispatch({ type: 'phase', phase: rejected ? 'error' : 'submission-unknown',
        error: { code: error instanceof ApiError ? error.code : 'network_error',
          message: rejected && error instanceof Error ? error.message
            : copy.ask.submissionUnknown } });
    }
  }, [attach, dailyLimit, deepAvailable, reportId, teardown]);

  const resume = useCallback(async (askId: string) => {
    teardown();
    locked.current = true;
    const generation = ++epoch.current;
    cancelPending.current = false;
    requestId.current = askId;
    dispatch({ type: 'submit', question: '', mode: 'deep' });
    dispatch({ type: 'queued', askId });
    dispatch({ type: 'phase', phase: 'verifying' });
    const result = await readResult(askId, generation);
    if (epoch.current !== generation || requestId.current !== askId) return;
    if (result && isAskActive(phaseForResult(result))) attach(askId, generation);
  }, [attach, readResult, teardown]);

  const refresh = useCallback(async () => {
    const askId = requestId.current;
    const generation = epoch.current;
    if (!askId) return;
    const result = await readResult(askId, generation);
    if (epoch.current !== generation || requestId.current !== askId) return;
    if (result && isAskActive(phaseForResult(result))) await resume(askId);
  }, [readResult, resume]);
  const reconnect = useCallback(async () => {
    const askId = requestId.current;
    if (askId) await resume(askId);
  }, [resume]);
  const cancel = useCallback(async () => {
    const askId = requestId.current;
    if (!askId || cancelPending.current) return;
    cancelPending.current = true;
    const generation = epoch.current;
    dispatch({ type: 'cancelling' });
    try {
      await api.cancelAsk(askId);
      if (epoch.current !== generation || requestId.current !== askId) return;
      await readResult(askId, generation);
    } catch (error) {
      if (epoch.current !== generation || requestId.current !== askId) return;
      if (error instanceof ApiError && error.code === 'ask_not_active') {
        await readResult(askId, generation);
        return;
      }
      dispatch({ type: 'phase', phase: 'cancelling', error: {
        code: 'cancel_unconfirmed',
        message: copy.ask.cancelUnconfirmed,
      } });
    } finally {
      if (epoch.current === generation && requestId.current === askId) {
        cancelPending.current = false;
      }
    }
  }, [readResult]);
  const reset = useCallback(() => {
    if (locked.current) return;
    epoch.current += 1;
    requestId.current = null;
    teardown();
    dispatch({ type: 'reset', reportId });
  }, [reportId, teardown]);
  const acknowledgeDuplicateRisk = useCallback(() => {
    if (current.current.phase !== 'submission-unknown') return;
    locked.current = false;
    dispatch({ type: 'phase', phase: 'error', error: {
      code: 'duplicate_risk_acknowledged',
      message: copy.ask.duplicateAcknowledged,
    } });
  }, []);
  const confirmFallback = useCallback(async () => {
    if (!current.current.quota || locked.current) return;
    dispatch({ type: 'confirmFallback' });
    await submit({ ...snapshot.current, mode: 'cite', contextAskIds: [] });
  }, [submit]);
  const declineFallback = useCallback(() => {
    dispatch({ type: 'declineFallback' });
  }, []);
  const setQuestion = useCallback((question: string) => {
    dispatch({ type: 'question', question });
  }, []);
  const setMode = useCallback((mode: AskMode) => {
    if (locked.current || (mode === 'deep' && !deepAvailable)) return;
    dispatch({ type: 'mode', mode });
  }, [deepAvailable]);

  return {
    state, setQuestion, setMode, submit, confirmFallback, declineFallback,
    cancel, reset, refresh, resume, reconnect, acknowledgeDuplicateRisk,
    isActive: isAskActive(state.phase),
    deepRemaining: Math.max(0, dailyLimit - state.deepUsed),
  };
}
