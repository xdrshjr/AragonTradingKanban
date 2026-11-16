import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RangeHint } from '@/components/report/RangeHint';
import { copy, fill } from '@/lib/copy';
import type { BlockHit, BlockRangeResponse } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  reportBlocks:
    vi.fn<(reportId: string, from: string, to: string, limit?: number) => Promise<BlockRangeResponse>>(),
}));

// 只替换 `api.reportBlocks` 一个方法：`ApiError` 等真实导出保留（hook 要靠它区分 408）。
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, api: { ...actual.api, reportBlocks: mocks.reportBlocks } };
});

/**
 * K 线区间联动条（spec §19.3.3 / §19.5.2 / AC-54 / AC-64）。
 *
 * 这里钉住的是"四种结局各自说不同的话"：加载中、有命中（计数行）、这段没被提及、
 * 临时版报告没有可反查的段落。它们共用同一块空间，如果文案混了，读者会把
 * "报告没提这件事"读成"功能坏了"。
 */

const FROM = '2025-03-01';
const TO = '2025-04-15';

const HIT: BlockHit = {
  blockId: 'valuation.table.0',
  sectionId: 'valuation',
  sectionTitle: '估值',
  kind: 'table',
  overlapDays: 46,
  matchKind: 'quarter',
  text: '2025 年一季度营收同比 +12.3%，PE 21.4x。',
};

const HIT_SECOND: BlockHit = {
  blockId: 'business#2',
  sectionId: 'business',
  sectionTitle: '业务与竞争',
  kind: 'paragraph',
  overlapDays: 31,
  matchKind: 'month',
  text: '2025 年 3 月直营渠道占比提升。',
};

function rangeResponse(overrides: Partial<BlockRangeResponse> = {}): BlockRangeResponse {
  return {
    reportId: 'rp_1',
    range: { from: FROM, to: TO },
    items: [],
    total: 0,
    truncated: false,
    asOf: '2026-02-10',
    reason: null,
    ...overrides,
  };
}

function renderHint(props: Partial<React.ComponentProps<typeof RangeHint>> = {}) {
  return render(
    <RangeHint reportId="rp_1" from={FROM} to={TO} onClear={vi.fn()} debounceMs={0} {...props} />,
  );
}

beforeEach(() => {
  mocks.reportBlocks.mockReset();
});

describe('RangeHint · 区间反查', () => {
  it('先出加载文案，就绪后计数行落在 role=status 的 live region 里', async () => {
    let settle: (value: BlockRangeResponse) => void = () => undefined;
    mocks.reportBlocks.mockReturnValue(
      new Promise<BlockRangeResponse>((resolve) => {
        settle = resolve;
      }),
    );

    renderHint();

    expect(await screen.findByText(copy.range.loading)).toBeInTheDocument();

    await act(async () => {
      settle(rangeResponse({ items: [HIT, HIT_SECOND], total: 2 }));
    });

    const line = await screen.findByText(fill(copy.range.hint, { from: FROM, to: TO, count: 2 }));
    const live = line.closest('[role="status"]');
    expect(live).not.toBeNull();
    expect(live).toHaveAttribute('aria-live', 'polite');

    // 只发一次请求，且区间原样透传（不做本地格式化，服务端才是裁决者）。
    expect(mocks.reportBlocks).toHaveBeenCalledTimes(1);
    expect(mocks.reportBlocks).toHaveBeenCalledWith('rp_1', FROM, TO);
  });

  it('命中为 0 时说"没有被提及"，而不是留一条空计数行', async () => {
    mocks.reportBlocks.mockResolvedValue(rangeResponse());
    renderHint();

    expect(await screen.findByText(copy.range.empty)).toBeInTheDocument();
    expect(screen.queryByText(fill(copy.range.hint, { from: FROM, to: TO, count: 0 }))).toBeNull();
  });

  it('临时版报告（provisional_no_dates）说"没有可反查的段落"', async () => {
    mocks.reportBlocks.mockResolvedValue(
      rangeResponse({ items: [], total: 0, reason: 'provisional_no_dates' }),
    );
    renderHint();

    expect(await screen.findByText(copy.range.provisional)).toBeInTheDocument();
    expect(screen.queryByText(copy.range.empty)).toBeNull();
  });

  it('truncated 时补一句"仅显示前 N 段（共 M 段）"', async () => {
    mocks.reportBlocks.mockResolvedValue(
      rangeResponse({ items: [HIT], total: 60, truncated: true }),
    );
    renderHint();

    expect(
      await screen.findByText(fill(copy.range.more, { shown: 1, total: 60 }), { exact: false }),
    ).toBeInTheDocument();
  });

  it('清除按钮把"删掉 ?range=" 的决定权交回调用方', async () => {
    mocks.reportBlocks.mockResolvedValue(rangeResponse({ items: [HIT], total: 1 }));
    const onClear = vi.fn();
    const user = userEvent.setup();

    renderHint({ onClear });

    await screen.findByText(fill(copy.range.hint, { from: FROM, to: TO, count: 1 }));
    await user.click(screen.getByRole('button', { name: copy.range.clear }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('命中段的定位按钮把 blockId 交给报告面板', async () => {
    mocks.reportBlocks.mockResolvedValue(rangeResponse({ items: [HIT, HIT_SECOND], total: 2 }));
    const onJumpToBlock = vi.fn();
    const user = userEvent.setup();

    renderHint({ onJumpToBlock });

    const hitRow = (await screen.findByText(HIT.blockId)).closest('li');
    expect(hitRow).not.toBeNull();
    // 命中段同时展示章节标题与重叠天数（数字来自服务端，前端不重算）。
    expect(within(hitRow as HTMLElement).getByText(HIT.sectionTitle)).toBeInTheDocument();
    expect(within(hitRow as HTMLElement).getByText(String(HIT.overlapDays))).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: new RegExp(HIT.sectionTitle) }));
    expect(onJumpToBlock).toHaveBeenCalledTimes(1);
    expect(onJumpToBlock).toHaveBeenCalledWith(HIT.blockId);
  });

  it('反查失败时给出可读的错误行，而不是继续挂在加载态', async () => {
    mocks.reportBlocks.mockRejectedValue(new Error('boom'));
    renderHint();

    expect(await screen.findByRole('alert')).toHaveTextContent(copy.range.failed);
    expect(screen.queryByText(copy.range.loading)).toBeNull();
  });
});
