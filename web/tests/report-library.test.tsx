import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ReportLibraryClient, type ReportLibraryClientProps } from '@/components/library/ReportLibraryClient';
import { ReportLibraryRow } from '@/components/library/ReportLibraryRow';
import { copy } from '@/lib/copy';
import { libraryHref } from '@/lib/library';
import type { LibraryResponse, ReportLibraryItem, ReportSummary } from '@/lib/types';

/**
 * ★v14（U1 / U7 / spec §23.3.8 / AC-137 / AC-143 / AC-150）：报告库的一行 + 整张列表。
 *
 * 这组用例守的是四种"看起来能用、其实在骗人"的实现：
 *
 * 1. **行链接不带 `?report=`**（AC-143 / U-0b）。点了 v12 却打开 v13，界面完全正常，
 *    只有读的人会发现结论对不上——所以断言 href 的形状，而不只是"有 a 标签"。
 * 2. **stance 只靠颜色**（§23.3.8）。色条 + 无文字徽章对色盲用户等于没有结论；
 *    所以每种 stance 都必须能找到 `copy.report.stance[...]` 的文字。
 * 3. **回收站的行混进星标/永久删除**（AC-150）。回收站只给「恢复」；且"还剩 N 天"
 *    必须由 `deletedAt` 算出来（假时钟固定"今天"，否则这条断言只是运气）。
 * 4. **挂载后又发一次 `/reports`**（★P2-10 / AC-137）。`initialData` 配
 *    `initialDataUpdatedAt` 才能让首屏那一页真的被复用；漏了 `initialDataUpdatedAt`
 *    TanStack 会把它当成"无限陈旧"，于是每个首屏都白跑一次网络——首屏预算与
 *    AC-137 的"只发一次"同时失效，而界面上看不出任何区别。本文件用"计数器为零"
 *    外加一个**反面样本**（同一夹具去掉 `initialData` 后请求确实发出）守住它。
 *
 * 组件的三个状态（空库 / 搜不到 / 回收站空）文案各不相同，也在这里各断言一次：
 * 一句"暂无数据"是敷衍（§23.3.8 第 4 条）。
 */

/* ── 夹具 ────────────────────────────────────────────────────────────────── */

/** AC-143 的正则要求 32 位小写 hex —— 夹具也用同形的 id，别让用例因为夹具而永远绿。 */
const REPORT_ID = '0123456789abcdef0123456789abcdef';
const SYMBOL = '600519.SH';
const NAME = '贵州茅台';
const HEADLINE = '毛利率承压，维持中性';

function makeItem(
  item: Partial<ReportLibraryItem> = {},
  report: Partial<ReportSummary> = {},
): ReportLibraryItem {
  return {
    report: {
      id: REPORT_ID,
      instrumentId: 'CN:600519.SH',
      version: 12,
      status: 'ready',
      title: `${NAME} · 投研报告`,
      headline: HEADLINE,
      stance: 'hold',
      asOf: '2026-02-01',
      generatedAt: '2026-02-09T12:00:00.000Z',
      isLatest: false,
      pinned: false,
      archived: false,
      provisional: false,
      groundedRatio: 0.87,
      quality: { groundedRatio: 0.87, missingSections: [], prunedBlocks: 0, source: 'aragon' },
      costKnown: false,
      docBytes: 2048,
      ...report,
    },
    instrument: { id: 'CN:600519.SH', symbol: SYMBOL, name: NAME, market: 'CN', currency: 'CNY' },
    docSha1: null,
    integrity: 'ok',
    deletedAt: null,
    ...item,
  };
}

function makeLibraryResponse(
  items: ReportLibraryItem[],
  overrides: Partial<LibraryResponse> = {},
): LibraryResponse {
  return {
    items,
    nextCursor: null,
    total: items.length,
    counts: { latest: items.length, all: items.length, starred: 0, archived: 0, trash: 0 },
    active: [],
    ...overrides,
  };
}

/* ── 行的用例（AC-143 / §23.3.8 / AC-150）────────────────────────────────── */

