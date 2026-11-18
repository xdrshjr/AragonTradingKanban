import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { WorkspaceClient } from '@/components/shell/WorkspaceClient';
import { useUiStore } from '@/lib/store';
import type {
  BarsResponse,
  Capabilities,
  DiffResult,
  Instrument,
  ReportDetail,
  ReportSummary,
} from '@/lib/types';

/**
 * ★AC-142 / R-74 / §23.3.3 —— **URL 是"当前在读哪一版"的唯一真源**。
 *
 * 断言三件事：
 *  1. 选一个旧版本 ⇒ `router.replace` **恰好一次**，URL 带 `report=<旧 id>`，
 *     且既有的 `view / range / tf / win / adj` 一个都没丢、一个都没变。
 *  2. 选最新版 ⇒ `report` 参数被**删除**（"URL 只记录与默认不同"），其余照旧。
 *  3. 第二个参数恒为 `{ scroll: false }` —— 视图状态不进后退栈。
 *
 * 走的是**真实 UI 路径**：`ReportPanel` 头部的 `VersionSwitcher` → `onSelectVersion`
 * → `WorkspaceClient.selectVersion`，没有绕过组件直接调 handler。三块面板都不需要
 * 图表数据（jsdom 视口 1024 < `BP_KLINE_COLLAPSE`，K 线面板本就不挂载），所以这
 * 条路径在单测里跑得起来；`fetch` 用一个通用 JSON 应答兜住所有接口。
 *
 * 注意 `syncUrl` 读的是 `window.location.search`，所以"既有参数"是从 **jsdom 的
 * location** 种进去的（等于"刷新/分享链接进来时 URL 里就有的那几个键"），而不是
 * 通过 props 传进去的 —— props 只决定面板渲染什么，不决定 URL 里有什么。因此这里
 * 故意不传 `initialRange`（那会顺带挂载 `RangeHint`，多一条与本案无关的取数）。
 */

/* ── 夹具 ──────────────────────────────────────────────────────────────── */

const INSTRUMENT: Instrument = {
  id: 'CN:600519.SH',
  symbol: '600519.SH',
  name: '贵州茅台',
  market: 'CN',
  currency: 'CNY',
  exchange: 'SSE',
  sector: '食品饮料',
  tz: 'Asia/Shanghai',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
};

const LATEST_ID = 'rp_latest_3';
const OLD_ID = 'rp_old_2';

/** 起始 URL。五个既有参数都**不是**默认值，否则"被丢弃"与"被规范化删除"分不开。 */
const INITIAL_SEARCH =
  '?view=full&range=2025-03-01..2025-04-15&tf=1w&win=6m&adj=none';

function makeSummary(
  overrides: Partial<ReportSummary> & Pick<ReportSummary, 'id' | 'version' | 'isLatest'>,
): ReportSummary {
  return {
    instrumentId: INSTRUMENT.id,
    status: 'ready',
    title: `贵州茅台 投研报告 v${overrides.version}`,
    headline: '趋势偏强，估值处于中性区间',
    stance: 'buy',
    asOf: '2026-02-10T00:00:00Z',
    generatedAt: '2026-02-10T00:00:00Z',
    pinned: false,
    archived: false,
    provisional: false,
    groundedRatio: 0.94,
    quality: {
      groundedRatio: 0.94,
      missingSections: [],
      prunedBlocks: 0,
      ungroundedCount: 0,
      source: 'aragon',
    },
    provider: 'openai',
    model: 'deepseek-chat',
    costUsd: null,
    costKnown: false,
    tokensIn: 100,
    tokensOut: 200,
    durationMs: 42_000,
    docBytes: 4096,
    ...overrides,
  };
}

const LATEST_SUMMARY = makeSummary({ id: LATEST_ID, version: 3, isLatest: true });
const OLD_SUMMARY = makeSummary({ id: OLD_ID, version: 2, isLatest: false });

