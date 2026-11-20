import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect, useRef, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AskPanel, type AskPanelProps } from '@/components/ask/AskPanel';
import { AskAnswer } from '@/components/ask/AskAnswer';
import { AskConversation } from '@/components/ask/AskConversation';
import { AskPanelBoundary, AskPanelLoading } from '@/components/ask/AskPanelBoundary';
import { AssistantLauncher } from '@/components/ask/AssistantLauncher';
import { initialAskStreamState } from '@/components/ask/useAskStream';
import type { AskTurnView } from '@/components/ask/useAskConversation';
import { copy, fill } from '@/lib/copy';
import type { AskHit, AskOut } from '@/lib/types';

/**
 * AC-26 / AC-27 / AC-36 的前端面（spec §18.3.2 / §18.3.6 / §12.2）。
 *
 * `global.fetch` 逐条 stub：这里没有网络，也**不允许**有——断言的是"发了什么请求"，
 * 所以每个用例都要能看到请求体（尤其是 429 之后那次必须带 `mode: 'cite'`）。
 */

type FetchInput = RequestInfo | URL;

const jsonResponse = (body: unknown, status = 200): Response =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }) as unknown as Response;

const HIT: AskHit = {
  blockId: 'business#3',
  sectionId: 'business',
  sectionTitle: '业务与竞争',
  blockType: 'metrics',
  score: 0.82,
  excerpt: '毛利率 62.1%（同比 -3.4pct），主因直营渠道占比提升。',
  citations: ['S3'],
};

const QUESTION = '毛利率下滑的主因是什么？';

const CITE_ANSWER: AskOut = {
  id: 'ask_cite_1',
  reportId: 'rp_1',
  question: QUESTION,
  mode: 'cite',
  status: 'done',
  grounded: true,
  confidence: 0.82,
  citationsDropped: 0,
  answer: {
    hits: [HIT],
    citations: [{ sourceId: 'S3', title: '2025 年年度报告', publisher: '上交所' }],
    hint: null,
  },
  costKnown: false,
  createdAt: '2026-02-10T09:00:00Z',
};

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

const recordCall = (input: FetchInput, init?: RequestInit): Recorded => ({
  url: String(input),
  method: init?.method ?? 'GET',
  body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null,
});

interface HarnessProps {
  onJumpToBlock?: (blockId: string, sectionId: string) => void;
  onJumpToSource?: (sourceId: string) => void;
  deepAvailable?: boolean;
  dailyLimit?: number;
  initialOpen?: boolean;
  questionMaxChars?: number;
}

/** 用真的受控父层驱动 `open`/`onOpenChange`，这样才能测到"Esc 之后面板真的关掉了"。 */
function Harness({ onJumpToBlock, onJumpToSource, deepAvailable = true, dailyLimit = 5, initialOpen = true, questionMaxChars }: HarnessProps) {
  const [open, setOpen] = useState(initialOpen);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (!open || event.defaultPrevented || event.isComposing) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
      }
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [open]);

  const props: AskPanelProps = {
    reportId: 'rp_1',
    open,
    onOpenChange: setOpen,
    deepAvailable,
    dailyLimit,
    onJumpToBlock: onJumpToBlock ?? (() => {}),
    onJumpToSource,
    triggerRef,
    questionMaxChars,
  };

  return (
    <div>
      <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>
        {copy.ask.open}
      </button>
      <AskPanel {...props} />
    </div>
  );
}

const questionBox = () => screen.getByRole('textbox', { name: copy.ask.questionLabel });
const panel = () => screen.getByRole('region', { name: copy.ask.title });

