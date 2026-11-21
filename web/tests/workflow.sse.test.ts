import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { generationReducer, initialGenerationState } from '@/components/generation/useGenerationStream';
import { subscribeToRun, type RunStreamEvent } from '@/lib/sse';

/** `noUncheckedIndexedAccess`：先断言存在再收窄，失败时给出可读的报错。 */
function must<T>(value: T | undefined | null): T {
  expect(value).toBeDefined();
  expect(value).not.toBeNull();
  return value as T;
}


/**
 * RW-4：`run.workflow` 的订阅链路三处同时落地——类型联合、订阅清单、契约 `sseEvents`。
 * 只测 reducer 会全绿而事件永远到不了 `onEvent`（V8-P1-3 的同一个坑），所以这里用假
 * `EventSource` 走**真实的** `subscribeToRun`。
 */

type Listener = (event: MessageEvent<string>) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, Listener[]>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {}

  emit(type: string, payload: Record<string, unknown>, seq = 1) {
    const frame = { data: JSON.stringify(payload), lastEventId: String(seq) } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) listener(frame);
  }
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const PLAN = {
  kind: 'plan',
  templateName: '标准五段式研究',
  version: 3,
  hash: '3f9a1c0b2d44',
  fallback: false,
  total: 3,
  items: [
    { key: 'S1', kind: 'step', title: '核对事实底座', role: 'data', level: 1, sections: ['thesis'], upstream: [] },
    { key: 'S2', kind: 'step', title: '技术面', role: 'data', level: 2, sections: [], upstream: ['S1'] },
    { key: 'G1', kind: 'gate', title: '估值前核对', level: 3, upstream: ['S2'] },
  ],
};

function event(payload: Record<string, unknown>, seq: number): RunStreamEvent {
  return { seq, type: 'run.workflow', payload, at: 1000 + seq };
}

describe('run.workflow 的订阅（RW-4）', () => {
  it('订阅清单真的订阅了 run.workflow，且事件到达 onEvent', async () => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ token: 't', expiresIn: 60 }) })),
    );
    const received: RunStreamEvent[] = [];
    const stop = subscribeToRun('run-1', { onEvent: (item) => received.push(item) });
    await flush();

    const source = must(FakeEventSource.instances[0]);
    expect(source.listeners.get('run.workflow')?.length).toBeGreaterThan(0);
    source.emit('run.workflow', PLAN, 5);
    expect(received).toHaveLength(1);
    expect(must(received[0]).type).toBe('run.workflow');
    expect(must(received[0]).payload.kind).toBe('plan');
    stop();
    vi.unstubAllGlobals();
  });

  it('契约 sseEvents 含 run.workflow（后端 / 前端 / 契约三向）', () => {
    const contract = JSON.parse(
      readFileSync(join(__dirname, '..', '..', 'contracts', 'run_error_codes.json'), 'utf-8'),
    ) as { sseEvents: string[] };
    expect(contract.sseEvents).toContain('run.workflow');
  });
});

describe('run.workflow 的 reducer', () => {
  const planned = generationReducer(initialGenerationState, { type: 'event', event: event(PLAN, 1) });

  it('plan 事件建立时间线，全部待定', () => {
    expect(planned.workflow?.items.map((item) => item.state)).toEqual(['pending', 'pending', 'pending']);
    expect(planned.workflow?.templateName).toBe('标准五段式研究');
    expect(planned.workflow?.total).toBe(3);
  });

  it('step 事件推进状态与计数；重复 start 不把已完成项拉回', () => {
    const steps = [
      { kind: 'step', key: 'S1', state: 'start', doneCount: 0, total: 3 },
      { kind: 'step', key: 'S1', state: 'done', doneCount: 1, total: 3 },
      { kind: 'step', key: 'S1', state: 'start', doneCount: 1, total: 3 },
      { kind: 'step', key: 'S2', state: 'skip', note: '数据不足', doneCount: 2, total: 3 },
    ];
    const state = steps.reduce(
      (acc, payload, index) => generationReducer(acc, { type: 'event', event: event(payload, index + 2) }),
      planned,
    );
    expect(state.workflow?.items[0]?.state).toBe('done');
    expect(state.workflow?.items[1]?.state).toBe('skip');
    expect(state.workflow?.items[1]?.note).toBe('数据不足');
    expect(state.workflow?.doneCount).toBe(2);
  });

  it('未知 kind、未知 key、没有 plan 时的 step 一律忽略', () => {
    const unknownKind = generationReducer(planned, { type: 'event', event: event({ kind: 'future' }, 2) });
    expect(unknownKind.workflow).toEqual(planned.workflow);
    const unknownKey = generationReducer(planned, {
      type: 'event',
      event: event({ kind: 'step', key: 'S9', state: 'done', doneCount: 1, total: 3 }, 3),
    });
    expect(unknownKey.workflow).toEqual(planned.workflow);
    const noPlan = generationReducer(initialGenerationState, {
      type: 'event',
      event: event({ kind: 'step', key: 'S1', state: 'done', doneCount: 1, total: 3 }, 1),
    });
    expect(noPlan.workflow).toBeNull();
  });

  it('回放重建：同一事件序列得到同一状态（刷新后由 replay 重建）', () => {
    const script = [
      event(PLAN, 1),
      event({ kind: 'step', key: 'S1', state: 'done', doneCount: 1, total: 3 }, 2),
    ];
    const run = () =>
      script.reduce((acc, item) => generationReducer(acc, { type: 'event', event: item }), initialGenerationState);
    expect(run().workflow).toEqual(run().workflow);
    expect(run().workflow?.items[0]?.state).toBe('done');
  });

  it('新的 submit 清空上一次的时间线', () => {
    const next = generationReducer(planned, { type: 'submit', instrumentId: 'x' });
    expect(next.workflow).toBeNull();
  });
});
