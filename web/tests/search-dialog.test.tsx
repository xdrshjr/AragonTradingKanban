import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nextFireAt, isValidCron, ScheduleDialog } from '@/components/schedule/ScheduleDialog';
import { SearchDialog } from '@/components/shell/SearchDialog';
import { api } from '@/lib/api';
import { copy } from '@/lib/copy';
import type { ScheduleOut, SearchResponse } from '@/lib/types';

/**
 * A2 检索弹窗 + A4 定时刷新编辑器（spec §18.3.3 / §18.3.4 / AC-29 / AC-31 / AC-32 / AC-36）。
 *
 * 这里钉住四件事：分组渲染、↑↓ 跨组选择后 Enter 深链、snippet 拆成真 `<mark>`（不走
 * innerHTML）、cron 解析与字段校验的边界。
 */

const SEARCH_RESPONSE: SearchResponse = {
  query: '毛利',
  instruments: [
    { symbol: '600519.SH', name: '贵州茅台', market: 'CN', currency: 'CNY', score: 0.91, existing: true },
  ],
  reports: [
    {
      reportId: 'rp_1',
      symbol: '600519.SH',
      title: '贵州茅台 投研报告 v3',
      sectionId: 'business',
      sectionTitle: '业务与竞争',
      blockId: 'business#0',
      // 服务端从**块原文**截取并包 <mark>；前端必须按标签切开渲染，不能 innerHTML。
      snippet: '…毛利率 <mark>62.1%</mark>，渠道结构改善…',
      score: 0.77,
    },
  ],
  tookMs: 8,
  degraded: false,
};

const SCHEDULE_OUT: ScheduleOut = {
  id: 'sch_1',
  instrumentId: 'ins_1',
  symbol: '600519.SH',
  cron: '0 9 * * 1-5',
  enabled: true,
  minAgeHours: 12,
  nextRunAt: '2026-02-16T09:00:00Z',
  lastRunId: null,
  failureCount: 0,
  createdAt: '2026-02-10T00:00:00Z',
  updatedAt: '2026-02-10T00:00:00Z',
};

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as unknown as Response;
}

