import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { SymbolRail, type RailItem } from '@/components/shell/SymbolRail';
import { copy } from '@/lib/copy';
import { num } from '@/lib/format';
import { useRailQuotes } from '@/lib/useRailQuotes';
import type { Bar, BarsResponse, ChartView, Instrument, Quote } from '@/lib/types';

/**
 * AC5 / AC7 of `docs/diagnoses/provider-chain-divergence/analysis.md`.
 *
 * AC5：`hfq` 下选中行与图表头部价差 0.00%（左栏 1,439.78 vs 头部 8,840.24 是本轮的
 *       +514% 现场），且其余行不因此新增 `/bars` 请求。
 * AC7：rail 每一行 `source === 'synthetic'` 时必须出现合成徽标（此前徽标只存在于图表
 *      footer，左栏把 AAPL 的 100% 合成价格当真实价格展示）。
 */

const RAIL_LAST = 1439.78; // /quote 给的前复权价 —— 选中行**不能**用它
const BARS_LAST = 8840.24; // 图表（hfq）末根收盘 —— 选中行应该用它
const BARS_PREV = 8911.19;

const mocks = vi.hoisted(() => ({
  quote: vi.fn<(id: string) => Promise<Quote>>(),
  bars: vi.fn<(id: string, tf?: string, range?: string, adjust?: string) => Promise<BarsResponse>>(),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, api: { ...actual.api, quote: mocks.quote, bars: mocks.bars } };
});

function instrument(id: string, symbol: string): Instrument {
  return { id, symbol, name: symbol, market: 'CN', currency: 'CNY' } as Instrument;
}

function bar(ts: number, close: number): Bar {
  return { ts, o: close, h: close, l: close, c: close, v: 1 };
}

function barsResponse(bars: Bar[], source = 'akshare', reasonCode = 'ok'): BarsResponse {
  return {
    instrument: instrument('CN:600519.SH', '600519.SH'),
    bars,
    indicators: {},
    meta: {
      source,
      barsCount: bars.length,
      fromTs: bars[0]?.ts ?? null,
      toTs: bars[bars.length - 1]?.ts ?? null,
      adjust: 'hfq',
      timeframe: '1d',
      cached: false,
      stale: false,
      reasonCode,
      warnings: [],
    },
  };
}

function quoteFor(id: string, last: number, source = 'akshare'): Quote {
  return { instrumentId: id, last, changePct: 1.5, updatedAt: '2026-11-17T05:00:00Z', source, cached: false };
}

const HFQ_VIEW: ChartView = { timeframe: '1d', range: '1y', adjust: 'hfq' };

function Harness({
  items,
  selectedId,
  chart,
}: {
  items: RailItem[];
  selectedId: string;
  chart: ChartView;
}) {
  const rows = useRailQuotes(items, { selectedId, chart });
  return (
    <ul>
      {rows.map((row) => (
        <li key={row.instrument.id} data-testid={`row-${row.instrument.id}`}>
          <span data-testid={`last-${row.instrument.id}`}>{row.last === null || row.last === undefined ? '—' : num(row.last, 2)}</span>
          <span data-testid={`source-${row.instrument.id}`}>{row.source ?? ''}</span>
          <span data-testid={`adjust-${row.instrument.id}`}>{row.adjust ?? ''}</span>
        </li>
      ))}
    </ul>
  );
}

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const TS = Date.UTC(2026, 10, 16, 7, 0) / 1000;

