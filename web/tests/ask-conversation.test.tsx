import { act, renderHook, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError } from '@/lib/api';
import { MeContext, OPEN_ME } from '@/lib/auth';
import type { AskOut } from '@/lib/types';
import {
  askStreamReducer, initialAskStreamState, subscribeToAsk,
  useAskStream,
} from '@/components/ask/useAskStream';
import { useAskConversation } from '@/components/ask/useAskConversation';

const answer = (id = 'old', status: AskOut['status'] = 'done'): AskOut => ({
  id, reportId: 'report', question: '上一轮问题', mode: 'deep', status,
  grounded: true, citationsDropped: 0, costKnown: false,
  answer: { answerText: '上一轮回答', hits: [], citations: [] },
  createdAt: '2026-11-20T12:00:00Z',
});

class ControlledSource {
  static instances: ControlledSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  listeners = new Map<string, (event: MessageEvent<string>) => void>();
  constructor(public url: string) { ControlledSource.instances.push(this); }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, listener);
  }
  close() { this.closed = true; }
  emit(type: string, payload: unknown, seq: number) {
    this.listeners.get(type)?.({
      data: JSON.stringify(payload), lastEventId: String(seq),
    } as MessageEvent<string>);
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  ControlledSource.instances = [];
});

describe('追问的权威状态', () => {
  it('停止时任务已结束则只读核验终态并释放发送锁', async () => {
    vi.stubGlobal('EventSource', ControlledSource);
    const post = vi.spyOn(api, 'ask').mockResolvedValue({
      id: 'active', askId: 'active', status: 'queued', streamUrl: '',
    });
    vi.spyOn(api, 'askEventsToken').mockResolvedValue({
      token: 't', expiresIn: 60,
    });
    vi.spyOn(api, 'cancelAsk').mockRejectedValue(new ApiError(409, {
      error: { code: 'ask_not_active', message: '任务已结束',
        requestId: '-', detail: {} },
    }));
    const get = vi.spyOn(api, 'askById').mockResolvedValue(answer('active'));
    const { result } = renderHook(() => useAskStream({ reportId: 'report' }));
    await act(() => result.current.submit({ question: '问题', mode: 'deep' }));
    await act(() => result.current.cancel());
    expect(get).toHaveBeenCalledWith('active');
    expect(result.current.state.phase).toBe('done');
    expect(result.current.isActive).toBe(false);
    expect(post).toHaveBeenCalledTimes(1);
  });

  it('GET running保留活动槽；SSE摘要必须等待GET核验', () => {
    const running = askStreamReducer(initialAskStreamState, {
      type: 'settled', result: answer('active', 'running'),
    });
    expect(running.phase).toBe('streaming');
    const preview = askStreamReducer(running, {
      type: 'event', event: {
        type: 'ask.done', seq: 1, payload: { grounded: true }, at: 1,
      },
    });
    expect(preview.phase).toBe('verifying');
    expect(preview.result?.grounded ?? false).toBe(false);
  });

  it('POST迟到拒绝不能覆盖已切换的报告', async () => {
    let reject!: (error: Error) => void;
    vi.spyOn(api, 'ask').mockReturnValue(new Promise((_, fail) => {
      reject = fail;
    }));
    const { result, rerender } = renderHook(
      ({ reportId }) => useAskStream({ reportId }),
      { initialProps: { reportId: 'first' } },
    );
    let pending!: Promise<void>;
    act(() => { pending = result.current.submit({ question: '问题' }); });
    rerender({ reportId: 'second' });
    await act(async () => { reject(new Error('old')); await pending; });
    expect(result.current.state.reportId).toBe('second');
    expect(result.current.state.error).toBeNull();
  });

  it('取消网络失败不谎报已取消；最终详情失败保持发送锁', async () => {
    vi.stubGlobal('EventSource', ControlledSource);
    vi.spyOn(api, 'ask').mockResolvedValue({
      id: 'active', askId: 'active', status: 'queued', streamUrl: '',
    });
    vi.spyOn(api, 'askEventsToken').mockResolvedValue({ token: 't', expiresIn: 60 });
    vi.spyOn(api, 'cancelAsk').mockRejectedValue(new ApiError(0, null));
    vi.spyOn(api, 'askById').mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useAskStream({ reportId: 'report' }));
    await act(() => result.current.submit({ question: '问题', mode: 'deep' }));
    await act(() => result.current.cancel());
    expect(result.current.state.phase).not.toBe('cancelled');
    expect(result.current.state.error?.code).toBe('cancel_unconfirmed');
    expect(result.current.isActive).toBe(true);
    await act(async () => {
      ControlledSource.instances[0]?.emit('ask.done', {}, 1);
    });
    expect(result.current.state.phase).toBe('unverified');
    expect(result.current.isActive).toBe(true);
  });

  it('重试停止重新进入pending并清除未确认错误，同一事件周期只发一次cancel', async () => {
    vi.stubGlobal('EventSource', ControlledSource);
    vi.spyOn(api, 'ask').mockResolvedValue({
      id: 'active', askId: 'active', status: 'queued', streamUrl: '',
    });
    vi.spyOn(api, 'askEventsToken').mockResolvedValue({ token: 't', expiresIn: 60 });
    const stop = vi.spyOn(api, 'cancelAsk')
      .mockRejectedValueOnce(new ApiError(0, null));
    const { result } = renderHook(() => useAskStream({ reportId: 'report' }));
    await act(() => result.current.submit({ question: '问题', mode: 'deep' }));
    await act(() => result.current.cancel());
    let finish!: () => void;
    stop.mockReturnValue(new Promise((resolve) => {
      finish = () => resolve({ status: 'cancelling', runId: 'active' });
    }));
    vi.spyOn(api, 'askById').mockResolvedValue(answer('active', 'cancelling'));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.cancel();
      void result.current.cancel();
    });
    expect(stop).toHaveBeenCalledTimes(2);
    expect(result.current.state.error).toBeNull();
    await act(async () => { finish(); await pending; });
  });

  it('反复open→error只能重连三次，重复seq不能重置失败计数', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', ControlledSource);
    vi.spyOn(api, 'askEventsToken').mockResolvedValue({ token: 't', expiresIn: 60 });
    const failed = vi.fn();
    const stop = subscribeToAsk('active', { onEvent: vi.fn(), onError: failed });
    await vi.advanceTimersByTimeAsync(0);
    for (let index = 0; index < 4; index += 1) {
      const source = ControlledSource.instances.at(-1)!;
      source.onopen?.();
      source.emit('ask.delta', { text: 'a' }, 1);
      source.onerror?.();
      await vi.advanceTimersByTimeAsync(10_000);
    }
    expect(ControlledSource.instances).toHaveLength(4);
    expect(failed).toHaveBeenLastCalledWith(expect.any(Error), 0);
    stop();
  });

  it('签名401立即停连，不使用空签名重试', async () => {
    vi.stubGlobal('EventSource', ControlledSource);
    vi.spyOn(api, 'askEventsToken').mockRejectedValue(new ApiError(401, null));
    const failed = vi.fn();
    const stop = subscribeToAsk('active', { onEvent: vi.fn(), onError: failed });
    await waitFor(() => expect(failed).toHaveBeenCalled());
    expect(ControlledSource.instances).toHaveLength(0);
    stop();
  });
});