/** 只替换 `fetch`：`lib/api` 的 URL 拼装、错误映射、超时都保持真实路径。 */
function stubSearchApi(payload: SearchResponse) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/v1/search')) return jsonResponse(payload);
    if (url.includes('/api/v1/schedules')) return jsonResponse({ items: [], total: 0 });
    return jsonResponse({}, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('SearchDialog', () => {
  it('typing a query renders both groups from the unified /search response', async () => {
    const fetchMock = stubSearchApi(SEARCH_RESPONSE);
    const user = userEvent.setup();

    render(
      <SearchDialog
        open
        onOpenChange={() => undefined}
        onSelectSymbol={() => undefined}
        onSelectHit={() => undefined}
      />,
    );

    // 空查询：引导语，不发请求。
    expect(screen.getByText(copy.search.emptyQuery)).toBeInTheDocument();

    await user.type(screen.getByRole('combobox'), '毛利');

    expect(await screen.findByText(copy.search.instruments)).toBeInTheDocument();
    expect(screen.getByText(copy.search.reports)).toBeInTheDocument();

    const url = String(fetchMock.mock.calls[0]?.[0] ?? '');
    expect(url).toContain('/api/v1/search?q=');
    expect(decodeURIComponent(url)).toContain('q=毛利');
  });

  it('↑↓ moves across both groups and Enter deep-links the report hit', async () => {
    stubSearchApi(SEARCH_RESPONSE);
    const user = userEvent.setup();
    const onSelectSymbol = vi.fn();
    const onSelectHit = vi.fn();
    const onOpenChange = vi.fn();

    render(
      <SearchDialog
        open
        onOpenChange={onOpenChange}
        onSelectSymbol={onSelectSymbol}
        onSelectHit={onSelectHit}
      />,
    );

    const input = screen.getByRole('combobox');
    await user.type(input, '毛利');
    await screen.findByText(copy.search.reports);

    // 第一行是标的命中，第二行才是报告命中——一次 ↓ 就跨过分组边界。
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('option', { name: /业务与竞争/ })).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{Enter}');

    expect(onSelectHit).toHaveBeenCalledTimes(1);
    expect(onSelectHit).toHaveBeenCalledWith(SEARCH_RESPONSE.reports[0]);
    expect(onSelectSymbol).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('renders the snippet as real <mark> nodes, never as raw HTML', async () => {
    stubSearchApi(SEARCH_RESPONSE);
    const user = userEvent.setup();

    render(
      <SearchDialog
        open
        onOpenChange={() => undefined}
        onSelectSymbol={() => undefined}
        onSelectHit={() => undefined}
      />,
    );

    await user.type(screen.getByRole('combobox'), '毛利');
    await screen.findByText(copy.search.reports);

    // Radix 把内容 portal 到 body，所以不能只看 `container`。
    const marks = document.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
    expect(marks[0]?.textContent).toBe('62.1%');

    // 命中行的可见文字是去标签后的原文，DOM 里不存在被当作文本渲染的标签串。
    const option = screen.getByRole('option', { name: /业务与竞争/ });
    expect(option.textContent).toContain('…毛利率 62.1%，渠道结构改善…');

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toContain('<mark>');
    expect(bodyText).not.toContain('<span');
  });

  it('surfaces the LIKE fallback notice when the server degrades', async () => {
    stubSearchApi({ ...SEARCH_RESPONSE, degraded: true });
    const user = userEvent.setup();

    render(
      <SearchDialog
        open
        onOpenChange={() => undefined}
        onSelectSymbol={() => undefined}
        onSelectHit={() => undefined}
      />,
    );

    await user.type(screen.getByRole('combobox'), '毛利');
    expect(await screen.findByText(copy.search.degraded)).toBeInTheDocument();
  });
});

describe('cron parser (nextFireAt)', () => {
  it('returns the following Monday 09:00 UTC for a weekday 9am rule', () => {
    // 2026-02-13 是周五；下一次命中应是 2026-02-16（周一）09:00 UTC。
    const next = nextFireAt('0 9 * * 1-5', new Date('2026-02-13T10:00:00Z'));
    expect(next?.toISOString()).toBe('2026-02-16T09:00:00.000Z');
  });

  it('rejects an out-of-range minute instead of guessing', () => {
    expect(nextFireAt('60 * * * *', new Date('2026-02-13T10:00:00Z'))).toBeNull();
    expect(isValidCron('60 * * * *')).toBe(false);
    expect(isValidCron('0 9 * * 1-5')).toBe(true);
    expect(isValidCron('0 9 * *')).toBe(false);
  });

  it('snaps */15 to the next quarter hour', () => {
    const next = nextFireAt('*/15 * * * *', new Date('2026-02-13T10:07:03Z'));
    expect(next?.toISOString()).toBe('2026-02-13T10:15:00.000Z');
  });

  it('finds 2/29 in the next leap year (§18.3.3 的两级推进)', () => {
    const next = nextFireAt('0 0 29 2 *', new Date('2026-02-13T10:00:00Z'));
    expect(next?.toISOString()).toBe('2028-02-29T00:00:00.000Z');
  });
});

describe('ScheduleDialog', () => {
  it('shows the inline cron error and refuses to submit an invalid expression', async () => {
    vi.spyOn(api, 'schedules').mockResolvedValue({ items: [], total: 0 });
    const createSpy = vi.spyOn(api, 'createSchedule').mockResolvedValue(SCHEDULE_OUT);
    const user = userEvent.setup();

    render(
      <ScheduleDialog
        open
        onOpenChange={() => undefined}
        instrumentId="ins_1"
        symbol="600519.SH"
        enabled
      />,
    );

    const cronInput = await screen.findByLabelText(copy.schedule.cron);
    await user.type(cronInput, '60 * * * *');

    // 边打边校验：红字立刻出现，但输入不被打断、保存按钮也不 disable。
    expect(screen.getByText(copy.schedule.cronInvalid)).toBeInTheDocument();

    const save = screen.getByRole('button', { name: copy.schedule.save });
    expect(save).toBeEnabled();
    await user.click(save);

    expect(createSpy).not.toHaveBeenCalled();
  });

  it('submits a valid cron with Enter and posts the trimmed body exactly once', async () => {
    vi.spyOn(api, 'schedules').mockResolvedValue({ items: [], total: 0 });
    const createSpy = vi.spyOn(api, 'createSchedule').mockResolvedValue(SCHEDULE_OUT);
    const user = userEvent.setup();

    render(
      <ScheduleDialog
        open
        onOpenChange={() => undefined}
        instrumentId="ins_1"
        symbol="600519.SH"
        enabled
      />,
    );

    const cronInput = await screen.findByLabelText(copy.schedule.cron);
    await user.type(cronInput, '0 9 * * 1-5');
    expect(screen.queryByText(copy.schedule.cronInvalid)).toBeNull();

    // 表单里按 Enter 即可保存（AC-36：不动鼠标也能完成）。
    await user.keyboard('{Enter}');

    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    expect(createSpy).toHaveBeenCalledWith({
      instrumentId: 'ins_1',
      cron: '0 9 * * 1-5',
      enabled: true,
      minAgeHours: 12,
    });
  });

  it('renders nothing at all when capabilities.features.schedule is off', async () => {
    const schedulesSpy = vi.spyOn(api, 'schedules').mockResolvedValue({ items: [], total: 0 });

    const { container } = render(
      <ScheduleDialog
        open
        onOpenChange={() => undefined}
        instrumentId="ins_1"
        symbol="600519.SH"
        enabled={false}
      />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(schedulesSpy).not.toHaveBeenCalled();
  });

  it('translates a 409 duplicate_schedule into readable copy', async () => {
    vi.spyOn(api, 'schedules').mockResolvedValue({ items: [], total: 0 });
    const { ApiError } = await import('@/lib/api');
    vi.spyOn(api, 'createSchedule').mockRejectedValue(
      new ApiError(409, {
        error: { code: 'duplicate_schedule', message: 'duplicate', requestId: 'req-1', detail: {} },
      }),
    );
    const user = userEvent.setup();

    render(
      <ScheduleDialog
        open
        onOpenChange={() => undefined}
        instrumentId="ins_1"
        symbol="600519.SH"
        enabled
      />,
    );

    await user.type(await screen.findByLabelText(copy.schedule.cron), '0 9 * * 1-5');
    await user.click(screen.getByRole('button', { name: copy.schedule.save }));

    expect(await screen.findByText(copy.schedule.duplicate)).toBeInTheDocument();
  });
});
