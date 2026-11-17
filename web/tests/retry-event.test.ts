import { describe, expect, it, vi } from 'vitest';

import { generationReducer, initialGenerationState } from '@/components/generation/useGenerationStream';
import { subscribeToRun, type RunStreamEvent, type RunStreamEventType } from '@/lib/sse';

/**
 * AC-85（spec §20.4 / V8-P1-3）：`run.retry` 必须**真的到达前端 state**。
 *
 * 这一条专门守"只改了类型联合"的那种假实现：`web/lib/sse.ts` 里 `wire([...])` 是一张
 * **硬编码的订阅清单**，把 `run.retry` 加进 `RunStreamEventType` 而漏掉那张清单时，
 * 服务端发了、类型也对、reducer 也写得对——事件却永远进不了 `onEvent`。
 * 所以这里用假 `EventSource` 走**真实的** `subscribeToRun`，而不是直接调 reducer。
 */

type Listener = (event: MessageEvent<string>) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, Listener[]>();
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    const bucket = this.listeners.get(type) ?? [];
    bucket.push(listener);
    this.listeners.set(type, bucket);
  }

  close() {
    this.closed = true;
  }

  /** 模拟服务端推一条事件。 */
  emit(type: string, payload: Record<string, unknown>, seq = 1) {
    const frame = {
      data: JSON.stringify(payload),
      lastEventId: String(seq),
    } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) listener(frame);
  }

  /** 这条事件名真的被订阅了吗？（假 EventSource 的"收得到"就是"注册过") */
  subscribes(type: string): boolean {
    return (this.listeners.get(type) ?? []).length > 0;
  }
}

function installFakeEventSource() {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
  // `subscribeToRun` 会先 `POST /runs/{id}/events-token`；给一个合法响应，让连接
  // 立刻建立（这条路径本身有 catch，但成功路径少一层等待）。
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ token: 'test-token', expiresIn: 60 }) })),
  );
}

/** 让 `connect()`（async）跑完：它内部有一次 fetch 与几个微任务。 */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe('run.retry 的订阅（AC-85）', () => {
  it('registers run.retry on the wire list, not just in the type union', async () => {
    installFakeEventSource();
    const unsubscribe = subscribeToRun('run-1', { onEvent: () => {} });
    await flush();
    await flush();

    const source = FakeEventSource.instances.at(-1);
    expect(source).toBeDefined();
    const expected: RunStreamEventType[] = [
      'run.stage',
      'run.progress',
      'agent.tool',
      'agent.text',
      'run.log',
      'run.retry',
      'run.done',
      'run.error',
    ];
    for (const type of expected) {
      expect(source!.subscribes(type), `${type} 没有被订阅（wire 数组漏了它）`).toBe(true);
    }
    unsubscribe();
  });

  it('delivers a dispatched run.retry into onEvent', async () => {
    installFakeEventSource();
    const received: RunStreamEvent[] = [];
    const unsubscribe = subscribeToRun('run-1', { onEvent: (event) => received.push(event) });
    await flush();
    await flush();

    const source = FakeEventSource.instances.at(-1)!;
    source.emit(
      'run.retry',
      { attempt: 3, maxRetries: 6, errorType: 'network_error', delayMs: 3743, tier: 1, tierCount: 2, totalAttempts: 7, retries: 7 },
      42,
    );

    expect(received).toHaveLength(1);
    expect(received[0]!.type).toBe('run.retry');
    expect(received[0]!.payload.errorType).toBe('network_error');
    unsubscribe();
  });

  it('turns run.retry into retryCount + a warn log (the state the UI renders)', async () => {
    installFakeEventSource();
    let state = generationReducer(initialGenerationState, { type: 'queued', runId: 'run-1' });

    const unsubscribe = subscribeToRun('run-1', {
      onEvent: (event) => {
        state = generationReducer(state, { type: 'event', event });
      },
    });
    await flush();
    await flush();

    const source = FakeEventSource.instances.at(-1)!;
    source.emit('run.retry', { attempt: 2, maxRetries: 6, errorType: 'network_error', delayMs: 1753 }, 7);

    expect(state.retryCount).toBe(1);
    const warn = state.logs.at(-1);
    expect(warn?.level).toBe('warn');
    expect(warn?.message).toContain('第 2/6 次重试');
    expect(warn?.message).toContain('network_error');
    unsubscribe();
  });

  it('counts locally so a resetting `attempt` cannot lose retries', () => {
    // E-45 实测：同一进程里 attempt 出现过 1,2 然后又是 1..6。累计必须是本地自增。
    const events: RunStreamEvent[] = [
      { seq: 1, type: 'run.retry', payload: { attempt: 1, maxRetries: 6 }, at: 1 },
      { seq: 2, type: 'run.retry', payload: { attempt: 2, maxRetries: 6 }, at: 2 },
      { seq: 3, type: 'run.retry', payload: { attempt: 1, maxRetries: 6 }, at: 3 },
    ];
    const state = events.reduce(
      (accumulator, event) => generationReducer(accumulator, { type: 'event', event }),
      generationReducer(initialGenerationState, { type: 'queued', runId: 'r' }),
    );
    expect(state.retryCount).toBe(3);
    expect(state.logs.every((line) => line.level === 'warn')).toBe(true);
  });

  it('carries the server hint and tri-state cost from run.error', () => {
    const state = generationReducer(
      generationReducer(initialGenerationState, { type: 'queued', runId: 'r' }),
      {
        type: 'event',
        event: {
          seq: 10,
          type: 'run.error',
          at: 10,
          payload: {
            code: 'provider_unavailable',
            message: '网络错误',
            retryable: true,
            hint: '稍等 1–2 分钟再试',
            failureFingerprint: 'a1b2c3d4e5f6',
            retryCount: 8,
            costSource: 'estimated',
            costUsdEst: 0.0123,
          },
        },
      },
    );
    expect(state.phase).toBe('error');
    expect(state.error?.hint).toBe('稍等 1–2 分钟再试');
    expect(state.error?.fingerprint).toBe('a1b2c3d4e5f6');
    expect(state.error?.retryable).toBe(true);
    expect(state.retryCount).toBe(8);
    expect(state.costSource).toBe('estimated');
    expect(state.costUsdEst).toBeCloseTo(0.0123);
    // 失败也要说清花了多少钱（H-9）。
    expect(state.costUsd).toBeNull();
  });

  it('keeps the tri-state on run.done instead of collapsing it into costKnown', () => {
    const state = generationReducer(
      generationReducer(initialGenerationState, { type: 'queued', runId: 'r' }),
      {
        type: 'event',
        event: {
          seq: 11,
          type: 'run.done',
          at: 11,
          payload: { reportId: 'rep-1', version: 2, costUsd: null, costKnown: false, costSource: 'estimated', costUsdEst: 0.0172 },
        },
      },
    );
    expect(state.phase).toBe('done');
    expect(state.costKnown).toBe(false);
    expect(state.costSource).toBe('estimated');
    expect(state.costUsdEst).toBeCloseTo(0.0172);
  });
});
