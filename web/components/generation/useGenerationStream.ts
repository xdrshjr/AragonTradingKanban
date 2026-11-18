'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useReducer, useRef } from 'react';

import { ApiError, api } from '@/lib/api';
import { copy, fill } from '@/lib/copy';
import { subscribeToRun, type RunStreamEvent } from '@/lib/sse';
import type { CostSource, RunStageId } from '@/lib/types';

/**
 * Generation state machine (spec §10.2).
 *
 *   idle → submitting → running(stage, pct, logs[], tools[]) → done | error
 *
 * The reducer is pure so the tests can drive it with a scripted event list; the
 * hook only adds the side effects (HTTP, SSE, query invalidation).
 */

export type GenerationPhase = 'idle' | 'submitting' | 'running' | 'done' | 'error' | 'cancelled';

export interface LogLine {
  id: number;
  level: 'info' | 'warn' | 'error';
  message: string;
  at: number;
}

export interface ToolCallView {
  id: number;
  name: string;
  input: string;
  isError: boolean;
  phase: string;
  outputPreview: string;
}

export interface GenerationState {
  phase: GenerationPhase;
  runId: string | null;
  instrumentId: string | null;
  stage: RunStageId | null;
  stageLabel: string | null;
  pct: number;
  logs: LogLine[];
  tools: ToolCallView[];
  streamText: string;
  error: {
    code: string;
    message: string;
    /** ★v7：服务端下发的一句"下一步做什么"（H-12：前端不拼文案）。 */
    hint?: string | null;
    /** ★v7：同一根因的指纹与次数——`count >= 3` 时换成更强的措辞。 */
    fingerprint?: string | null;
    repeatCount?: number;
    retryable?: boolean;
  } | null;
  reportId: string | null;
  version: number | null;
  /**
   * ★v14（U3 / §23.3.4a）：这次生成**没有新增版本**（内容与最新版完全一致）。
   * 它是唯一权威来源——202 响应里的 `dedupe` 只表示"请求了去重"。
   */
  deduped: boolean;
  costUsd: number | null;
  costKnown: boolean;
  /** ★v7（H4）：成本三态。`estimated` 与 `reported` 永不同义（H-6）。 */
  costSource: CostSource | null;
  costUsdEst: number | null;
  /** ★v7（§20.3.4）：本次 run 观察到多少次 CLI 重试（`run.retry` 的本地累计）。 */
  retryCount: number;
  startedAt: number | null;
  finishedAt: number | null;
  reconnectAttempt: number;
}

const MAX_LOGS = 50;
const MAX_TOOLS = 24;

export const initialGenerationState: GenerationState = {
  phase: 'idle',
  runId: null,
  instrumentId: null,
  stage: null,
  stageLabel: null,
  pct: 0,
  logs: [],
  tools: [],
  streamText: '',
  error: null,
  reportId: null,
  version: null,
  deduped: false,
  costUsd: null,
  costKnown: false,
  costSource: null,
  costUsdEst: null,
  retryCount: 0,
  startedAt: null,
  finishedAt: null,
  reconnectAttempt: 0,
};

export type GenerationAction =
  | { type: 'submit'; instrumentId: string }
  | { type: 'queued'; runId: string }
  | { type: 'exception'; code: string; message: string }
  | { type: 'event'; event: RunStreamEvent }
  | { type: 'reconnect'; attempt: number }
  | { type: 'cancel'; }
  | { type: 'reset' };

function pushLog(logs: LogLine[], level: LogLine['level'], message: string, at: number): LogLine[] {
  const next = [...logs, { id: logs.length + at, level, message, at }];
  return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next;
}