const REPORTS: ReportSummary[] = [LATEST_SUMMARY, OLD_SUMMARY];

function makeDetail(summary: ReportSummary): ReportDetail {
  return {
    report: summary,
    doc: {
      schema: 'aragon.research.report/v1',
      instrument: {
        symbol: INSTRUMENT.symbol,
        name: INSTRUMENT.name,
        market: 'CN',
        currency: 'CNY',
      },
      asOf: '2026-02-10T00:00:00Z',
      headline: summary.headline,
      rating: { stance: 'buy', conviction: 3, horizon: '6-12m' },
      summary: '示例摘要',
      keyMetrics: [],
      sections: [{ id: 'thesis', title: '核心逻辑', blocks: [{ type: 'paragraph', text: '核心段落' }] }],
      risks: [],
      sources: [],
      disclaimer: '仅供研究参考。',
    },
  };
}

const LATEST_DETAIL = makeDetail(LATEST_SUMMARY);
const OLD_DETAIL = makeDetail(OLD_SUMMARY);

const CAPABILITIES: Capabilities = {
  aragon: {
    available: true,
    version: '1.0.0',
    versionOk: true,
    provider: 'openai',
    model: 'deepseek-chat',
    hasApiKey: true,
    ajv: true,
    canStructured: true,
    hasSubmitResult: true,
    extraAllowTools: [],
    errors: [],
  },
  agentMode: 'real',
  dataProviders: [],
  features: {},
  limits: {},
};

/* ── `next/navigation` 替身 ─────────────────────────────────────────────── */

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  prefetch: vi.fn(),
  back: vi.fn(),
  refresh: vi.fn(),
  /** 本案的"初始 query string" —— `useSearchParams()` 从它构造。 */
  search: '',
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: mocks.replace,
    push: mocks.push,
    prefetch: mocks.prefetch,
    back: mocks.back,
    refresh: mocks.refresh,
  }),
  useSearchParams: () => new URLSearchParams(mocks.search),
  usePathname: () => '/600519.SH',
  useParams: () => ({ symbol: '600519.SH' }),
}));

/**
 * `AppShell` 头部有一个 `<Link href="/reports">`（`features.reportLibrary`）。换成
 * 裸标签，免得单测依赖 Next 的 router context —— 它没有 URL 语义要验证。
 */
vi.mock('next/link', () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

/* ── 通用 JSON 应答（一个 handler 兜住所有接口）────────────────────────── */

function jsonResponse(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as unknown as Response;
}

function barsPayload(): BarsResponse {
  return {
    instrument: INSTRUMENT,
    bars: [],
    indicators: {},
    meta: {
      source: 'fixture',
      barsCount: 0,
      adjust: 'qfq',
      timeframe: '1d',
      cached: false,
      stale: false,
      warnings: [],
    },
  };
}

function diffPayload(): DiffResult {
  return {
    a: OLD_ID,
    b: LATEST_ID,
    statusChanged: null,
    ratingChanged: null,
    metricsDelta: [],
    sectionsAdded: [],
    sectionsRemoved: [],
    blocksChanged: [],
    summary: '',
  };
}

/** 按路径分派；`/reports/<id>` 永远回一份能渲染的详情。 */
function payloadFor(url: string): unknown {
  const path = url.split('/api/v1')[1] ?? url;
  if (path.startsWith('/schedules')) return { items: [], total: 0 };
  if (path.startsWith('/watchlist')) return { items: [] };
  if (path.includes('/bars')) return barsPayload();
  if (path.includes('/quote')) {
    return { instrumentId: INSTRUMENT.id, updatedAt: '2026-02-10T00:00:00Z' };
  }
  if (path.includes('/diff/')) return diffPayload();
  if (path.includes('/reports/latest')) return LATEST_DETAIL;
  if (path.startsWith('/instruments/') && path.includes('/reports')) {
    return { items: REPORTS, total: REPORTS.length };
  }
  if (path.startsWith('/reports/')) {
    const id = path.slice('/reports/'.length);
    const summary = REPORTS.find((report) => report.id === id);
    return summary ? makeDetail(summary) : LATEST_DETAIL;
  }
  return {};
}

/* ── 渲染与读取 ─────────────────────────────────────────────────────────── */

function renderWorkspace(props: Partial<Parameters<typeof WorkspaceClient>[0]> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceClient
        initialInstrument={INSTRUMENT}
        initialReports={REPORTS}
        initialDetail={LATEST_DETAIL}
        initialWatchlist={[]}
        capabilities={CAPABILITIES}
        {...props}
      />
    </QueryClientProvider>,
  );
}

