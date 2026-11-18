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
  | 'run.error'
  //: ★v12（S6 / §22.3.7(c) / AC-131）：服务端保活改成**真实帧**（`event: ping` + `data: {}`）。
  //: 过去的保活是 SSE **注释帧** `: ping\n\n`，而注释帧**不会**触发 `EventSource.onmessage`
  //: ——于是任何"看门狗只在事件到达时刷新 lastEventAt"的写法都会把一次安静的长模型调用
  //: （无 tool 事件、无 progress）判成断流，45 秒必然误重连，比现状更糟。
  | 'ping';

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
  /**
   * ★v12（S6 / §22.3.7(c) / AC-131）：**看门狗**触发。
   *
   * 连接静默超过 `idleTimeoutMs`（默认 45 秒）时，`subscribeToRun` 自己 `close()` 并调用
   * 这里一次。为什么回调住在这里而不是 `useGenerationStream.ts`：`EventSource` 的生命周期
   * 完全封在本文件里（`source` 是局部变量，上层**没有任何方式** `es.close()`），所以
   * 看门狗放在 hook 里写不出来（★v12 修订 V11-P1-5 的落地形态）。
   */
  onIdle?: () => void;
  /** 静默多久算断流。`ping` 帧会刷新计时器，所以它必须**远大于**服务端的 ping 间隔。 */
  idleTimeoutMs?: number;
  /** 3 attempts, exponential backoff, then give up (spec §6.3). */
  maxRetries?: number;
}

const DEFAULT_MAX_RETRIES = 3;

/**
 * ★v12（S6）：静默阈值。服务端的保活间隔是 `poll_timeout=15s`，三倍余量。
 *
 * 为什么是"三倍"而不是"两倍"：`ping` 帧本身也会因为浏览器节流（后台标签页 1 秒一次
 * 定时器）迟到，两倍余量在后台标签页上会偶发误判。45 秒 = 3 × 15s。
 */
export const DEFAULT_IDLE_TIMEOUT_MS = 45_000;

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
  const {
    onEvent,
    onOpen,
    onError,
    onReconnect,
    onIdle,
    idleTimeoutMs = DEFAULT_IDLE_TIMEOUT_MS,
    maxRetries = DEFAULT_MAX_RETRIES,
  } = options;
  let after = options.after ?? 0;
  let attempt = 0;
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  //: ★v12（S6）：最后一次**收到帧**的时刻（含 `ping`）。看门狗的唯一输入。
  let lastEventAt = Date.now();
  let idleTimer: ReturnType<typeof setInterval> | null = null;

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
        // ★v12（S6 / AC-131）：**任何**帧都刷新静默计时器——包括 `ping`。
        // 这是"安静 ≠ 断"的唯一实现：一次 40 秒的模型调用只会产生 ping 帧，
        // 若 ping 不刷新计时器，看门狗必然误判（正是 V11-P1-5 证伪的那一版）。
        lastEventAt = Date.now();
        if (type === 'ping') {
          // 保活帧**不进** reducer：它不是状态变化，只是"对面还活着"。
          return;
        }
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
        // ★v12（S6 / AC-131）：保活帧必须真的被订阅，否则 AC-131 的"发 ping 时不误判"
        // 根本不成立（`contracts/run_error_codes.json` 的 `sseEvents` 已加 `ping`，
        // 三向断言会红）。
        'ping',
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

  /**
   * ★v12（S6 / §22.3.7(c) / AC-125 / AC-131）：**看门狗**。
   *
   * 只做一件事：连接静默超过 `idleTimeoutMs` 就自己 `close()` 并回调 `onIdle()` 一次。
   * 为什么必须是它（E-110/E-110 的现状）：服务端卡死时只会发保活帧，浏览器不会触发
   * `onerror`，界面于是永远 `running`、`pct` 冻在 60% 左右——**用户唯一能看到的事实是
   * "数字不动了"**。看门狗把这个沉默变成一次可解释的 `stream_closed`。
   *
   * 三条纪律：
   * 1. 计时器在 `connect()` **之外**起（重连不该重置它，否则"断流→重连→再断流"会永远
   *    不触发）；
   * 2. `ping` 帧会刷新 `lastEventAt`（见 `wire()`），所以"服务端活着但安静"不会误判；
   * 3. 只回调**一次**：`close()` 之后再进来直接 `return`。
   */
  const startWatchdog = () => {
    if (!onIdle || idleTimeoutMs <= 0 || typeof setInterval !== 'function') return;
    if (idleTimer !== null) return;
    idleTimer = setInterval(() => {
      if (closed) return;
      if (Date.now() - lastEventAt < idleTimeoutMs) return;
      // 先关自己再回调：回调里可能立刻重新订阅，重复的 `close()` 会打断新的连接。
      close();
      onIdle();
    }, Math.max(1000, Math.floor(idleTimeoutMs / 3)));
  };

  const close = () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    if (idleTimer !== null) {
      clearInterval(idleTimer);
      idleTimer = null;
    }
    source?.close();
    source = null;
  };

  void connect();
  startWatchdog();

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