describe('ReportLibraryRow · 一行就是一份报告（AC-143）', () => {
  it('★AC-143：整行是一个 libraryHref 链接，标的/版本/标题/可信度都在行里', () => {
    render(<ReportLibraryRow item={makeItem()} />);

    const row = screen.getByRole('option');
    expect(row).toHaveAttribute('data-report-id', REPORT_ID);
    expect(within(row).getByText(NAME)).toBeInTheDocument();
    expect(within(row).getByText(SYMBOL)).toBeInTheDocument();
    expect(within(row).getByText('v12')).toBeInTheDocument();
    expect(within(row).getByText(HEADLINE)).toBeInTheDocument();
    // `groundedRatio` 0.87 → 87%（取整在组件里，唯一实现）
    expect(within(row).getByText('87%')).toBeInTheDocument();

    const link = within(row).getByRole('link', { name: new RegExp(NAME) });
    const href = link.getAttribute('href');
    // AC-143：形状是硬要求——少一个 `?report=` 就会打开"最新版"而不是这一版。
    expect(href).toMatch(/^\/[A-Za-z0-9._-]+\?report=[0-9a-f]{32}$/);
    // 并且与唯一实现 `libraryHref()` 逐字符相等（不在测试里重写一份模板）。
    expect(href).toBe(libraryHref(SYMBOL, REPORT_ID));
  });

  it('★§23.3.8：可信度缺失是「—」，不是 0%（"不知道" ≠ "零可信"）', () => {
    render(
      <ReportLibraryRow
        item={makeItem({}, {
          groundedRatio: null,
          quality: { groundedRatio: 0, missingSections: [], prunedBlocks: 0, source: 'aragon' },
        })}
      />,
    );

    const row = screen.getByRole('option');
    expect(within(row).getByText('—')).toBeInTheDocument();
    expect(within(row).queryByText('0%')).toBeNull();
  });

  it('★§23.3.8：stance 不只靠颜色——四种结论都有文字徽章', () => {
    for (const stance of ['buy', 'hold', 'sell', 'watch'] as const) {
      const { unmount } = render(<ReportLibraryRow item={makeItem({}, { stance })} />);
      expect(within(screen.getByRole('option')).getByText(copy.report.stance[stance])).toBeInTheDocument();
      unmount();
    }
  });

  it('★AC-147/§23.5.4：损坏的正文与临时版各有一枚文字徽章，不靠颜色区分', () => {
    render(<ReportLibraryRow item={makeItem({ integrity: 'corrupt' }, { provisional: true })} />);

    const row = screen.getByRole('option');
    expect(within(row).getByText(copy.library.integrityCorrupt)).toBeInTheDocument();
    expect(within(row).getByText(copy.report.provisional)).toBeInTheDocument();
  });
});

