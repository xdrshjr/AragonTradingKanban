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
    let payload: Record<string, unknown> = {};
    try {
      payload = raw.data ? (JSON.parse(raw.data) as Record<string, unknown>) : {};
    } catch {
      payload = { raw: raw.data };
    }
    // 按 seq 去重：重连时服务端会从 `after` 重放，同一帧可能来两次。
    if (seq > 0 && seq <= after) return;
    if (seq > after) after = seq;
    onEvent({ seq, type, payload, at: Date.now() });
  };

  const connect = async () => {
    if (closed) return;
    let token = '';
    try {
      const issued = await api.askEventsToken(askId);
      token = issued.token;
    } catch {
      // 未启用鉴权时拿不到 token 也能连：中间件只在 ?token= 存在时才校验。
      token = '';
    }
    if (closed) return;

    source?.close();
    source = new EventSource(buildAskEventsUrl(askId, token, after));

    source.onopen = () => {
      attempt = 0;
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

    source.onerror = () => {
      if (closed) return;
      source?.close();
      source = null;
      if (attempt >= maxRetries) {
        onError?.(new Error(copy.ask.failed), 0);
        close();
        return;
      }
      const delay = retryDelay(attempt);
      attempt += 1;
      onReconnect?.(attempt);
      onError?.(new Error(copy.ask.thinking), delay);
      retryTimer = setTimeout(() => {
        void connect();
      }, delay);
    };
  };

  void connect();

  return close;
}

/* ── 状态机 ──────────────────────────────────────────────────────────────── */