async function askCiteQuestion(user: ReturnType<typeof userEvent.setup>, text = QUESTION) {
  await user.type(questionBox(), text);
  await user.click(screen.getByRole('button', { name: copy.ask.submit }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('AI 助手 — 输入与浮窗', () => {
  it('停止网络失败保留活动预览并明确提示停止未确认', () => {
    render(<AskAnswer state={{ ...initialAskStreamState,
      phase: 'streaming', streamText: '已收到的预览',
      error: { code: 'cancel_unconfirmed', message: copy.ask.cancelUnconfirmed },
    }} onJumpToBlock={vi.fn()} />);
    expect(screen.getByText('已收到的预览')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(copy.ask.cancelUnconfirmed);
  });

  it('入口随可视视口平移，紧凑形态展开后隐藏入口', async () => {
    const viewport = new EventTarget() as EventTarget & {
      width: number; height: number; offsetLeft: number; offsetTop: number;
    };
    Object.assign(viewport, {
      width: 390, height: 320, offsetLeft: 20, offsetTop: 30,
    });
    vi.stubGlobal('visualViewport', viewport);
    const { rerender } = render(<AssistantLauncher open={false} onClick={vi.fn()} />);
    const button = await screen.findByRole('button', { name: copy.ask.launcherOpen });
    await waitFor(() => expect(button.style.getPropertyValue('--assistant-vh'))
      .toBe('320px'));
    expect(button.style.getPropertyValue('--assistant-top')).toBe('30px');
    expect(button.style.getPropertyValue('--assistant-right'))
      .toBe(`${window.innerWidth - 410}px`);
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).toHaveAttribute('aria-controls', 'aragon-ai-assistant');
    rerender(<AssistantLauncher open onClick={vi.fn()} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('资源载入失败仍提供关闭和重新加载入口，不发送问题', async () => {
    const fetchMock = vi.fn();
    const close = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    function FailedChunk(): never { throw new Error('ChunkLoadError'); }
    render(<AskPanelBoundary onClose={close}><FailedChunk /></AskPanelBoundary>);
    expect(await screen.findByText(copy.ask.loadingFailed)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: copy.ask.reloadPage })).toBeEnabled();
    await userEvent.setup().click(screen.getByRole('button', { name: copy.ask.close }));
    expect(close).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('资源失败后可真正收起，重开保留恢复提示', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    function FailedChunk(): never { throw new Error('ChunkLoadError'); }
    function BoundaryHarness() {
      const [open, setOpen] = useState(true);
      return <>
        <button onClick={() => setOpen(true)}>重新打开助手</button>
        <AskPanelBoundary open={open} onClose={() => setOpen(false)}>
          <FailedChunk />
        </AskPanelBoundary>
      </>;
    }
    render(<BoundaryHarness />);
    await screen.findByText(copy.ask.loadingFailed);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: copy.ask.close }));
    expect(screen.queryByRole('region', { name: copy.ask.title })).toBeNull();
    await user.click(screen.getByRole('button', { name: '重新打开助手' }));
    expect(await screen.findByText(copy.ask.loadingFailed)).toBeInTheDocument();
  });

  it('资源失败时收起也将焦点交还实际触发按钮', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    function FailedChunk(): never { throw new Error('ChunkLoadError'); }
    function FocusHarness() {
      const [open, setOpen] = useState(false);
      const triggerRef = useRef<HTMLButtonElement | null>(null);
      return <>
        <button ref={triggerRef} onClick={() => setOpen(true)}>打开失败助手</button>
        <AskPanelBoundary open={open} triggerRef={triggerRef}
          onClose={() => setOpen(false)}><FailedChunk /></AskPanelBoundary>
      </>;
    }
    render(<FocusHarness />);
    const trigger = screen.getByRole('button', { name: '打开失败助手' });
    vi.spyOn(trigger, 'getClientRects').mockReturnValue([
      { width: 100, height: 32 },
    ] as unknown as DOMRectList);
    const user = userEvent.setup();
    await user.click(trigger);
    await screen.findByText(copy.ask.loadingFailed);
    await user.click(screen.getByRole('button', { name: copy.ask.close }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('资源加载期间收起使用挂载前的触发按钮归还焦点', async () => {
    function LoadingHarness() {
      const [open, setOpen] = useState(false);
      const [mounted, setMounted] = useState(false);
      return <>
        <button onClick={() => { setMounted(true); setOpen(true); }}>打开加载助手</button>
        {mounted ? <AskPanelLoading open={open}
          onClose={() => setOpen(false)} /> : null}
      </>;
    }
    render(<LoadingHarness />);
    const trigger = screen.getByRole('button', { name: '打开加载助手' });
    vi.spyOn(trigger, 'getClientRects').mockReturnValue([
      { width: 100, height: 32 },
    ] as unknown as DOMRectList);
    const user = userEvent.setup();
    await user.click(trigger);
    await screen.findByText(copy.ask.loadingPanel);
    await user.click(screen.getByRole('button', { name: copy.ask.close }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('上翻阅读时不抢滚动位置，点击新回答提示才回底部', async () => {
    const turn: AskTurnView = {
      localId: 'local-1', askId: 'ask-1', question: QUESTION,
      mode: 'cite', contextAskIds: [], phase: 'done',
      result: CITE_ANSWER, preview: '', error: null,
    };
    const props = {
      turns: [turn], streamState: initialAskStreamState,
      historyStatus: 'ready' as const, historyEnabled: false,
      identityKey: 'open', onSuggestion: vi.fn(), onReloadHistory: vi.fn(),
      onStartWithoutHistory: vi.fn(), onRetry: vi.fn(), onRefreshTurn: vi.fn(),
      onRefresh: vi.fn(), onReconnect: vi.fn(), onAcknowledgeDuplicate: vi.fn(),
      onJumpToBlock: vi.fn(),
    };
    const { container, rerender } = render(<AskConversation {...props} />);
    const messages = container.querySelector('[data-assistant-messages]')!;
    Object.defineProperty(messages, 'scrollHeight', { value: 1200 });
    Object.defineProperty(messages, 'clientHeight', { value: 300 });
    const scroll = vi.spyOn(messages, 'scrollTo');
    fireEvent.scroll(messages);
    rerender(<AskConversation {...props} turns={[turn, {
      ...turn, localId: 'local-2', askId: 'ask-2', question: '继续研究',
    }]} />);
    await screen.findByRole('button', { name: copy.ask.bottom });
    expect(scroll).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole('button', { name: copy.ask.bottom }));
    expect(scroll).toHaveBeenCalledWith({ top: 1200 });
  });

  it('收起再打开保留草稿，且不会自动发送问题', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(questionBox(), '待继续研究的问题');
    await user.click(screen.getByRole('button', { name: copy.ask.close }));
    expect(screen.queryByRole('region', { name: copy.ask.title })).toBeNull();
    await user.click(screen.getByRole('button', { name: copy.ask.open }));
    expect(questionBox()).toHaveValue('待继续研究的问题');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('连续发送后保留前轮回答和来源核对入口', async () => {
    let count = 0;
    vi.stubGlobal('fetch', vi.fn(async (_input: FetchInput, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as { question: string };
      count += 1;
      return jsonResponse({ ...CITE_ANSWER, id: `ask_${count}`,
        question: body.question,
        createdAt: `2026-11-20T10:00:0${count}Z` });
    }));
    const user = userEvent.setup();
    render(<Harness />);
    await askCiteQuestion(user);
    await screen.findByRole('button', { name: /业务与竞争/ });
    await askCiteQuestion(user, '还有哪些风险？');
    await waitFor(() => expect(screen.getAllByRole('button', {
      name: /业务与竞争/,
    })).toHaveLength(2));
    expect(screen.getByText(QUESTION.normalize('NFKC'))).toBeInTheDocument();
    expect(screen.getByText('还有哪些风险?')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /引用 S3/ })).toHaveLength(2);
  });

  it('按 NFKC 后的码点验证上限，允许修改超限草稿', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<Harness questionMaxChars={2} />);

    fireEvent.change(questionBox(), { target: { value: '㍿' } });
    expect(questionBox()).toHaveValue('㍿');
    expect(screen.getByRole('button', { name: copy.ask.submit }))
      .toBeDisabled();
    expect(screen.getByText('4/2')).toBeInTheDocument();
    fireEvent.change(questionBox(), { target: { value: '😀😀' } });
    expect(screen.getByRole('button', { name: copy.ask.submit }))
      .toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('输入法组合期间 Ctrl+Enter 不提交，结束后才允许发送', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<Harness />);
    fireEvent.change(questionBox(), { target: { value: QUESTION } });
    fireEvent.compositionStart(questionBox());
    fireEvent.keyDown(questionBox(), {
      key: 'Enter', ctrlKey: true, keyCode: 229, isComposing: true,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('面板 portal 到 body，建议问题仅填草稿，不自动请求', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<Harness />);
    expect(container.querySelector('[role="region"]')).toBeNull();
    expect(panel().parentElement).toBe(document.body);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '总结核心结论' }));
    expect(questionBox()).toHaveValue('总结核心结论');
    expect(questionBox()).toHaveFocus();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('AskPanel — 引用检索（cite）', () => {
  it('渲染命中块原文，点击命中块把 blockId/sectionId 交给报告面板', async () => {
    const calls: Recorded[] = [];
    const fetchMock = vi.fn(async (input: FetchInput, init?: RequestInit) => {
      calls.push(recordCall(input, init));
      return jsonResponse(CITE_ANSWER, 200);
    });
    vi.stubGlobal('fetch', fetchMock);

    const onJumpToBlock = vi.fn();
    const user = userEvent.setup();
    render(<Harness onJumpToBlock={onJumpToBlock} />);

    await askCiteQuestion(user);

    const hitButton = await screen.findByRole('button', { name: /业务与竞争/ });
    expect(within(hitButton).getByText(/毛利率 62\.1%/)).toBeInTheDocument();
    expect(screen.getByText(fill(copy.ask.hitsTitle, { count: 1 }))).toBeInTheDocument();

    await user.click(hitButton);
    expect(onJumpToBlock).toHaveBeenCalledTimes(1);
    expect(onJumpToBlock).toHaveBeenCalledWith(HIT.blockId, HIT.sectionId);

    // 默认档就是 cite：请求体里必须显式带上 mode，绝不隐式升级到 deep。
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toContain('/api/v1/reports/rp_1/ask');
    expect(calls[0]?.method).toBe('POST');
    expect(calls[0]?.body).toMatchObject({
      question: QUESTION.normalize('NFKC'), mode: 'cite',
    });
  });

  it('引用 chip 点击时交给 onJumpToSource', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(CITE_ANSWER, 200)),
    );

    const onJumpToSource = vi.fn();
    const user = userEvent.setup();
    render(<Harness onJumpToSource={onJumpToSource} />);

    await askCiteQuestion(user);

    await user.click(await screen.findByRole('button', { name: /引用 S3/ }));
    expect(onJumpToSource).toHaveBeenCalledWith('S3');
  });
});

describe('AskPanel — 429 额度（AC-27）', () => {
  it('用户额度拒绝显示真实原因，不以每报告上限冒充账户额度', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ error: {
      code: 'quota_exceeded', message: '今日深度追问额度已用完',
      requestId: 'quota-user', detail: { resource: 'deep_ask',
        scope: 'user', limit: 2, usedToday: 2, bonus: 0 },
    } }, 429)));
    const user = userEvent.setup();
    render(<Harness dailyLimit={30} />);
    await user.type(questionBox(), QUESTION);
    await user.click(screen.getByRole('radio', { name: copy.ask.modeDeep }));
    await user.click(screen.getByRole('button', { name: copy.ask.deepSubmit }));
    const quota = await screen.findByRole('alertdialog');
    expect(within(quota).getByText('今日深度追问额度已用完'))
      .toBeInTheDocument();
    expect(quota).not.toHaveTextContent('30/30');
  });

  it('不静默降级：先显示确认，用户确认后才以 mode=cite 重发', async () => {
    const calls: Recorded[] = [];
    const fetchMock = vi.fn(async (input: FetchInput, init?: RequestInit) => {
      const call = recordCall(input, init);
      calls.push(call);
      const mode = (call.body as { mode?: string } | null)?.mode;
      if (mode === 'deep') {
        // 后端的 429 封套：code + detail.retryAfterMs + detail.suggestMode
        return jsonResponse(
          {
            error: {
              code: 'too_many_asks',
              message: '今日深度追问额度已用完',
              requestId: 'req-429',
              detail: { retryAfterMs: 3_600_000, suggestMode: 'cite' },
            },
          },
          429,
        );
      }
      return jsonResponse(CITE_ANSWER, 200);
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Harness dailyLimit={5} />);

    await user.type(questionBox(), QUESTION);
    const modeGroup = screen.getByRole('radiogroup', { name: copy.ask.modeGroup });
    await user.click(within(modeGroup).getByRole('radio', { name: copy.ask.modeDeep }));
    await user.click(screen.getByRole('button', { name: copy.ask.deepSubmit }));

    // 1) 显式确认，而不是默默改成 cite
    expect(await screen.findByText(fill(copy.ask.quotaTitle, { limit: 5 }))).toBeInTheDocument();
    expect(screen.getByText(copy.ask.quotaBody)).toBeInTheDocument();
    expect(calls).toHaveLength(1);
    expect((calls[0]?.body as { mode?: string }).mode).toBe('deep');
    expect(screen.queryByRole('button', { name: /业务与竞争/ })).toBeNull();

    // 2) 确认之后才发第二次，且必须是 cite
    await user.click(screen.getByRole('button', { name: copy.ask.quotaAccept }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect((calls[1]?.body as { mode?: string }).mode).toBe('cite');
    expect((calls[1]?.body as { question?: string }).question)
      .toBe(QUESTION.normalize('NFKC'));
    expect(await screen.findByRole('button', { name: /业务与竞争/ })).toBeInTheDocument();
  });

  it('拒绝确认时保持原有状态，不偷偷发请求', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: 'too_many_asks',
            message: '今日深度追问额度已用完',
            requestId: 'req-429',
            detail: { retryAfterMs: 3_600_000 },
          },
        },
        429,
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<Harness />);

    await user.type(questionBox(), QUESTION);
    await user.click(within(screen.getByRole('radiogroup', { name: copy.ask.modeGroup })).getByRole('radio', { name: copy.ask.modeDeep }));
    await user.click(screen.getByRole('button', { name: copy.ask.deepSubmit }));

    await screen.findByText(copy.ask.quotaBody);
    await user.click(screen.getByRole('button', { name: copy.ask.quotaDecline }));

    expect(screen.queryByText(copy.ask.quotaBody)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('AskPanel — 深度追问（deep）', () => {
  /** jsdom 没有 EventSource；这份假实现让"流式"变成可控的事件注入。 */
  class FakeEventSource {
    static instances: FakeEventSource[] = [];
    url: string;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    closed = false;
    private listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();

    constructor(url: string) {
      this.url = url;
      FakeEventSource.instances.push(this);
    }

    addEventListener(type: string, handler: (event: MessageEvent<string>) => void) {
      const list = this.listeners.get(type) ?? [];
      list.push(handler);
      this.listeners.set(type, list);
    }

    close() {
      this.closed = true;
    }

    emit(type: string, payload: unknown, seq: number) {
      for (const handler of this.listeners.get(type) ?? []) {
        handler({ data: JSON.stringify(payload), lastEventId: String(seq) } as MessageEvent<string>);
      }
    }
  }

  it('202 之后订阅 ask.* 事件流，终态渲染 answerText + 引用 + 置信度', async () => {
    FakeEventSource.instances = [];

    const deepAnswer: AskOut = {
      ...CITE_ANSWER,
      id: 'ask_deep_1',
      mode: 'deep',
      grounded: true,
      confidence: 0.9,
      answer: {
        answerText: '主因是直营渠道占比从 38% 提升到 46%，压低整体毛利率约 3.1pct。',
        hits: [],
        citations: [{ sourceId: 'S3', title: '2025 年年度报告', publisher: '上交所' }],
        hint: null,
      },
      costUsd: 0.04,
      costKnown: true,
      latencyMs: 23_000,
    };

    const calls: Recorded[] = [];
    const fetchMock = vi.fn(async (input: FetchInput, init?: RequestInit) => {
      const call = recordCall(input, init);
      calls.push(call);
      if (call.url.includes('/events-token')) return jsonResponse({ token: 'tok', expiresIn: 60 }, 200);
      if (call.method === 'POST' && call.url.endsWith('/ask')) {
        return jsonResponse({ id: 'ask_deep_1', askId: 'ask_deep_1', status: 'queued', streamUrl: '/api/v1/asks/ask_deep_1/events' }, 202);
      }
      return jsonResponse(deepAnswer, 200);
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);

    const user = userEvent.setup();
    render(<Harness />);

    await user.type(questionBox(), QUESTION);
    await user.click(within(screen.getByRole('radiogroup', { name: copy.ask.modeGroup })).getByRole('radio', { name: copy.ask.modeDeep }));
    await user.click(screen.getByRole('button', { name: copy.ask.deepSubmit }));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0]!;
    // token 走短签名，绑定的是 askId（§18.5.2 v4 修订 V4-P1-4）
    expect(source.url).toContain('/api/v1/asks/ask_deep_1/events');
    expect(source.url).toContain('token=tok');

    await act(async () => {
      source.emit('ask.stage', { stage: 'research', pct: 40, label: '正在检索报告…' }, 1);
    });
    expect(await screen.findByText('正在检索报告…')).toBeInTheDocument();

    await act(async () => {
      source.emit('ask.delta', { text: '主因是直营渠道占比' }, 2);
    });
    expect(await screen.findByText('主因是直营渠道占比')).toBeInTheDocument();

    await act(async () => {
      source.emit(
        'ask.done',
        {
          askId: 'ask_deep_1',
          grounded: true,
          confidence: 0.9,
          citations: [{ sourceId: 'S3', title: '2025 年年度报告', publisher: '上交所' }],
          costUsd: 0.04,
          costKnown: true,
          citationsDropped: 0,
        },
        3,
      );
    });

    // 终态再取一次完整 AskOut：正文与命中块只有在库里才是权威的
    expect(await screen.findByText(/直营渠道占比从 38%/)).toBeInTheDocument();
    expect(source.closed).toBe(true);
    expect(screen.getByText(copy.ask.confidence.high)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /引用 S3/ })).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'GET' && call.url.includes('/api/v1/asks/ask_deep_1'))).toBe(true);
  });
});

