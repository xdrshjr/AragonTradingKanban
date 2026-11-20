import {
  QueryClient, QueryClientProvider, useQuery,
} from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SymbolRail, type RailItem } from '@/components/shell/SymbolRail';
import { copy } from '@/lib/copy';
import { num } from '@/lib/format';
import { useRailQuotes } from '@/lib/useRailQuotes';
import { chartBarsQuery } from '@/lib/chartQuery';
import { INDICATOR_KEYS } from '@/lib/chartDataset';
import {
  DEFAULT_INDICATORS, loadIndicators, saveIndicators,
} from '@/components/chart/IndicatorToggles';
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

beforeEach(() => window.localStorage.clear());

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
    indicators: Object.fromEntries(INDICATOR_KEYS.map(key => [
      key, bars.map(() => null),
    ])),
    chart: {
      version: 'chart/v1', calculationBarsCount: bars.length,
      warmupBarsCount: 0, historyLimited: true,
      volumeUnit: source === 'synthetic' ? 'unit' : 'lot',
      volumeUnitSize: source === 'synthetic' ? null : 100,
      amountCurrency: 'CNY',
      effectiveAdjust: source === 'synthetic' ? 'synthetic' : 'hfq',
      returnBasis: 'adjacent_close',
      indicatorConvention: 'aragon-chart-v1',
      stats: bars.map((bar, index) => {
        const previous = bars[index - 1]?.c ?? null;
        const date = new Date(bar.ts * 1000).toISOString().slice(0, 10);
        return {
          ts: bar.ts, periodStart: date, periodEnd: date,
          leadingPartial: false, previousClose: previous,
          change: previous === null ? null : bar.c - previous,
          changePct: previous === null ? null
            : (bar.c / previous - 1) * 100,
          amplitudePct: previous === null ? null : 0,
        };
      }),
    },
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
          <span data-testid={`change-${row.instrument.id}`}>
            {row.changePct ?? '—'}
          </span>
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
    expect(mocks.bars).toHaveBeenCalledWith(
      'CN:600519.SH', '1d', '1y', 'hfq', { profile: 'chart' },
    );
  });

  it('直接采用尾stats涨幅，不能按显示柱重新计算', async () => {
    const response = barsResponse([
      bar(TS - 86400, BARS_PREV), bar(TS, BARS_LAST),
    ]);
    response.chart!.stats.at(-1)!.changePct = 6.25;
    mocks.bars.mockResolvedValue(response);
    mocks.quote.mockResolvedValue(quoteFor('CN:600519.SH', RAIL_LAST));
    renderWithClient(<Harness
      items={[{ instrument: response.instrument }]}
      selectedId="CN:600519.SH" chart={HFQ_VIEW}
    />);
    await waitFor(() => {
      expect(screen.getByTestId('change-CN:600519.SH'))
        .toHaveTextContent('6.25');
    });
  });

  it('缺前收时保留尾价且涨幅为null，不补quote涨幅', async () => {
    const response = barsResponse([
      bar(TS - 86400, BARS_PREV), bar(TS, BARS_LAST),
    ]);
    Object.assign(response.chart!.stats.at(-1)!, {
      previousClose: null, change: null, changePct: null,
      amplitudePct: null,
    });
    mocks.bars.mockResolvedValue(response);
    mocks.quote.mockResolvedValue(quoteFor('CN:600519.SH', RAIL_LAST));
    renderWithClient(<Harness
      items={[{ instrument: response.instrument }]}
      selectedId="CN:600519.SH" chart={HFQ_VIEW}
    />);
    await waitFor(() => expect(screen.getByTestId('last-CN:600519.SH'))
      .toHaveTextContent(num(BARS_LAST, 2)));
    expect(screen.getByTestId('change-CN:600519.SH')).toHaveTextContent('—');
  });

  it('图表和选中行共用chart key，只发一次请求', async () => {
    const response = barsResponse([bar(TS, BARS_LAST)]);
    mocks.bars.mockResolvedValue(response);
    mocks.quote.mockResolvedValue(quoteFor('CN:600519.SH', RAIL_LAST));
    function SharedChart() {
      const query = useQuery(chartBarsQuery('CN:600519.SH', HFQ_VIEW));
      return <div data-testid="shared-bars">{query.data?.bars.length}</div>;
    }
    const previousCalls = mocks.bars.mock.calls.length;
    renderWithClient(<>
      <SharedChart />
      <Harness items={[{ instrument: response.instrument }]}
        selectedId="CN:600519.SH" chart={HFQ_VIEW} />
    </>);
    await waitFor(() => expect(screen.getByTestId('last-CN:600519.SH'))
      .toHaveTextContent(num(BARS_LAST, 2)));
    expect(screen.getByTestId('shared-bars')).toHaveTextContent('1');
    expect(mocks.bars.mock.calls.length - previousCalls).toBe(1);
  });

  it('首次失败且quote缺失时清除旧item价格、涨幅与口径', async () => {
    mocks.bars.mockRejectedValue(new Error('offline'));
    mocks.quote.mockRejectedValue(new Error('offline'));
    renderWithClient(<Harness items={[{
      instrument: instrument('CN:600519.SH', '600519.SH'),
      last: 999, changePct: 88, source: 'old', adjust: 'qfq',
    }]} selectedId="CN:600519.SH" chart={HFQ_VIEW} />);
    await waitFor(() => expect(screen.getByTestId('last-CN:600519.SH'))
      .toHaveTextContent('—'));
    expect(screen.getByTestId('change-CN:600519.SH')).toHaveTextContent('—');
    expect(screen.getByTestId('source-CN:600519.SH')).toBeEmptyDOMElement();
    expect(screen.getByTestId('adjust-CN:600519.SH')).toBeEmptyDOMElement();
  });
});

describe('旧指标导出作为v2偏好的兼容包装', () => {
  it('旧入口读v2，保存时保留EMA及振荡图且不回写v1', () => {
    const legacy = JSON.stringify({ ...DEFAULT_INDICATORS, boll: false });
    localStorage.setItem('aragon.indicators.v1', legacy);
    localStorage.setItem('aragon.chart.v2', JSON.stringify({
      version: 2,
      main: { ...DEFAULT_INDICATORS, ema: true, boll: true },
      volume: true, oscillator: 'rsi', style: 'line', scale: 'log',
    }));
    expect(loadIndicators().boll).toBe(true);
    saveIndicators({ ...DEFAULT_INDICATORS, volume: false });
    const stored = JSON.parse(localStorage.getItem('aragon.chart.v2')!);
    expect(stored.oscillator).toBe('rsi');
    expect(stored.main.ema).toBe(true);
    expect(stored.volume).toBe(false);
    expect(localStorage.getItem('aragon.indicators.v1')).toBe(legacy);
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