describe('AC5：只有选中行跟随图表的口径与来源', () => {
  it('hfq 下选中行取图表那条序列的末根，其余行仍来自 /quote', async () => {
    mocks.quote.mockImplementation(async (id) => quoteFor(id, RAIL_LAST));
    mocks.bars.mockResolvedValue(barsResponse([bar(TS - 86400, BARS_PREV), bar(TS, BARS_LAST)]));

    const items: RailItem[] = [
      { instrument: instrument('CN:600519.SH', '600519.SH') },
      { instrument: instrument('CN:000001.SZ', '000001.SZ') },
    ];
    renderWithClient(<Harness items={items} selectedId="CN:600519.SH" chart={HFQ_VIEW} />);

    // 选中行：图表末根（8840.24），不是 /quote 的 1439.78
    await waitFor(() => {
      expect(screen.getByTestId('last-CN:600519.SH')).toHaveTextContent(num(BARS_LAST, 2));
    });
    expect(screen.getByTestId('last-CN:600519.SH')).not.toHaveTextContent(num(RAIL_LAST, 2));
    expect(screen.getByTestId('adjust-CN:600519.SH')).toHaveTextContent('hfq');
    expect(screen.getByTestId('source-CN:600519.SH')).toHaveTextContent('akshare');

    // 其余行仍旧走 /quote，且不带 复权 口径角标
    await waitFor(() => {
      expect(screen.getByTestId('last-CN:000001.SZ')).toHaveTextContent(num(RAIL_LAST, 2));
    });
    expect(screen.getByTestId('adjust-CN:000001.SZ')).toHaveTextContent('');

    // 代价：整段 rail 只发出 **一次** /bars（选中行），没有 12 个请求。
    expect(mocks.bars).toHaveBeenCalledTimes(1);
    expect(mocks.bars).toHaveBeenCalledWith('CN:600519.SH', '1d', '1y', 'hfq');
  });
});

describe('AC7：合成价格必须在左栏被标出来', () => {
  it('source=synthetic 的行渲染合成徽标，真实来源的行不渲染', async () => {
    mocks.quote.mockImplementation(async (id) =>
      id === 'US:AAPL' ? quoteFor(id, 330.46, 'synthetic') : quoteFor(id, RAIL_LAST, 'akshare'),
    );
    mocks.bars.mockResolvedValue(barsResponse([bar(TS, 330.46)], 'synthetic'));

    const items: RailItem[] = [
      { instrument: instrument('US:AAPL', 'AAPL') },
      { instrument: instrument('CN:600519.SH', '600519.SH') },
    ];
    renderWithClient(<Harness items={items} selectedId="CN:600519.SH" chart={HFQ_VIEW} />);

    await waitFor(() => {
      expect(screen.getByTestId('source-US:AAPL')).toHaveTextContent('synthetic');
    });
    expect(screen.getByTestId('source-CN:600519.SH')).toHaveTextContent('synthetic');

    // 徽标本身由 SymbolRail 渲染 —— 选中行与真实来源行各自判断。
    renderWithClient(
      <SymbolRail
        instruments={[
          { instrument: instrument('US:AAPL', 'AAPL'), last: 330.46, changePct: 1.3, source: 'synthetic' },
          { instrument: instrument('CN:600519.SH', '600519.SH'), last: 1439.78, changePct: -1.0, source: 'akshare' },
        ]}
        watchlist={[]}
        selectedId="CN:600519.SH"
        onSelect={() => {}}
        onResolved={() => {}}
      />,
    );
    expect(screen.getAllByText(copy.chart.synthetic)).toHaveLength(1);
  });

  it('图表还没有数据时，选中行退回 /quote，且不谎称口径是前复权', async () => {
    mocks.quote.mockImplementation(async (id) => quoteFor(id, RAIL_LAST));
    mocks.bars.mockResolvedValue(barsResponse([]));

    const items: RailItem[] = [{ instrument: instrument('CN:600519.SH', '600519.SH') }];
    renderWithClient(<Harness items={items} selectedId="CN:600519.SH" chart={HFQ_VIEW} />);

    await waitFor(() => {
      expect(screen.getByTestId('last-CN:600519.SH')).toHaveTextContent(num(RAIL_LAST, 2));
    });
    // 实时报价是**不复权**的行情，标成 qfq 就是替它背书；这里只能留空。
    expect(screen.getByTestId('adjust-CN:600519.SH')).toHaveTextContent('');
  });

  it('选中行跟随图表时，非前复权会标出「后复权」', () => {
    renderWithClient(
      <SymbolRail
        instruments={[{ instrument: instrument('CN:600519.SH', '600519.SH'), last: BARS_LAST, changePct: 0.8, source: 'akshare', adjust: 'hfq' }]}
        watchlist={[]}
        selectedId="CN:600519.SH"
        onSelect={() => {}}
        onResolved={() => {}}
      />,
    );
    expect(screen.getByText(copy.chart.adjustApplied.hfq)).toBeInTheDocument();
    expect(screen.getByText(num(BARS_LAST, 2))).toBeInTheDocument();
  });
});