/** `syncUrl` 写的是 `?a=b` 这种相对 query；也容忍带路径的写法。 */
function queryOf(url: string): URLSearchParams {
  const index = url.indexOf('?');
  return new URLSearchParams(index >= 0 ? url.slice(index + 1) : '');
}

function replaceCall(index = 0): { url: string; query: URLSearchParams; options: unknown } {
  const call = mocks.replace.mock.calls[index] as unknown[] | undefined;
  if (!call) throw new Error(`router.replace 没有被调用第 ${index + 1} 次`);
  const url = String(call[0]);
  return { url, query: queryOf(url), options: call[1] };
}

/**
 * 真实 UI 路径：打开 `VersionSwitcher`，点一行版本。
 *
 * 触发器按 `aria-haspopup="listbox"` 定位（全仓只有 `VersionSwitcher` 用它）——
 * 触发器上显示的是**当前**版本号，不能拿"要选的那一版"去找它。列表是
 * `role="listbox"` / `role="option"`（`VersionSwitcher` 的既有 ARIA）。
 */
async function pickVersion(user: ReturnType<typeof userEvent.setup>, label: 'v2' | 'v3') {
  const trigger = await waitFor(() => {
    const node = document.querySelector<HTMLElement>('button[aria-haspopup="listbox"]');
    if (!node) throw new Error('版本切换器还没渲染出来');
    return node;
  });
  await user.click(trigger);

  const listbox = await screen.findByRole('listbox', { name: '报告版本' });
  const option = within(listbox)
    .getAllByRole('option')
    .find((candidate) => (candidate.textContent ?? '').trim().startsWith(label));
  if (!option) throw new Error(`版本列表里没有 ${label}`);
  await user.click(option);
}

/** 既有五个参数一个都没丢、一个都没变（AC-142 的"现值"）。 */
function expectPreserved(query: URLSearchParams) {
  expect(query.get('view')).toBe('full');
  expect(query.get('range')).toBe('2025-03-01..2025-04-15');
  expect(query.get('tf')).toBe('1w');
  expect(query.get('win')).toBe('6m');
  expect(query.get('adj')).toBe('none');
}