describe('AskPanel — 未接地（临时版报告）', () => {
  it('grounded=false 且带 hint 时显示 hint，而不是空盒子', async () => {
    const ungrounded: AskOut = {
      ...CITE_ANSWER,
      id: 'ask_cite_2',
      grounded: false,
      confidence: null,
      citationsDropped: 2,
      answer: { hits: [], citations: [], hint: copy.ask.provisionalHint },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(ungrounded, 200)),
    );

    const user = userEvent.setup();
    render(<Harness />);
    await askCiteQuestion(user);

    expect(await screen.findByText(copy.ask.provisionalHint)).toBeInTheDocument();
    // 丢弃的引用要看得见，不能只体现在 grounded 上
    expect(screen.getByText(fill(copy.ask.droppedNotice, { count: 2 }))).toBeInTheDocument();
    expect(screen.queryByText(copy.ask.notGrounded)).toBeNull();
  });
});

describe('AskPanel — 可访问性（AC-36）', () => {
  it('Esc 关闭抽屉，焦点回到触发元素', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(CITE_ANSWER, 200)),
    );

    const user = userEvent.setup();
    render(<Harness initialOpen={false} />);

    const trigger = screen.getByRole('button', { name: copy.ask.open });
    // jsdom has no layout; model this connected visible button explicitly.
    vi.spyOn(trigger, 'getClientRects').mockReturnValue([
      { width: 80, height: 32 },
    ] as unknown as DOMRectList);
    expect(screen.queryByRole('region', { name: copy.ask.title })).toBeNull();

    await user.click(trigger);
    const region = await screen.findByRole('region', { name: copy.ask.title });
    expect(within(region).getByRole('textbox', { name: copy.ask.questionLabel })).toHaveFocus();

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('region', { name: copy.ask.title })).toBeNull());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('答案区有 aria-live，控件都能用 Tab 到达（无焦点陷阱）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(CITE_ANSWER, 200)),
    );

    const user = userEvent.setup();
    render(<Harness />);

    const region = panel();
    expect(region).toHaveAttribute('role', 'region');
    const live = within(region).getByLabelText(copy.a11y.askLiveRegion);
    expect(live).toHaveAttribute('aria-live', 'polite');

    // 输入框 → Tab → 提交按钮：Tab 顺序在面板内部正常推进。
    const box = questionBox();
    await user.type(box, QUESTION);
    box.focus();
    await user.tab();
    expect(screen.getByRole('button', { name: copy.ask.submit })).toHaveFocus();
    expect(box).not.toHaveFocus();
  });
});