const options = {
  reportId: 'report', identityKey: 'open', open: true,
  deepAvailable: true, conversationAvailable: true,
  dailyLimit: 5, questionMaxChars: 500,
};

describe('多轮历史与话题边界', () => {
  it('上下文失效后先显式新话题才能再次发送', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [answer()], total: 1 });
    const post = vi.spyOn(api, 'ask').mockRejectedValueOnce(new ApiError(422, {
      error: { code: 'invalid_ask_context', message: '请开始新话题',
        requestId: '-', detail: {} },
    })).mockResolvedValue(answer('new'));
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    act(() => {
      result.current.setDraft('付费问题'); result.current.setMode('deep');
    });
    await act(() => result.current.submit());
    act(() => result.current.setDraft('继续追问'));
    expect(result.current.canSubmit).toBe(false);
    await act(() => result.current.submit());
    expect(post).toHaveBeenCalledTimes(1);
    act(() => result.current.newTopic());
    act(() => result.current.setDraft('新问题'));
    expect(result.current.canSubmit).toBe(true);
    await act(() => result.current.submit());
    expect(post).toHaveBeenLastCalledWith('report', {
      question: '新问题', mode: 'deep', contextAskIds: [],
    });
  });

  it.each(['', '下一条草稿'])(
    '拒绝额度降级保留错误轮次和可编辑问题，已有草稿=%s', async (nextDraft) => {
      vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [], total: 0 });
      const post = vi.spyOn(api, 'ask').mockRejectedValue(new ApiError(429, {
        error: { code: 'quota_exceeded', message: '今日额度不足',
          requestId: '-', detail: {} },
      }));
      const { result } = renderHook(() => useAskConversation(options));
      await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
      act(() => {
        result.current.setDraft('付费问题'); result.current.setMode('deep');
      });
      await act(() => result.current.submit());
      expect(result.current.turns[0]?.phase).toBe('error');
      expect(result.current.turns[0]?.error?.code).toBe('quota_exceeded');
      expect(result.current.canSubmit).toBe(false);
      act(() => result.current.setMode('cite'));
      expect(result.current.turns[0]?.mode).toBe('deep');
      act(() => {
        result.current.setDraft(nextDraft);
        result.current.stream.declineFallback();
      });
      expect(result.current.draft).toBe(nextDraft || '付费问题');
      expect(result.current.turns[0]?.phase).toBe('error');
      expect(result.current.isActive).toBe(false);
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it('StrictMode重放effect后仍能恢复历史，清理不会删除当前订阅', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [answer()], total: 1 });
    const { result } = renderHook(() => useAskConversation(options), {
      wrapper: ({ children }) => <StrictMode>{children}</StrictMode>,
    });
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    expect(result.current.turns).toHaveLength(1);
  });

  it('剩余额度来自当前Me账本，不能用配置上限减本次会话次数冒充', () => {
    const bucket = {
      limit: 5, remaining: 1, usedToday: 4, bonus: 0,
      resetAt: '', pendingRequest: null,
    };
    const { result } = renderHook(() => useAskConversation({
      ...options, conversationAvailable: false,
    }), { wrapper: ({ children }) => <MeContext.Provider value={{
      me: { ...OPEN_ME, quota: { report: bucket, deepAsk: bucket, tz: 'UTC' } },
      setMe: () => {}, refreshMe: async () => {},
    }}>{children}</MeContext.Provider> });
    expect(result.current.stream.deepRemaining).toBe(1);
    expect(result.current.deepQuotaKnown).toBe(true);
    expect(result.current.deepUnlimited).toBe(false);
  });

  it('第二轮发送服务端历史id，新话题后刷新不重新加入旧id', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [answer()], total: 1 });
    const post = vi.spyOn(api, 'ask').mockResolvedValue(answer('new'));
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    act(() => { result.current.setMode('deep'); result.current.setDraft('解释第二点'); });
    await act(() => result.current.submit());
    expect(post).toHaveBeenLastCalledWith('report', {
      question: '解释第二点', mode: 'deep', contextAskIds: ['old'],
    });
    await waitFor(() => expect(result.current.isActive).toBe(false));
    act(() => result.current.newTopic());
    await act(() => result.current.reloadHistory());
    expect(result.current.contextCandidateIds).toEqual([]);
    act(() => result.current.setDraft('新问题'));
    await act(() => result.current.submit());
    expect(post).toHaveBeenLastCalledWith('report', {
      question: '新问题', mode: 'deep', contextAskIds: [],
    });
  });

  it('旧API不读取mine历史或发送上下文', async () => {
    const history = vi.spyOn(api, 'reportAsks');
    const post = vi.spyOn(api, 'ask').mockResolvedValue(answer('new'));
    const { result } = renderHook(() => useAskConversation({
      ...options, conversationAvailable: false,
    }));
    act(() => { result.current.setDraft('问题'); result.current.setMode('deep'); });
    await act(() => result.current.submit());
    expect(history).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledWith('report', {
      question: '问题', mode: 'deep', contextAskIds: [],
    });
  });

  it('POST未知受理保留快照和锁，刷新无匹配也不能自动解锁', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [], total: 0 });
    const post = vi.spyOn(api, 'ask').mockRejectedValue(new ApiError(0, null));
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    act(() => result.current.setDraft('付费问题'));
    await act(() => result.current.submit());
    expect(result.current.stream.state.phase).toBe('submission-unknown');
    await act(() => result.current.reloadHistory());
    await act(() => result.current.submit({ question: '重复问题' }));
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.current.isActive).toBe(true);
  });

  it('未知受理唯一匹配后GET核验原id，合并成本轮而不是再POST或重复渲染', async () => {
    const accepted = { ...answer('accepted'), question: '待确认问题',
      mode: 'cite' as const, createdAt: new Date().toISOString() };
    vi.spyOn(api, 'reportAsks')
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValue({ items: [accepted], total: 1 });
    const post = vi.spyOn(api, 'ask').mockRejectedValue(new ApiError(0, null));
    const get = vi.spyOn(api, 'askById').mockResolvedValue(accepted);
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    await act(() => result.current.submit({ question: '待确认问题' }));
    await act(() => result.current.reloadHistory());
    await waitFor(() => expect(result.current.isActive).toBe(false));
    expect(get).toHaveBeenCalledWith('accepted');
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.current.turns).toHaveLength(1);
    expect(result.current.turns[0]?.askId).toBe('accepted');
  });

  it('未知受理不能绑定发送前已知的同题旧轮次并解除收费锁', async () => {
    const old = { ...answer('known'), question: '同题问题',
      mode: 'cite' as const, createdAt: new Date().toISOString() };
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [old], total: 1 });
    vi.spyOn(api, 'ask').mockRejectedValue(new ApiError(0, null));
    const get = vi.spyOn(api, 'askById').mockResolvedValue(old);
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    await act(() => result.current.submit({ question: '同题问题' }));
    await act(() => result.current.reloadHistory());
    expect(result.current.stream.state.phase).toBe('submission-unknown');
    expect(result.current.isActive).toBe(true);
    expect(get).not.toHaveBeenCalled();
  });

  it('先恢复最新活动任务，不POST；身份切换取消旧历史响应', async () => {
    vi.stubGlobal('EventSource', ControlledSource);
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [answer('active', 'running')], total: 1 });
    vi.spyOn(api, 'askById').mockResolvedValue(answer('active', 'running'));
    vi.spyOn(api, 'askEventsToken').mockResolvedValue({ token: 't', expiresIn: 60 });
    const post = vi.spyOn(api, 'ask');
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(ControlledSource.instances).toHaveLength(1));
    expect(result.current.isActive).toBe(true);
    expect(post).not.toHaveBeenCalled();
    expect(ControlledSource.instances[0]?.url).not.toContain('after=');
  });

  it('身份变化立刻清除草稿和旧回答，迟到历史不能进入新身份', async () => {
    let finishOld!: (value: { items: AskOut[]; total: number }) => void;
    let oldSignal: AbortSignal | undefined;
    vi.spyOn(api, 'reportAsks')
      .mockImplementationOnce((_report, _limit, request) => {
        oldSignal = request?.signal;
        return new Promise((resolve) => { finishOld = resolve; });
      })
      .mockResolvedValue({ items: [], total: 0 });
    const { result, rerender } = renderHook(
      ({ identityKey }) => useAskConversation({ ...options, identityKey }),
      { initialProps: { identityKey: 'alice' } },
    );
    act(() => result.current.setDraft('Alice的私密草稿'));
    rerender({ identityKey: 'bob' });
    await act(async () => {
      finishOld({ items: [answer('private')], total: 1 });
    });
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    expect(oldSignal?.aborted).toBe(true);
    expect(result.current.turns).toEqual([]);
    expect(result.current.draft).toBe('');
    expect(result.current.contextCandidateIds).toEqual([]);
  });

  it('关闭重开保留草稿与已完成问答，不重新POST', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [answer()], total: 1 });
    const post = vi.spyOn(api, 'ask');
    const { result, rerender } = renderHook(
      ({ open }) => useAskConversation({ ...options, open }),
      { initialProps: { open: true } },
    );
    await waitFor(() => expect(result.current.turns).toHaveLength(1));
    act(() => result.current.setDraft('下次继续'));
    rerender({ open: false });
    rerender({ open: true });
    expect(result.current.draft).toBe('下次继续');
    expect(result.current.turns[0]?.askId).toBe('old');
    expect(post).not.toHaveBeenCalled();
  });

  it('同步重复提交只创建一轮、只POST一次，下一条草稿不污染当前快照', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [], total: 0 });
    let finish!: (value: AskOut) => void;
    const post = vi.spyOn(api, 'ask').mockReturnValue(new Promise((resolve) => {
      finish = resolve;
    }));
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    let first!: Promise<void>;
    act(() => {
      first = result.current.submit({ question: '本轮问题' });
      void result.current.submit({ question: '本轮问题' });
      result.current.setDraft('下一条草稿');
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.current.turns).toHaveLength(1);
    await act(async () => {
      finish({ ...answer('one'), question: '本轮问题', mode: 'cite' });
      await first;
    });
    expect(result.current.turns[0]?.question).toBe('本轮问题');
    expect(result.current.draft).toBe('下一条草稿');
  });

  it('cite降级使用付费请求原问题快照且永不携带上下文', async () => {
    vi.spyOn(api, 'reportAsks').mockResolvedValue({ items: [answer()], total: 1 });
    const post = vi.spyOn(api, 'ask')
      .mockRejectedValueOnce(new ApiError(429, { error: {
        code: 'too_many_asks', message: '额度不足', requestId: '-', detail: {},
      } }))
      .mockResolvedValue({ ...answer('cite'), mode: 'cite' });
    const { result } = renderHook(() => useAskConversation(options));
    await waitFor(() => expect(result.current.historyStatus).toBe('ready'));
    act(() => { result.current.setDraft('付费问题'); result.current.setMode('deep'); });
    await act(() => result.current.submit());
    act(() => result.current.setDraft('下一条草稿'));
    await act(() => result.current.stream.confirmFallback());
    expect(post).toHaveBeenLastCalledWith('report', {
      question: '付费问题', mode: 'cite', contextAskIds: [],
    });
    expect(result.current.draft).toBe('下一条草稿');
    expect(result.current.turns.at(-1)?.phase).toBe('done');
    expect(result.current.turns.at(-1)?.askId).toBe('cite');
    expect(result.current.turns.at(-1)?.mode).toBe('cite');
  });

  it('错误轮次保留原问题与模式，改变下一轮模式不会改写失败快照', async () => {
    const post = vi.spyOn(api, 'ask').mockRejectedValueOnce(new ApiError(422, {
      error: { code: 'invalid_question', message: '拒绝',
        requestId: '-', detail: {} },
    })).mockResolvedValue(answer('retry'));
    const { result } = renderHook(() => useAskConversation({
      ...options, conversationAvailable: false,
    }));
    await act(() => result.current.submit({ question: '原问题', mode: 'deep' }));
    act(() => { result.current.setMode('cite'); result.current.setDraft('新草稿'); });
    expect(result.current.turns[0]?.mode).toBe('deep');
    await act(() => result.current.retry(result.current.turns[0]!));
    expect(post).toHaveBeenLastCalledWith('report', {
      question: '原问题', mode: 'deep', contextAskIds: [],
    });
    expect(result.current.draft).toBe('新草稿');
  });

  it('NFKC展开后检查产品限制，emoji的raw限制按Unicode码点计算', async () => {
    const post = vi.spyOn(api, 'ask').mockResolvedValue(answer('unicode'));
    const { result } = renderHook(() => useAskConversation({
      ...options, conversationAvailable: false, questionMaxChars: 4000,
    }));
    act(() => result.current.setDraft('😀'.repeat(2500)));
    expect(result.current.canSubmit).toBe(true);
    await act(() => result.current.submit());
    expect(post).toHaveBeenCalledTimes(1);
    act(() => result.current.setDraft('ﷺ'.repeat(400)));
    expect(result.current.canSubmit).toBe(false);
    await act(() => result.current.submit());
    expect(post).toHaveBeenCalledTimes(1);
  });

  it.each([0, -1, Number.NaN, 3.5, Number.POSITIVE_INFINITY])(
    '问题上限=%s时使用500，界面与发送校验保持一致', async (limit) => {
      const post = vi.spyOn(api, 'ask').mockResolvedValue(answer('limit'));
      const { result } = renderHook(() => useAskConversation({
        ...options, conversationAvailable: false, questionMaxChars: limit,
      }));
      act(() => result.current.setDraft('有效问题'));
      expect(result.current.canSubmit).toBe(true);
      await act(() => result.current.submit());
      expect(post).toHaveBeenCalledTimes(1);
      act(() => result.current.setDraft('字'.repeat(501)));
      expect(result.current.canSubmit).toBe(false);
      await act(() => result.current.submit());
      expect(post).toHaveBeenCalledTimes(1);
    },
  );

  it('配置超过4000时仍执行Pydantic硬上限', async () => {
    const post = vi.spyOn(api, 'ask').mockResolvedValue(answer('limit'));
    const { result } = renderHook(() => useAskConversation({
      ...options, conversationAvailable: false, questionMaxChars: 5000,
    }));
    act(() => result.current.setDraft('字'.repeat(4001)));
    expect(result.current.canSubmit).toBe(false);
    await act(() => result.current.submit());
    expect(post).not.toHaveBeenCalled();
    act(() => result.current.setDraft('字'.repeat(4000)));
    expect(result.current.canSubmit).toBe(true);
  });

  it('历史读取失败保留草稿，显式跳过后才能开始新问题', async () => {
    vi.spyOn(api, 'reportAsks').mockRejectedValue(new Error('offline'));
    const post = vi.spyOn(api, 'ask').mockResolvedValue(answer('new'));
    const { result } = renderHook(() => useAskConversation(options));
    act(() => result.current.setDraft('草稿'));
    await waitFor(() => expect(result.current.historyStatus).toBe('error'));
    await act(() => result.current.submit());
    expect(post).not.toHaveBeenCalled();
    expect(result.current.draft).toBe('草稿');
    act(() => result.current.startWithoutHistory());
    await act(() => result.current.submit());
    expect(post).toHaveBeenCalledTimes(1);
    expect(result.current.hasStartedNewTopic).toBe(true);
  });
});
