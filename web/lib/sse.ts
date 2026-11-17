/**
 * SSE client for generation progress (spec §6.3, ★P1-3).
 *
 * `EventSource` cannot send an `Authorization` header, so when `API_TOKEN` is
 * configured we first POST for a 60-second token bound to the run id and put it
 * in the query string. Reconnects carry `?after=<lastSeq>` so the server can
 * replay from `run_events` instead of losing events (§7.4, ★P1-5).
 */

import { api, sseBase } from './api';

export type RunStreamEventType =
  | 'run.stage'
  | 'run.progress'
  | 'agent.tool'
  | 'agent.text'
  | 'run.log'
  //: ★v7（V8-P1-3）：CLI 的带抖动退避。`agent.*` 是 CLI 的词汇，`run.*` 是 run 的词汇。
  | 'run.retry'
  | 'run.done'
  | 'run.error';

export interface RunStreamEvent {
  seq: number;
  type: RunStreamEventType;
  payload: Record<string, unknown>;
  at: number;
}

export interface StreamOptions {
  after?: number;
  onEvent: (event: RunStreamEvent) => void;
  onOpen?: () => void;
  onError?: (error: Error, retryInMs: number) => void;
  onReconnect?: (attempt: number) => void;
  /** 3 attempts, exponential backoff, then give up (spec §6.3). */
  maxRetries?: number;
}

const DEFAULT_MAX_RETRIES = 3;

export function buildEventsUrl(runId: string, token: string, after: number): string {
  const params = new URLSearchParams();
  if (token) params.set('token', token);
  if (after > 0) params.set('after', String(after));
  const query = params.toString();
  return `${sseBase()}/api/v1/runs/${encodeURIComponent(runId)}/events${query ? `?${query}` : ''}`;
}

export function retryDelay(attempt: number): number {
  return Math.min(8000, 400 * 2 ** attempt);
}

/**
 * Subscribe to a run. Returns an unsubscribe function; call it on unmount so a
 * cancelled generation does not keep a socket open.
 */
export function subscribeToRun(runId: string, options: StreamOptions): () => void {
  const { onEvent, onOpen, onError, onReconnect, maxRetries = DEFAULT_MAX_RETRIES } = options;
  let after = options.after ?? 0;
  let attempt = 0;
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const parse = (raw: MessageEvent<string>, type: RunStreamEventType, seq: number) => {
    let payload: Record<string, unknown> = {};
    try {
      payload = raw.data ? (JSON.parse(raw.data) as Record<string, unknown>) : {};
    } catch {
      payload = { raw: raw.data };
    }
    const event: RunStreamEvent = { seq, type, payload, at: Date.now() };
    if (seq > after) after = seq;
    onEvent(event);
  };

  const connect = async () => {
    if (closed) return;
    let token = '';
    try {
      const issued = await api.eventsToken(runId);
      token = issued.token;
    } catch {
      token = '';
    }
    if (closed) return;

    source?.close();
    source = new EventSource(buildEventsUrl(runId, token, after));

    source.onopen = () => {
      attempt = 0;
      onOpen?.();
    };

    const wire = (type: RunStreamEventType) => {
      source?.addEventListener(type, (event) => {
        const message = event as MessageEvent<string>;
        const seq = Number.parseInt(message.lastEventId || '0', 10) || after;
        parse(message, type, seq);
        if (type === 'run.done' || type === 'run.error') {
          close();
        }
      });
    };

    // ★v8（V8-P1-3）：这张数组是**订阅清单**，不是类型。只把 `run.retry` 加进上面那个
    // 类型联合，事件永远不会到达 `onEvent`——服务端发了，前端收不到，而测试如果只
    // 测 reducer 就会全绿。`tests/retry-event.test.ts` 用假 `EventSource` 守这条。
    (
      [
        'run.stage',
        'run.progress',
        'agent.tool',
        'agent.text',
        'run.log',
        'run.retry',
        'run.done',
        'run.error',
      ] as RunStreamEventType[]
    ).forEach(wire);

    source.onerror = () => {
      if (closed) return;
      source?.close();
      source = null;
      if (attempt >= maxRetries) {
        onError?.(new Error('进度连接中断，已停止重连'), 0);
        close();
        return;
      }
      const delay = retryDelay(attempt);
      attempt += 1;
      onReconnect?.(attempt);
      onError?.(new Error('进度连接中断，正在重连'), delay);
      retryTimer = setTimeout(() => {
        void connect();
      }, delay);
    };
  };

  const close = () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    source?.close();
    source = null;
  };

  void connect();

  // A backgrounded tab may have its connection dropped by the browser; coming
  // back should resume rather than silently stop updating.
  const onVisibility = () => {
    if (document.visibilityState === 'visible' && !closed && source === null) {
      attempt = 0;
      void connect();
    }
  };
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibility);
  }

  return () => {
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibility);
    }
    close();
  };
}
