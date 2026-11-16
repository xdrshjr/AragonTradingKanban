import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AskPanel, type AskPanelProps } from '@/components/ask/AskPanel';
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
}

/** 用真的受控父层驱动 `open`/`onOpenChange`，这样才能测到"Esc 之后面板真的关掉了"。 */
function Harness({ onJumpToBlock, onJumpToSource, deepAvailable = true, dailyLimit = 5, initialOpen = true }: HarnessProps) {
  const [open, setOpen] = useState(initialOpen);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const props: AskPanelProps = {
    reportId: 'rp_1',
    open,
    onOpenChange: setOpen,
    deepAvailable,
    dailyLimit,
    onJumpToBlock: onJumpToBlock ?? (() => {}),
    onJumpToSource,
    triggerRef,
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
    expect(calls[0]?.body).toMatchObject({ question: QUESTION, mode: 'cite' });
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
    expect((calls[1]?.body as { question?: string }).question).toBe(QUESTION);
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
    expect(screen.queryByRole('region', { name: copy.ask.title })).toBeNull();

    await user.click(trigger);
    const region = await screen.findByRole('region', { name: copy.ask.title });
    expect(within(region).getByRole('textbox', { name: copy.ask.questionLabel })).toHaveFocus();

    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('region', { name: copy.ask.title })).toBeNull());
    expect(trigger).toHaveFocus();
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