export function generationReducer(state: GenerationState, action: GenerationAction): GenerationState {
  switch (action.type) {
    case 'submit':
      return {
        ...initialGenerationState,
        phase: 'submitting',
        instrumentId: action.instrumentId,
        startedAt: Date.now(),
      };

    case 'queued':
      return { ...state, phase: 'running', runId: action.runId, startedAt: state.startedAt ?? Date.now() };

    case 'exception':
      return {
        ...state,
        phase: 'error',
        error: { code: action.code, message: action.message },
        finishedAt: Date.now(),
      };

    case 'reconnect':
      return { ...state, reconnectAttempt: action.attempt };

    case 'cancel':
      return { ...state, phase: 'cancelled', finishedAt: Date.now() };

    case 'reset':
      return initialGenerationState;

    case 'event': {
      const { event } = action;
      const payload = event.payload ?? {};
      const at = event.at;

      switch (event.type) {
        case 'run.stage': {
          const stage = (payload.stage as RunStageId | undefined) ?? state.stage;
          const pct = typeof payload.pct === 'number' ? payload.pct : state.pct;
          const label = typeof payload.label === 'string' ? payload.label : state.stageLabel;
          return {
            ...state,
            phase: state.phase === 'submitting' ? 'running' : state.phase,
            stage: stage ?? null,
            stageLabel: label ?? null,
            pct: Math.max(state.pct, pct),
          };
        }
        case 'run.progress': {
          const pct = typeof payload.pct === 'number' ? payload.pct : state.pct;
          return { ...state, pct: Math.max(state.pct, pct), reconnectAttempt: 0 };
        }
        case 'agent.tool': {
          const tool: ToolCallView = {
            id: state.tools.length + at,
            name: String(payload.name ?? 'tool'),
            input: String(payload.input ?? ''),
            isError: Boolean(payload.isError),
            phase: String(payload.phase ?? 'result'),
            outputPreview: String(payload.outputPreview ?? ''),
          };
          const tools = [...state.tools, tool];
          return { ...state, tools: tools.length > MAX_TOOLS ? tools.slice(tools.length - MAX_TOOLS) : tools };
        }
        case 'agent.text': {
          const delta = String(payload.delta ?? '');
          const merged = `${state.streamText}${delta}`;
          return { ...state, streamText: merged.slice(-4000) };
        }
        case 'run.log': {
          const level = (payload.level as LogLine['level']) ?? 'info';
          const message = String(payload.message ?? '');
          return { ...state, logs: pushLog(state.logs, level, message, at) };
        }
        case 'run.retry': {
          // ★v7（§20.3.4 / AC-85）：一条 warn 日志 + 计数。计数用本地自增，
          // 因为 CLI 的 `attempt` 会重置（E-45：同一进程出现两段 1..6）。
          const attempt = typeof payload.attempt === 'number' ? payload.attempt : null;
          const max = typeof payload.maxRetries === 'number' ? payload.maxRetries : null;
          const errorType = typeof payload.errorType === 'string' ? payload.errorType : 'unknown';
          // ★v9（K4 / AC-100）：`layer` 是新增字段。没有它（CLI 那一层）走原话术；
          // 有它（本服务自己的重试）换一句，否则一次 runner 重试会被说成"引擎重试"。
          const message =
            payload.layer === 'runner'
              ? fill(copy.generation.retryNoticeRunner, {
                  attempt: typeof payload.attempt === 'number' ? payload.attempt : '—',
                  of: typeof payload.of === 'number' ? payload.of : '—',
                })
              : fill(copy.generation.retryNotice, {
                  attempt: attempt ?? '—',
                  max: max ?? '—',
                  errorType,
                });
          return {
            ...state,
            retryCount: state.retryCount + 1,
            logs: pushLog(state.logs, 'warn', message, at),
          };
        }
        case 'run.done':
          return {
            ...state,
            phase: 'done',
            pct: 100,
            reportId: typeof payload.reportId === 'string' ? payload.reportId : null,
            version: typeof payload.version === 'number' ? payload.version : null,
            // 缺字段 ⇒ false（旧后端仍然可用，界面只是不提示"未新增版本"）。
            deduped: payload.deduped === true,
            costUsd: typeof payload.costUsd === 'number' ? payload.costUsd : null,
            costKnown: Boolean(payload.costKnown),
            // ★v7：三态与估算金额一起进 state——只留 `costKnown` 会让界面把估算当回执。
            costSource: (payload.costSource as CostSource | undefined) ?? null,
            costUsdEst: typeof payload.costUsdEst === 'number' ? payload.costUsdEst : null,
            retryCount:
              typeof payload.retryCount === 'number' ? Math.max(state.retryCount, payload.retryCount) : state.retryCount,
            finishedAt: at,
            error: null,
          };
        case 'run.error': {
          const code = String(payload.code ?? 'internal');
          return {
            ...state,
            phase: code === 'cancelled' ? 'cancelled' : 'error',
            error: {
              code,
              message: String(payload.message ?? '生成失败'),
              // ★v7（H-12 / P-2）：hint 由服务端下发；前端**不再**按码拼字符串。
              hint: typeof payload.hint === 'string' ? payload.hint : null,
              fingerprint: typeof payload.failureFingerprint === 'string' ? payload.failureFingerprint : null,
              // ★v8 评审（H3.5 的数据通路）：同指纹在最近 N 条 run 里的次数由服务端算好，
              // 前端只负责与阈值比较——否则"同一原因失败三次"这句文案永远没有数据来源。
              repeatCount: typeof payload.repeatCount === 'number' ? payload.repeatCount : undefined,
              retryable: typeof payload.retryable === 'boolean' ? payload.retryable : undefined,
            },
            costSource: (payload.costSource as CostSource | undefined) ?? state.costSource,
            costUsdEst: typeof payload.costUsdEst === 'number' ? payload.costUsdEst : state.costUsdEst,
            retryCount:
              typeof payload.retryCount === 'number' ? Math.max(state.retryCount, payload.retryCount) : state.retryCount,
            finishedAt: at,
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

export interface UseGenerationStreamResult {
  state: GenerationState;
  start: (instrumentId: string, options?: { force?: boolean; override?: boolean }) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
  isActive: boolean;
}

export interface UseGenerationStreamOptions {
  onDone?: (reportId: string) => void;
  /**
   * ★v14：`run.done.deduped === true` 时调用（**在** `onDone` 之后）。
   * 父组件负责说那句「内容与 v13 一致，未新增版本」，并且**仍然**打开那一版。
   */
  onDeduped?: (reportId: string, version: number | null) => void;
  onError?: (code: string, message: string) => void;
  /**
   * ★v6 G3：预算闸门被触发（402 `monthly_budget_exceeded`）。
   *
   * 它**不是**错误：用户可以选择「仍然生成」（`override: true`），所以这里单独回调，
   * 而不是走 `onError` 的红色 Toast —— 把一次可覆盖的拦截说成失败，等于教用户
   * 以后无视所有红色提示。
   */
  onBudgetBlocked?: (detail: Record<string, unknown>) => void;
  /**
   * ★v9（K6）：接到了一次**接管**（409 `run_conflict` + `activeRunId`）。
   *
   * 过去这里静默 `return`：用户不知道自己点的那次没有生效。现在两句一起说——
   * "已为你接管进行中的那次"（`copy.errors.runConflict`）。
   */
  onTakeover?: (activeRunId: string) => void;
}

export function useGenerationStream(options: UseGenerationStreamOptions = {}): UseGenerationStreamResult {
  const [state, dispatch] = useReducer(generationReducer, initialGenerationState);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const queryClient = useQueryClient();
  const { onDone, onDeduped, onError, onBudgetBlocked, onTakeover } = options;

  const teardown = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);

  useEffect(() => teardown, [teardown]);

  /**
   * ★v12（S6 / §22.3.7(b) / AC-125）：看门狗触发后**对一次账**。
   *
   * 为什么不直接重连：`stream_closed` 的语义是"这条连接静默了 45 秒"，而它可能只是网关
   * 把长连接吃了，服务端仍在跑这次生成（甚至已经跑完）。`GET /runs/{id}` 是唯一能回答
   * "它现在到底在哪"的接口——`api.run()` 在此之前**全仓 0 调用**（写好了两年，从没人用过）。
   *
   * 三条纪律：
   * 1. **只调一次**（AC-125）：看门狗自己保证只回调一次（`lib/sse.ts` 的 `closed` 短路），
   *    这里不再加重试；
   * 2. 已经成功就地把报告列表刷出来——否则用户会盯着"连接断了"而看不到已经生成好的报告；
   * 3. 任何异常都吞掉：对账失败不该把一次"断流"升级成"两个错误"。
   */
  const reconcileRun = useCallback(
    async (runId: string, instrumentId: string) => {
      try {
        const run = await api.run(runId);
        if (run.status === 'succeeded') {
          void queryClient.invalidateQueries({ queryKey: ['reports', instrumentId] });
        }
      } catch {
        /* 对账失败：界面保持 stream_closed，用户手上的「继续查看」仍然有效 */
      }
    },
    [queryClient],
  );

  /** One wiring for both "I started it" and "I attached to an existing run". */
  const streamOptions = useCallback(
    (instrumentId: string, runId: string) => ({
      onEvent: (event: RunStreamEvent) => {
        dispatch({ type: 'event', event });
        if (event.type === 'run.done') {
          const reportId = event.payload.reportId;
          void queryClient.invalidateQueries({ queryKey: ['reports', instrumentId] });
          // ★v14（U6 / AC-152）：报告库与首页的『最近生成的研报』必须跟着变，
          // 否则生成完切回首页还是旧列表（那正是本轮需求的原话背景）。
          void queryClient.invalidateQueries({ queryKey: ['reports', 'library'] });
          if (typeof reportId === 'string') {
            onDone?.(reportId);
            if (event.payload.deduped === true) {
              const version =
                typeof event.payload.version === 'number' ? event.payload.version : null;
              onDeduped?.(reportId, version);
            }
          }
        }
        if (event.type === 'run.error') {
          onError?.(String(event.payload.code ?? 'internal'), String(event.payload.message ?? '生成失败'));
        }
      },
      onReconnect: (attempt: number) => dispatch({ type: 'reconnect', attempt }),
      onError: (error: Error, retryInMs: number) => {
        if (retryInMs === 0) {
          dispatch({ type: 'exception', code: 'stream_closed', message: error.message });
        }
      },
      /**
       * ★v12（S6 / §22.3.7(c) / AC-125）：看门狗回调。
       *
       * 看门狗住在 `lib/sse.ts`（它握着 `EventSource` 与 `close()`，而本 hook 拿不到），
       * 这里只接收结论。收到它就说明"连接静默超过 45 秒"——服务端的保活已经改成**真实**
       * `ping` 帧（`event: ping` + `data: {}`），所以安静 45 秒只有两种可能：服务端真的没了，
       * 或者中间设备把连接吃了。两者都不是"再等一等"能解决的（S-9：按钮必须真做事）。
       */
      onIdle: () => {
        dispatch({ type: 'exception', code: 'stream_closed', message: copy.errors.streamClosed });
        void reconcileRun(runId, instrumentId);
      },
    }),
    [onDeduped, onDone, onError, queryClient, reconcileRun],
  );

  const start = useCallback(
    async (
      instrumentId: string,
      startOptions: { force?: boolean; override?: boolean; baseReportId?: string | null; dedupe?: boolean } = {},
    ) => {
      teardown();
      dispatch({ type: 'submit', instrumentId });

      try {
        const created = await api.createReport(
          instrumentId,
          {
            force: startOptions.force,
            // ★v14（U3）：`baseReportId` = 用户点了「基于此版本更新」的那一版；
            // 不传就是普通生成（服务端的摘要段渲染成空行）。
            ...(startOptions.baseReportId ? { baseReportId: startOptions.baseReportId } : {}),
            ...(startOptions.dedupe === false ? { dedupe: false } : {}),
          },
          { override: startOptions.override },
        );
        dispatch({ type: 'queued', runId: created.runId });

        unsubscribeRef.current = subscribeToRun(created.runId, streamOptions(instrumentId, created.runId));
      } catch (error) {
        if (error instanceof ApiError) {
          // A live run already exists for this symbol: attach to it rather than
          // failing (§7.3) — with the same wiring, so the takeover also refreshes
          // the query cache and switches to the finished report.
          if (error.code === 'run_conflict' && typeof error.detail.activeRunId === 'string') {
            const activeRunId = error.detail.activeRunId;
            dispatch({ type: 'queued', runId: activeRunId });
            unsubscribeRef.current = subscribeToRun(activeRunId, streamOptions(instrumentId, activeRunId));
            // ★v9（K6 / §21.3.7）：**不再静默**。过去这里直接 `return`，用户不知道
            // 自己点的那次没有生效（虽然接管了进度）；现在两句一起说：接管了 + 这是
            // 别人的那一次。文案用 `copy.errors.runConflict`（它写了两年从没被用过）。
            onTakeover?.(activeRunId);
            return;
          }
          // ★v6 G3：402 是可覆盖的闸门，不是失败 —— 交给弹窗，界面保持干净。
          if (error.code === 'monthly_budget_exceeded') {
            dispatch({ type: 'reset' });
            onBudgetBlocked?.(error.detail);
            return;
          }
          dispatch({ type: 'exception', code: error.code, message: error.message });
          onError?.(error.code, error.message);
          return;
        }
        dispatch({
          type: 'exception',
          code: 'unknown',
          message: error instanceof Error ? error.message : '生成失败',
        });
      }
    },
    [onBudgetBlocked, onError, onTakeover, streamOptions, teardown],
  );

  const cancel = useCallback(async () => {
    if (!state.runId) return;
    try {
      await api.cancelRun(state.runId);
      dispatch({ type: 'cancel' });
    } catch {
      /* the run may already be terminal — the stream will tell us */
    }
  }, [state.runId]);

  const reset = useCallback(() => {
    teardown();
    dispatch({ type: 'reset' });
  }, [teardown]);

  const isActive = state.phase === 'submitting' || state.phase === 'running';

  return { state, start, cancel, reset, isActive };
}