export type AskPhase = 'idle' | 'submitting' | 'streaming' | 'cancelling' | 'done' | 'error' | 'cancelled';

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
  | { type: 'submit'; mode: AskMode; question: string }
  | { type: 'queued'; askId: string }
  | { type: 'settled'; result: AskOut }
  | { type: 'exception'; code: string; message: string }
  | { type: 'quota'; limit: number; retryAfterMs: number | null }
  | { type: 'confirmFallback' }
  | { type: 'declineFallback' }
  | { type: 'cancelling' }
  | { type: 'cancelled' }
  | { type: 'reconnect'; attempt: number }
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
  return 'done';
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
    grounded: payload.grounded !== false,
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
      return { ...state, phase: 'streaming', askId: action.askId, reconnectAttempt: 0 };

    case 'settled': {
      const phase = phaseForResult(action.result);
      const consumed = state.mode === 'deep' && phase === 'done';
      return {
        ...state,
        phase,
        askId: action.result.id || state.askId,
        result: action.result,
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
        phase: 'idle',
        quota: { limit: action.limit, retryAfterMs: action.retryAfterMs },
      };

    case 'confirmFallback':
      return { ...state, quota: null, mode: 'cite' };

    case 'declineFallback':
      return { ...state, quota: null, phase: state.result ? phaseForResult(state.result) : 'idle' };

    case 'cancelling':
      return { ...state, phase: 'cancelling' };

    case 'cancelled':
      return { ...state, phase: 'cancelled', askId: null };

    case 'reconnect':
      return { ...state, reconnectAttempt: action.attempt };

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
          return { ...state, phase: 'done', result: syntheticResult(state, payload), error: null };

        case 'ask.error': {
          const code = stringOrNull(payload.code) ?? 'internal';
          return {
            ...state,
            phase: code === 'cancelled' ? 'cancelled' : 'error',
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
  /** `limits.askDailyLimit`，只用于文案，不做前端额度判定。 */
  dailyLimit?: number;
  /** `capabilities.features.askDeep`；false（mock 模式）时强制 cite。 */
  deepAvailable?: boolean;
  onDone?: (result: AskOut) => void;
}

export interface AskSubmitInput {
  question?: string;
  mode?: AskMode;
}

export interface UseAskStreamResult {
  state: AskStreamState;
  setQuestion: (question: string) => void;
  setMode: (mode: AskMode) => void;
  submit: (input?: AskSubmitInput) => Promise<void>;
  /** AC-27 的「改用引用检索」：确认后才真正以 cite 重发。 */
  confirmFallback: () => Promise<void>;
  declineFallback: () => void;
  cancel: () => Promise<void>;
  reset: () => void;
  isActive: boolean;
  deepRemaining: number;
}

function isQueued(value: AskOut | AskQueuedResponse): value is AskQueuedResponse {
  return 'askId' in value && typeof value.askId === 'string';
}

export function useAskStream(options: UseAskStreamOptions): UseAskStreamResult {
  const { reportId, dailyLimit = 0, deepAvailable = true, onDone } = options;
  const [state, dispatch] = useReducer(askStreamReducer, initialAskStreamState);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const onDoneRef = useRef(onDone);

  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  /**
   * 每次提交/重置都 +1：`await` 之后回来的请求如果发现代号变了，就必须放弃结果。
   * 没有它，一个在组件卸载或换问题之后才解析的 `api.ask(...)` 会留下一条没人
   * 关闭的 EventSource（连带重连定时器）。
   */
  const generationRef = useRef(0);
  /** 最新的 `state.askId`，供异步回调判断"这条流还是不是当前这条"。 */
  const askIdRef = useRef<string | null>(null);
  useEffect(() => {
    askIdRef.current = state.askId;
  }, [state.askId]);

  const teardown = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);

  useEffect(
    () => () => {
      // 卸载即作废：在途请求回来后不得再订阅。
      generationRef.current += 1;
      teardown();
    },
    [teardown],
  );

  // 换报告就重置：上一份报告的答案不能留在新报告的面板上（包括额度确认态）。
  useEffect(() => {
    generationRef.current += 1;
    teardown();
    dispatch({ type: 'reset', reportId });
  }, [reportId, teardown]);

  // mock 模式没有 deep 能力：把已有选择退回去，避免发一个必被拒的请求。
  useEffect(() => {
    if (!deepAvailable) dispatch({ type: 'mode', mode: 'cite' });
  }, [deepAvailable]);

  /** 拿到 `ask.done` 后再取一次完整 AskOut：SSE 只带摘要，正文/命中块在库里。 */
  const refresh = useCallback(async (askId: string) => {
    try {
      const result = await api.askById(askId);
      // 迟到的结果不许覆盖新问题：只有"当前这条追问"才允许落状态。
      if (askIdRef.current !== askId) return;
      dispatch({ type: 'settled', result });
      onDoneRef.current?.(result);
    } catch {
      // 拉不到就保留 syntheticResult——流里的终态已经足够渲染。
    }
  }, []);

  const streamOptions = useCallback(
    (askId: string) => ({
      onEvent: (event: AskStreamEvent) => {
        dispatch({ type: 'event', event });
        if (event.type === 'ask.done') void refresh(askId);
      },
      onReconnect: (attempt: number) => dispatch({ type: 'reconnect', attempt }),
      onError: (error: Error, retryInMs: number) => {
        if (retryInMs === 0) dispatch({ type: 'exception', code: 'stream_closed', message: error.message });
      },
    }),
    [refresh],
  );

  const submit = useCallback(
    async (input: AskSubmitInput = {}) => {
      const question = (input.question ?? state.question).trim();
      if (question.length === 0) return;
      const requested = input.mode ?? state.mode;
      const mode: AskMode = deepAvailable ? requested : 'cite';

      teardown();
      const generation = (generationRef.current += 1);
      dispatch({ type: 'submit', mode, question });

      try {
        const created = await api.ask(reportId, { question, mode });
        // 请求回来时如果已经换了问题 / 卸载了组件，这条流不能再订阅。
        if (generationRef.current !== generation) return;
        if (isQueued(created)) {
          dispatch({ type: 'queued', askId: created.askId });
          const unsubscribe = subscribeToAsk(created.askId, streamOptions(created.askId));
          if (generationRef.current !== generation) {
            unsubscribe();
            return;
          }
          unsubscribeRef.current = unsubscribe;
          return;
        }
        dispatch({ type: 'settled', result: created });
        onDoneRef.current?.(created);
      } catch (error) {
        // AC-27：429 不静默兜底，把「改用引用检索」变成一次显式确认。
        if (error instanceof ApiError && error.code === 'too_many_asks') {
          dispatch({ type: 'quota', limit: dailyLimit, retryAfterMs: error.retryAfterMs });
          return;
        }
        dispatch({
          type: 'exception',
          code: error instanceof ApiError ? error.code : 'unknown',
          message: error instanceof Error && error.message ? error.message : copy.ask.failed,
        });
      }
    },
    [dailyLimit, deepAvailable, reportId, state.mode, state.question, streamOptions, teardown],
  );

  const confirmFallback = useCallback(async () => {
    dispatch({ type: 'confirmFallback' });
    await submit({ mode: 'cite' });
  }, [submit]);

  const declineFallback = useCallback(() => {
    dispatch({ type: 'declineFallback' });
  }, []);

  const cancel = useCallback(async () => {
    const askId = state.askId;
    if (!askId) return;
    dispatch({ type: 'cancelling' });
    try {
      await api.cancelAsk(askId);
    } catch {
      // 可能已经终态；流会给出结论。
    }
    teardown();
    dispatch({ type: 'cancelled' });
  }, [state.askId, teardown]);

  const reset = useCallback(() => {
    generationRef.current += 1;
    teardown();
    dispatch({ type: 'reset', reportId });
  }, [reportId, teardown]);

  const setQuestion = useCallback((question: string) => dispatch({ type: 'question', question }), []);

  const setMode = useCallback(
    (mode: AskMode) => {
      if (mode === 'deep' && !deepAvailable) return;
      dispatch({ type: 'mode', mode });
    },
    [deepAvailable],
  );

  const isActive = state.phase === 'submitting' || state.phase === 'streaming' || state.phase === 'cancelling';

  return {
    state,
    setQuestion,
    setMode,
    submit,
    confirmFallback,
    declineFallback,
    cancel,
    reset,
    isActive,
    deepRemaining: Math.max(0, dailyLimit - state.deepUsed),
  };
}