describe('ReportLibraryRow · 回收站模式（AC-150）', () => {
  it('★AC-150：回收站的行只有「恢复」，没有星标按钮（也没有"永久删除"）', async () => {
    const onRestore = vi.fn();
    const item = makeItem({ deletedAt: new Date(Date.now() - 5 * 86_400_000).toISOString() });

    render(<ReportLibraryRow item={item} trash trashRetentionDays={30} onRestore={onRestore} />);

    const restore = screen.getByRole('button', { name: copy.library.actions.restore });
    expect(screen.queryByRole('button', { name: copy.library.actions.star })).toBeNull();
    expect(screen.queryByRole('button', { name: copy.library.actions.unstar })).toBeNull();

    await userEvent.click(restore);
    expect(onRestore).toHaveBeenCalledTimes(1);
    expect(onRestore).toHaveBeenCalledWith(item);
  });

  it('★AC-150：剩余天数由 deletedAt 算（假时钟），文案取自唯一实现', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-02-10T12:00:00.000Z'));
      // 5 天前删除、保留 30 天 ⇒ 还剩 25 天。
      const item = makeItem({ deletedAt: '2026-02-05T12:00:00.000Z' });
      render(<ReportLibraryRow item={item} trash trashRetentionDays={30} />);

      expect(
        screen.getByText(copy.library.trashDaysLeft.replace('{days}', '25')),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('ReportLibraryRow · 星标', () => {
  it('★aria-pressed 反映 pinned；点击把整条 item 交给调用方（不自己写）', async () => {
    const onToggleStar = vi.fn();
    const item = makeItem();

    const first = render(<ReportLibraryRow item={item} onToggleStar={onToggleStar} />);
    const star = screen.getByRole('button', { name: copy.library.actions.star });
    expect(star).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(star);
    expect(onToggleStar).toHaveBeenCalledTimes(1);
    expect(onToggleStar).toHaveBeenCalledWith(item);
    first.unmount();

    render(<ReportLibraryRow item={makeItem({}, { pinned: true })} onToggleStar={vi.fn()} />);
    const unstar = screen.getByRole('button', { name: copy.library.actions.unstar });
    expect(unstar).toHaveAttribute('aria-pressed', 'true');
  });
});

/* ── 列表的用例（AC-137 / AC-150）────────────────────────────────────────── */

/**
 * `ReportLibraryClient` 只认两样外部世界：`next/navigation` 与 `globalThis.fetch`。
 * 前者用 `vi.mock` 给一个可改写的 `searchParams`（`?scope=` / `?q=` 就是这样进来的），
 * 后者逐条 stub —— 这里没有网络，也**不允许**有（断言之一就是"没发请求"）。
 */
const nav = vi.hoisted(() => ({ search: '', replace: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: nav.replace,
    push: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function renderClient(props: Partial<ReportLibraryClientProps> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <ReportLibraryClient
        initialData={makeLibraryResponse([makeItem()])}
        initialDataUpdatedAt={Date.now()}
        initialScope="all"
        enabled
        trashRetentionDays={30}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...view, client };
}

/** 记录每次请求的 URL（`api.reportLibrary()` 拼出来的形状是 `/api/v1/reports?...`）。 */
function requestedUrls(): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input));
}

beforeEach(() => {
  nav.search = '';
  nav.replace.mockReset();
  fetchMock.mockReset();
  // 默认：任何"不该发出"的请求都拿到一个合法的空库，而不是 reject —— 让失败原因只是断言，不是噪声。
  fetchMock.mockImplementation(async () => jsonResponse(makeLibraryResponse([])));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ReportLibraryClient · 列表（AC-137）', () => {
  it('★AC-137：按日分组 + aria-live 播报"已加载 N 份"', () => {
    const today = new Date().toISOString();
    const twoDaysAgo = new Date(Date.now() - 2 * 86_400_000).toISOString();
    renderClient({
      initialData: makeLibraryResponse([
        makeItem({}, { id: 'a'.repeat(32), generatedAt: today }),
        makeItem({}, { id: 'b'.repeat(32), generatedAt: twoDaysAgo }),
      ]),
    });

    expect(screen.getByRole('listbox', { name: copy.library.title })).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(2);
    expect(screen.getByRole('heading', { level: 2, name: copy.library.groups.today })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: copy.library.groups.thisWeek })).toBeInTheDocument();

    // 列表刷新对读屏用户不能是"什么都没发生"（§23.3.8 第 3 条）。
    const live = screen.getByText(copy.library.loaded.replace('{count}', '2'));
    expect(live).toHaveAttribute('aria-live', 'polite');
  });

  it('★§23.3.8：空库说的是「还没有生成的报告」，不是「没有匹配」', () => {
    renderClient({ initialData: makeLibraryResponse([]) });

    expect(screen.getByText(copy.library.empty)).toBeInTheDocument();
    expect(screen.getByText(copy.library.emptyBody)).toBeInTheDocument();
    expect(screen.queryByText(copy.library.emptySearch)).toBeNull();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('★AC-137：搜不到时是另一句话（`q` 进 URL，空库与空搜索结果不共用文案）', () => {
    nav.search = 'q=zzz';
    renderClient({ initialQuery: 'zzz', initialData: makeLibraryResponse([]) });

    expect(screen.getByText(copy.library.emptySearch)).toBeInTheDocument();
    expect(screen.getByText(copy.library.emptySearchBody)).toBeInTheDocument();
    expect(screen.queryByText(copy.library.empty)).toBeNull();
  });

  it('★AC-150：scope=trash 才出现回收站提示，行上是「恢复」而不是星标', () => {
    nav.search = 'scope=trash';
    const deleted = makeItem({ deletedAt: new Date(Date.now() - 3 * 86_400_000).toISOString() });
    renderClient({ initialScope: 'trash', initialData: makeLibraryResponse([deleted]) });

    expect(
      screen.getByText(`${copy.library.trashNotice}。${copy.library.trashNoticePurge}。`),
    ).toBeInTheDocument();
    // 非回收站那一句键位提示不该同时存在（两条分支是互斥的）。
    expect(screen.queryByText(copy.library.keys)).toBeNull();

    expect(screen.getByRole('button', { name: copy.library.actions.restore })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: copy.library.actions.star })).toBeNull();
  });

  it('★后端不支持时说实话，且不画一个空列表（`enabled=false`）', () => {
    renderClient({ enabled: false });

    expect(screen.getByText(copy.library.needsNewerBackend)).toBeInTheDocument();
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});

describe('ReportLibraryClient · 首屏不重复请求（★P2-10 / AC-137）', () => {
  it('★P2-10：带 initialDataUpdatedAt ⇒ 挂载后不再发 `/reports`', async () => {
    renderClient({ initialData: makeLibraryResponse([makeItem()]), initialDataUpdatedAt: Date.now() });

    // 给 TanStack 的 mount effect 与任何后续调度一个机会：该发的话，就在这段时间里发。
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(requestedUrls().filter((url) => url.includes('/reports'))).toEqual([]);
    // 首屏那一页确实被用上了（否则上面那条空数组可能只是"列表根本没渲染"）。
    expect(screen.getAllByRole('option')).toHaveLength(1);
  });

  it('★P2-10 反面样本（同一个 bug 的现场）：initialDataUpdatedAt=0 时挂载立刻重发一次', async () => {
    // 这正是漏传 `initialDataUpdatedAt` 的后果：TanStack 把 initialData 当作"无限陈旧"。
    renderClient({ initialData: makeLibraryResponse([makeItem()]), initialDataUpdatedAt: 0 });

    await waitFor(() => {
      expect(requestedUrls().some((url) => url.includes('/reports?scope=all'))).toBe(true);
    });
  });

  it('★P2-10 反面样本：没有 initialData（scope 与 initialScope 不同）时请求确实会发出', async () => {
    nav.search = 'scope=starred';
    renderClient({ initialScope: 'all', initialData: makeLibraryResponse([makeItem()]) });

    await waitFor(() => {
      expect(requestedUrls().some((url) => url.includes('/reports?scope=starred'))).toBe(true);
    });
  });
});

describe('ReportLibraryClient · 三个非常态（§23.3.8 第 4 条之外的骨架与错误）', () => {
  it('★pending 时是骨架而不是空列表（组件没有 test id，所以按 Skeleton 的 class 数）', async () => {
    // 永不 resolve：查询停在 pending，且不会有第二次请求打扰断言。
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    // scope 与 initialScope 不同 ⇒ 组件不把 initialData 交给查询 ⇒ 首帧是 pending。
    nav.search = 'scope=starred';
    const { container } = renderClient({ initialScope: 'all' });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.queryByText(copy.library.allLoaded)).toBeNull();
    // `Skeleton` 只带 class（`aria-hidden` + `animate-pulse`），没有 test id ⇒ 按唯一 class 计数。
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(6);
  });

  it('★HTTP 500 ⇒ 错误卡片带「重试」，点了会真的再请求一次', async () => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(
        { error: { code: 'internal_error', message: '服务端出错了', requestId: 'req-42', detail: {} } },
        500,
      ),
    );
    nav.search = 'scope=starred';
    renderClient({ initialScope: 'all' });

    expect(await screen.findByText('服务端出错了')).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`req-42`))).toBeInTheDocument();

    const retry = screen.getByRole('button', { name: copy.library.retry });
    const before = fetchMock.mock.calls.length;
    await userEvent.click(retry);
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
