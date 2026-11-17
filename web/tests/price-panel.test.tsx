import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { PricePanel } from '@/components/chart/PricePanel';
import { copy } from '@/lib/copy';
import { deltaArrow, epochToDate, num } from '@/lib/format';
import type { Bar, BarsResponse } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  bars: vi.fn<() => Promise<BarsResponse>>(),
  /** What `CandleChart` was handed — the axis label prints `bars[-1].c`. */
  lastChartBars: [] as Bar[],
}));

// 只替换 `api.bars`：`ApiError` 等真实导出保留。
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, api: { ...actual.api, bars: mocks.bars } };
});

// lightweight-charts 需要真实 canvas；这里只关心头部那一对数字与图表拿到的是不是同一批 bar。
vi.mock('@/components/chart/CandleChart', () => ({
  CandleChart: ({ bars }: { bars: Bar[] }) => {
    mocks.lastChartBars = bars;
    return <div data-testid="candle-chart">{bars.length}</div>;
  },
}));

/**
 * BUG-2 / R-3：面板头部的那一对数字必须同源。
 *
 * 缺陷现场（`analysis.md` §1）：头部渲染 `1,729.52 ▼2.2%`，而价格取自 `bars`、
 * 涨跌幅取自 `quote`——日线序列自己的前收是 1,721.63，对应 `+0.46%`。屏幕上从未
 * 存在过一个同时满足这两个数的交易日。左栏同一时刻显示 `1,634.88 ▼2.2%`。
 *
 * 修法的方向不是"优先 quote"（那样只会把矛盾搬到价格轴标签旁边，见 R-3），而是
 * **一对数字同源 + 同源的标签跟着走**：chart 有 bar 时整对用 bars（价格/涨跌/日期），
 * 只有图里没有 bar 时整对回落 quote。
 */

const QUOTE_LAST = 1634.88;
const QUOTE_CHANGE = -2.22;
const BAR_LAST = 1729.52;
const BAR_PREV = 1721.63;
const BAR_TS = Date.UTC(2026, 10, 16, 7, 0) / 1000; // 2026-11-16 15:00 Asia/Shanghai

function bar(ts: number, close: number): Bar {
  return { ts, o: close, h: close, l: close, c: close, v: 1_000_000 };
}

function barsResponse(bars: Bar[]): BarsResponse {
  return {
    instrument: {
      id: 'CN:600519.SH',
      symbol: '600519.SH',
      name: '贵州茅台',
      market: 'CN',
      currency: 'CNY',
    } as BarsResponse['instrument'],
    bars,
    indicators: {},
    meta: {
      source: 'synthetic',
      barsCount: bars.length,
      fromTs: bars[0]?.ts ?? null,
      toTs: bars[bars.length - 1]?.ts ?? null,
      adjust: 'qfq',
      timeframe: '1d',
      cached: true,
      stale: false,
      warnings: [],
    },
  };
}

function renderPanel(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const baseProps = {
  instrumentId: 'CN:600519.SH',
  symbol: '600519.SH',
  name: '贵州茅台',
  currency: 'CNY',
};

describe('PricePanel 头部一对数字同源', () => {
  it('bars 在场时价格、涨跌幅、日期全部来自 bars —— 即便 quote 给出相反结论', async () => {
    mocks.lastChartBars = [];
    mocks.bars.mockResolvedValue(barsResponse([bar(BAR_TS - 86400 * 3, BAR_PREV), bar(BAR_TS, BAR_LAST)]));
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);

    // 价格来自 bars
    expect(await screen.findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    // 涨跌幅来自同一个源：+0.46%，而不是 quote 的 ▼2.2%
    const barImplied = ((BAR_LAST / BAR_PREV - 1) * 100) as number;
    expect(screen.getByText(deltaArrow(barImplied))).toBeInTheDocument();
    expect(screen.queryByText(deltaArrow(QUOTE_CHANGE))).not.toBeInTheDocument();
    // 日期 Tag 与价格同口径（bars 的最后一根）
    expect(screen.getByText(epochToDate(BAR_TS))).toBeInTheDocument();
    // 图表拿到的是同一批 bar，右侧价格轴才会印同一个数
    expect(mocks.lastChartBars[mocks.lastChartBars.length - 1]?.c).toBe(BAR_LAST);
  });

  it('bars 为空时整对回落 quote，且不会渲染图表', async () => {
    mocks.lastChartBars = [];
    mocks.bars.mockResolvedValue(barsResponse([]));
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);

    expect(await screen.findByText(num(QUOTE_LAST, 2))).toBeInTheDocument();
    expect(screen.getByText(deltaArrow(QUOTE_CHANGE))).toBeInTheDocument();
    expect(screen.queryByTestId('candle-chart')).not.toBeInTheDocument();
  });

  it('只有一根 bar 时不编造涨跌幅', async () => {
    mocks.bars.mockResolvedValue(barsResponse([bar(BAR_TS, BAR_LAST)]));
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);

    expect(await screen.findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    // 没有前收就没有涨跌幅：既不借用 quote，也不除零
    expect(screen.queryByText(deltaArrow(QUOTE_CHANGE))).not.toBeInTheDocument();
    expect(screen.getByText(deltaArrow(null))).toBeInTheDocument();
  });

  it('bars 与 quote 各说各话时，头部只出现 bars 的那一个数', async () => {
    mocks.bars.mockResolvedValue(barsResponse([bar(BAR_TS - 86400 * 3, BAR_PREV), bar(BAR_TS, BAR_LAST)]));
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);

    expect(await screen.findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(screen.queryByText(num(QUOTE_LAST, 2))).not.toBeInTheDocument();
  });
});

/**
 * ★PCD-2：报价与 K 线落在不同 provider 上时，以前两个价并排而无人告诉读者。
 * 后端已经在 `meta.warnings` 里写了一条可读文案，这里要求界面上也有一个
 * 一眼可见的徽标，而且它**不能**靠 `stale` 来触发（那会把“混源”说成“陈旧”）。
 */
describe('PricePanel 跨源提示（PCD-2）', () => {
  it('reasonCode=divergent_source 且两侧来源不同时出徽标', async () => {
    mocks.bars.mockResolvedValue({
      ...barsResponse([bar(BAR_TS - 86400 * 3, BAR_PREV), bar(BAR_TS, BAR_LAST)]),
      meta: {
        ...barsResponse([]).meta,
        source: 'beta',
        reasonCode: 'divergent_source',
        barsCount: 2,
        stale: false,
      },
    });
    renderPanel(
      <PricePanel {...baseProps} lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} quoteSource="alpha" />,
    );

    expect(await screen.findByText(copy.chart.sourceDivergent)).toBeInTheDocument();
    // 它不是“数据陈旧”：这份数据一点也不陈旧。
    expect(screen.queryByText(/陈旧/)).not.toBeInTheDocument();
  });

  it('来源相同时不出徽标', async () => {
    mocks.bars.mockResolvedValue(barsResponse([bar(BAR_TS - 86400 * 3, BAR_PREV), bar(BAR_TS, BAR_LAST)]));
    renderPanel(
      <PricePanel {...baseProps} lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} quoteSource="synthetic" />,
    );

    expect(await screen.findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(screen.queryByText(copy.chart.sourceDivergent)).not.toBeInTheDocument();
  });
});
