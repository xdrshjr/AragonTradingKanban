/**
 * AC-131 / AC-125（S6 / §22.3.7(c)）：**静默看门狗**。
 *
 * 为什么这个文件必须存在（一次真实事故）：服务端卡死时不会 `onerror`，只会继续发保活帧，
 * 界面于是永远 `running`、`pct` 冻在 60% 左右 —— 用户唯一能看到的事实是"数字不动了"。
 * `subscribeToRun` 因此内置看门狗：静默超过 `idleTimeoutMs`（默认 45s）就自己 `close()`
 * 并回调 `onIdle()` 一次，界面再把这次沉默翻译成一个可解释的 `stream_closed`。
 *
 * 两条"假实现陷阱"，本文件逐条钉住：
 * 1. **`ping` 帧必须刷新计时器**。旧的服务端保活是 SSE **注释帧** `: ping\n\n`，注释帧
 *    不触发 `EventSource` 的任何监听器；如果看门狗只认"业务事件"，那么一次 40 秒的安静
 *    模型调用（无 tool、无 progress）会在 45 秒被误判成断流并重连 —— 比现状更糟。
 *    所以第 1 条测试用"只发 ping 的假 EventSource"跑过好几个 ping 周期，断言 `onIdle`
 *    **一次都没被调用**（AC-131）。
 * 2. **`onIdle` 是可选回调**。没传它时不能抛、也不能留下一个永远在跑的 interval
 *    （未处理的定时器在真实浏览器里会一直占着，测试里则会污染别的用例）。
 *
 * 第 3 条测试守 AC-125 的上下文：看门狗产出的 `stream_closed` 在 `runErrors` 里必须有
 * 一个**真动作**。它过去不在表里 ⇒ 兜底成"查看日志"，而日志里什么都不会有（连接是前端
 * 自己放弃的），主按钮点了等于没点。
 */

import { describe, expect, it, vi } from 'vitest';

import { DEFAULT_IDLE_TIMEOUT_MS, subscribeToRun } from '@/lib/sse';
import { actionFor, primaryActionFor } from '@/lib/runErrors';

type Listener = (event: MessageEvent<string>) => void;

/** 与 `tests/retry-event.test.ts` 同一个假 `EventSource` 惯用法：只实现被测代码用到的那几样。 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, Listener[]>();
  closed = false;
  /** 被 `close()` 了几次。看门狗"只关一次"就是靠它证明的。 */
  closeCalls = 0;
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
    this.closeCalls += 1;
  }

  /** 模拟服务端推一条事件（`ping` 的载荷就是 `{}`）。 */
  emit(type: string, payload: Record<string, unknown> = {}, seq = 1) {
    const frame = {
      data: JSON.stringify(payload),
      lastEventId: String(seq),
    } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) listener(frame);
  }

  /** 这条事件名真的被订阅了吗？（假 `EventSource` 的"收得到"就是"注册过"） */
  subscribes(type: string): boolean {
    return (this.listeners.get(type) ?? []).length > 0;
  }
}

function installFakeEventSource() {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
  // `subscribeToRun` 先 `POST /runs/{id}/events-token`；给一个合法响应让连接立刻建立。
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ token: 'test-token', expiresIn: 60 }) })),
  );
}

/**
 * 让 `connect()`（async）跑完。
 *
 * 这里**不能**用 `setTimeout(0)` 那种 flush（`retry-event.test.ts` 的写法）：本文件的用例
 * 全程开着假定时器，真实的 `setTimeout` 永远不会到期。改为把微任务队列排干即可 ——
 * `connect()` 只 await 了 `fetch` 与 `response.json()`。
 */
async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 12; index += 1) {
    await Promise.resolve();
  }
}

/** 本文件统一用的静默阈值：比默认 45s 短得多，让用例跑得快且时间线一眼能读懂。 */
const IDLE_MS = 6_000;

function startStream(options: { onIdle?: () => void }) {
  const onEvent = vi.fn();
  const unsubscribe = subscribeToRun('run-1', {
    onEvent,
    idleTimeoutMs: IDLE_MS,
    ...(options.onIdle ? { onIdle: options.onIdle } : {}),
  });
  return { onEvent, unsubscribe };
}