beforeEach(() => {
  mocks.replace.mockClear();
  mocks.push.mockClear();
  mocks.prefetch.mockClear();
  mocks.back.mockClear();
  mocks.refresh.mockClear();
  mocks.search = INITIAL_SEARCH;

  // `syncUrl` 读 `window.location.search`：这就是"刷新/分享进来时 URL 里已有的东西"。
  window.history.replaceState({}, '', `/600519.SH${INITIAL_SEARCH}`);
  window.localStorage.clear();

  // zustand store 是模块级的：`activeReportId` 优先级高于 `initialReportId`，
  // 不清就会让下一个用例跑在上一轮选中的版本上（R-74 点名的"两个真源"）。
  useUiStore.setState({
    activeReportId: null,
    selectedInstrumentId: null,
    commandPaletteOpen: false,
    klineDrawerOpen: false,
    overlayCollapsed: false,
    railCollapsedByUser: false,
    sourceDrawer: { open: false, activeSourceId: null },
  });

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => jsonResponse(payloadFor(String(input)))),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/* ── 用例 ──────────────────────────────────────────────────────────────── */

describe('工作区 URL · 版本选择是唯一真源（AC-142 / R-74）', () => {
  it('选旧版本 → router.replace 恰好一次，带 report=<旧 id>，其余参数一个不丢', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await pickVersion(user, 'v2');

    expect(mocks.replace).toHaveBeenCalledTimes(1);
    const { query } = replaceCall();
    expect(query.get('report')).toBe(OLD_ID);
    expectPreserved(query);
    // 写进去的是"旧版"而不是最新版 —— 否则这条断言在两个 id 互换了也照样绿。
    expect(query.get('report')).not.toBe(LATEST_ID);
  });

  it('深链在旧版时选最新版 → report 参数被删除，其余参数照旧', async () => {
    const user = userEvent.setup();
    // 从 `?report=<旧 id>` 进来（刷新/分享的真实形态）。
    const deepLink = `${INITIAL_SEARCH}&report=${OLD_ID}`;
    mocks.search = deepLink;
    window.history.replaceState({}, '', `/600519.SH${deepLink}`);

    renderWorkspace({ initialReportId: OLD_ID, initialDetail: OLD_DETAIL });

    await pickVersion(user, 'v3');

    expect(mocks.replace).toHaveBeenCalledTimes(1);
    const { query } = replaceCall();
    // "URL 只记录与默认不同" —— 最新版就是默认，所以这个键必须消失，而不是被写成 id。
    expect(query.has('report')).toBe(false);
    expectPreserved(query);
  });

  it('第二个参数恒为 { scroll: false } —— 视图状态不进后退栈', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await pickVersion(user, 'v2');

    expect(mocks.replace).toHaveBeenCalledTimes(1);
    expect(replaceCall().options).toEqual({ scroll: false });
    // 也不能偷偷 push 一条历史条目。
    expect(mocks.push).not.toHaveBeenCalled();
  });

  /**
   * ★v14（U2 / 实施发现 D-8）：**URL 改了不等于界面改了**。
   *
   * `detailQuery` 的 `initialData` 是"数据"而不是"函数"，而 key 里含 `selectedReportId`
   * ⇒ 修复前，从首屏（最新版）切到 v2 时新 key 直接继承了 `initialDetail`（最新版），
   * 叠加 `staleTime: Infinity` 之后**永远不会去取 v2**：URL 写着 v2，面板还是 v3。
   * 这条用例盯的是"真的发了一次 `/reports/<v2>`、并且面板换成了 v2 的那句 headline"。
   */
  it('选旧版本后面板真的去取那一版（不是继承首屏数据）', async () => {
    const user = userEvent.setup();
    const oldHeadline = '旧版结论：增速回落，等待旺季验证';
    const oldDetail: ReportDetail = {
      ...OLD_DETAIL,
      doc: { ...OLD_DETAIL.doc, headline: oldHeadline },
    };
    // `payloadFor` 按 id 回详情；这里让"旧版"的正文与最新版**可区分**。
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown) => {
        const url = String(input);
        if (url.includes(`/reports/${OLD_ID}`) && !url.includes('/diff/')) return jsonResponse(oldDetail);
        return jsonResponse(payloadFor(url));
      }),
    );

    renderWorkspace();

    await pickVersion(user, 'v2');

    // 1) 真的发了一次 `/reports/<旧 id>`（修复前这一步永远不发生）。
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map(
      (call) => String(call[0]),
    );
    expect(calls.some((url) => url.includes(`/reports/${OLD_ID}`))).toBe(true);

    // 2) 面板显示的是旧版的那句话，而且历史版本横幅出现了（U2 的可见证据）。
    expect(await screen.findByText(oldHeadline)).toBeInTheDocument();
    expect(await screen.findByText(/正在查看历史版本 v2/)).toBeInTheDocument();
  });
});
