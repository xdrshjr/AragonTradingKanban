'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useReducer, useRef } from 'react';

import { ApiError, api } from '@/lib/api';
import { subscribeToRun, type RunStreamEvent } from '@/lib/sse';
import type { RunStageId } from '@/lib/types';

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
  error: { code: string; message: string } | null;
  reportId: string | null;
  version: number | null;
  costUsd: number | null;
  costKnown: boolean;
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
  costUsd: null,
  costKnown: false,
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
        case 'run.done':
          return {
            ...state,
            phase: 'done',
            pct: 100,
            reportId: typeof payload.reportId === 'string' ? payload.reportId : null,
            version: typeof payload.version === 'number' ? payload.version : null,
            costUsd: typeof payload.costUsd === 'number' ? payload.costUsd : null,
            costKnown: Boolean(payload.costKnown),
            finishedAt: at,
            error: null,
          };
        case 'run.error': {
          const code = String(payload.code ?? 'internal');
          return {
            ...state,
            phase: code === 'cancelled' ? 'cancelled' : 'error',
            error: { code, message: String(payload.message ?? '生成失败') },
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
  start: (instrumentId: string, options?: { force?: boolean }) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
  isActive: boolean;
}

export interface UseGenerationStreamOptions {
  onDone?: (reportId: string) => void;
  onError?: (code: string, message: string) => void;
}

export function useGenerationStream(options: UseGenerationStreamOptions = {}): UseGenerationStreamResult {
  const [state, dispatch] = useReducer(generationReducer, initialGenerationState);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const queryClient = useQueryClient();
  const { onDone, onError } = options;

  const teardown = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);

  useEffect(() => teardown, [teardown]);

  /** One wiring for both "I started it" and "I attached to an existing run". */
  const streamOptions = useCallback(
    (instrumentId: string) => ({
      onEvent: (event: RunStreamEvent) => {
        dispatch({ type: 'event', event });
        if (event.type === 'run.done') {
          const reportId = event.payload.reportId;
          void queryClient.invalidateQueries({ queryKey: ['reports', instrumentId] });
          if (typeof reportId === 'string') onDone?.(reportId);
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
    }),
    [onDone, onError, queryClient],
  );

  const start = useCallback(
    async (instrumentId: string, startOptions: { force?: boolean } = {}) => {
      teardown();
      dispatch({ type: 'submit', instrumentId });

      try {
        const created = await api.createReport(instrumentId, { force: startOptions.force });
        dispatch({ type: 'queued', runId: created.runId });

        unsubscribeRef.current = subscribeToRun(created.runId, streamOptions(instrumentId));
      } catch (error) {
        if (error instanceof ApiError) {
          // A live run already exists for this symbol: attach to it rather than
          // failing (§7.3) — with the same wiring, so the takeover also refreshes
          // the query cache and switches to the finished report.
          if (error.code === 'run_conflict' && typeof error.detail.activeRunId === 'string') {
            const activeRunId = error.detail.activeRunId;
            dispatch({ type: 'queued', runId: activeRunId });
            unsubscribeRef.current = subscribeToRun(activeRunId, streamOptions(instrumentId));
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
    [onError, streamOptions, teardown],
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