describe('SSE 静默看门狗（AC-131）', () => {
  it('只发 ping 帧的连接不会被判成断流（AC-131 的核心）', async () => {
    // 这条用例存在的唯一理由：`ping` 是**保活**帧，不是状态变化。若看门狗只在业务事件
    // 上刷新 `lastEventAt`，一次 40 秒无 tool/progress 的正常模型调用会在第 45 秒被强行
    // 重连 —— 用户看到的是"进度条突然归零又重来"，而服务端其实一切正常。
    // 所以：假 EventSource 只发 `ping`，跑够 3 个以上静默阈值，断言 `onIdle` 零调用。
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    installFakeEventSource();
    let unsubscribe: (() => void) | null = null;
    try {
      const onIdle = vi.fn();
      const stream = startStream({ onIdle });
      unsubscribe = stream.unsubscribe;
      await flushMicrotasks();

      const source = FakeEventSource.instances.at(-1)!;
      expect(source).toBeDefined();
      // 前提断言：`ping` 必须真的被 `wire()` 订阅，否则"ping 不误判"根本无从谈起
      // （这也正是 `tests/runErrors.test.ts` 里 `sseEvents` 逐项相等守的那张清单）。
      expect(source.subscribes('ping')).toBe(true);
      // 另一个前提断言：看门狗**确实装上了**。少了这一条，"onIdle 没被调用"就可能只是
      // 因为压根没有看门狗（下一条用例证明它存在时会真的开火）。
      expect(vi.getTimerCount()).toBe(1);

      const startedAt = Date.now();
      const PING_PERIOD_MS = 1_000; // 远小于 `IDLE_MS`，也小于看门狗自己的检查周期
      const PING_FRAMES = 20; // 累计 20 秒 = 3 个以上 `IDLE_MS`
      for (let index = 0; index < PING_FRAMES; index += 1) {
        vi.advanceTimersByTime(PING_PERIOD_MS);
        source.emit('ping');
      }
      await flushMicrotasks();

      // 时间上真的越过了阈值（不然这条断言是空转）。
      expect(Date.now() - startedAt).toBeGreaterThan(IDLE_MS);

      expect(source.subscribes('ping')).toBe(true);
      expect(onIdle).toHaveBeenCalledTimes(0);
      expect(source.closed).toBe(false);
      // `ping` 不是状态变化，绝不能进 reducer（进 `onEvent` 会污染日志与阶段）。
      expect(stream.onEvent).not.toHaveBeenCalled();
    } finally {
      unsubscribe?.();
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('完全静默超过阈值时 close() 一次、onIdle() 恰好一次', async () => {
    // 这条守的是"卡死能被解释"：服务端不再发任何帧（连 ping 都没有）时，`onerror` 永远
    // 不会触发，界面会永远停在跑动状态。看门狗必须自己 `close()` 并回调一次，让上层派发
    // `stream_closed`。两个"恰好一次"都很重要：
    // - 重复 `close()` 会打断 `onIdle` 里可能立刻发起的新订阅；
    // - 重复 `onIdle()` 会让界面连弹两次失败卡片。
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    installFakeEventSource();
    let unsubscribe: (() => void) | null = null;
    try {
      const onIdle = vi.fn();
      const stream = startStream({ onIdle });
      unsubscribe = stream.unsubscribe;
      await flushMicrotasks();

      const source = FakeEventSource.instances.at(-1)!;
      expect(source.closed).toBe(false);

      // 一帧都不发，只让时间走：看门狗的检查周期是 max(1000, IDLE_MS/3) = 2000ms，
      // 所以到 IDLE_MS 这一刻刚好判定为断流。
      vi.advanceTimersByTime(IDLE_MS);

      expect(source.closed).toBe(true);
      expect(source.closeCalls).toBe(1);
      expect(onIdle).toHaveBeenCalledTimes(1);

      // 之后时间再走很久，也不该有第二次：interval 已经被清掉，`closed` 也已置位。
      for (let round = 0; round < 5; round += 1) {
        vi.advanceTimersByTime(IDLE_MS * 10);
      }
      expect(onIdle).toHaveBeenCalledTimes(1);
      expect(source.closeCalls).toBe(1);
    } finally {
      unsubscribe?.();
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('不传 onIdle 时不抛错，也不安装任何看门狗定时器', async () => {
    // `onIdle` 是可选参数：其他调用方（例如"只读的进度条"）不该被迫处理这个回调。
    // 但"可选"不等于"留一个空转的 interval"：真实浏览器里那个 interval 会一直活到
    // 页面关闭，测试里则会跨用例泄漏（所以这里用 `vi.getTimerCount()` 直接数）。
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    installFakeEventSource();
    let unsubscribe: (() => void) | null = null;
    try {
      const stream = startStream({});
      unsubscribe = stream.unsubscribe;
      await flushMicrotasks();

      const source = FakeEventSource.instances.at(-1)!;
      // connect() 内部的 fetch 超时定时器在 finally 里已经清掉 ⇒ 没有 onIdle 就一个定时器都没有。
      expect(vi.getTimerCount()).toBe(0);

      expect(() => vi.advanceTimersByTime(IDLE_MS * 20)).not.toThrow();
      // 没有看门狗 ⇒ 连接不会被自己关掉，界面继续等（此时由 `onerror`/重连负责）。
      expect(source.closed).toBe(false);
      expect(stream.onEvent).not.toHaveBeenCalled();
    } finally {
      unsubscribe?.();
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('默认静默阈值是 45 秒（服务端 ping 间隔 15s 的三倍）', () => {
    // 看门狗的全部杀伤力都压在这个数字上：小于服务端 ping 间隔就会疯狂误重连，
    // 太大则用户要盯着卡死的进度条等一分钟以上。45s = 3 × 15s 是两者的折中。
    expect(DEFAULT_IDLE_TIMEOUT_MS).toBe(45_000);
  });
});

describe('stream_closed 在 runErrors 里有一个真动作（AC-125 上下文）', () => {
  it('映射到 retry，且是卡片上唯一的主按钮', () => {
    // 看门狗 `close()` 之后上层派发的码就是 `stream_closed`（前端本地码，服务端不产出）。
    // 它过去**不在** `RUN_ERROR_ACTIONS` 里 ⇒ 兜底成"查看日志"，而日志里什么都不会有
    // （连接是前端自己放弃的）。主按钮必须是真做事的"再次生成"。
    expect(actionFor('stream_closed').action).toBe('retry');
    expect(actionFor('stream_closed').primary).toBe(true);
    expect(primaryActionFor('stream_closed')).toBe('retry');
  });
});
