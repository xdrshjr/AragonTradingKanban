import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act, fireEvent, render, screen, within,
} from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PricePanel } from '@/components/chart/PricePanel';
import { ChartReadout } from '@/components/chart/ChartReadout';
import { ChartSettings } from '@/components/chart/ChartSettings';
import {
  ChartToolbar, type ChartToolbarProps,
} from '@/components/chart/ChartToolbar';
import { ChartDataTable } from '@/components/chart/ChartDataTable';
import type { CandleChartProps } from '@/components/chart/CandleChart';
import {
  INDICATOR_KEYS, buildChartDataset, type ChartSelection,
} from '@/lib/chartDataset';
import { DEFAULT_CHART_PREFERENCES } from '@/lib/chartPreferences';
import { copy } from '@/lib/copy';
import { deltaArrow, epochToDate, num } from '@/lib/format';
import type { Bar, BarsResponse } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  bars: vi.fn<() => Promise<BarsResponse>>(),
  lastChartBars: [] as Bar[],
  select: null as ((selection: ChartSelection) => void) | null,
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, api: { ...actual.api, bars: mocks.bars } };
});

// Native canvas behaviour belongs to controller/E2E tests; keep real UI here.
vi.mock('@/components/chart/CandleChart', async () => {
  const { forwardRef } = await import('react');
  return {
    CandleChart: forwardRef<unknown, CandleChartProps>(function Chart(
      props, ref,
    ) {
      void ref;
      mocks.lastChartBars = props.bars;
      mocks.select = props.onSelection;
      return <div data-testid="candle-chart">{props.bars.length}</div>;
    }),
  };
});

const QUOTE_LAST = 1634.88;
const QUOTE_CHANGE = -2.22;
const BAR_LAST = 1729.52;
const BAR_PREV = 1721.63;
const BAR_TS = Date.UTC(2026, 10, 16, 7) / 1000;
const clients: QueryClient[] = [];
const baseProps = {
  instrumentId: 'CN:600519.SH', symbol: '600519.SH',
  name: '贵州茅台', currency: 'CNY',
};
const LATEST: ChartSelection = { index: null, origin: 'latest' };

function bar(ts: number, close: number): Bar {
  return { ts, o: close, h: close, l: close, c: close,
    v: 1_000_000, amount: null };
}

function barsResponse(bars: Bar[]): BarsResponse {
  return {
    instrument: {
      id: baseProps.instrumentId, symbol: baseProps.symbol,
      name: baseProps.name, market: 'CN', currency: 'CNY',
    } as BarsResponse['instrument'],
    bars,
    indicators: Object.fromEntries(INDICATOR_KEYS.map((key) => [
      key, bars.map(() => null),
    ])),
    meta: {
      source: 'synthetic', barsCount: bars.length,
      fromTs: bars[0]?.ts ?? null, toTs: bars.at(-1)?.ts ?? null,
      adjust: 'qfq', timeframe: '1d', cached: true, stale: false,
      warnings: [],
    },
    chart: {
      version: 'chart/v1', calculationBarsCount: bars.length,
      warmupBarsCount: 0, historyLimited: true,
      volumeUnit: 'unit', volumeUnitSize: null, amountCurrency: 'CNY',
      effectiveAdjust: 'synthetic', returnBasis: 'adjacent_close',
      indicatorConvention: 'aragon-chart-v1',
      stats: bars.map((row, index) => {
        const previous = bars[index - 1]?.c ?? null;
        const valid = previous !== null && previous > 0;
        return {
          ts: row.ts, periodStart: epochToDate(row.ts),
          periodEnd: epochToDate(row.ts), leadingPartial: false,
          previousClose: previous,
          change: valid ? row.c - previous : null,
          changePct: valid ? (row.c / previous - 1) * 100 : null,
          amplitudePct: valid ? (row.h - row.l) / previous * 100 : null,
        };
      }),
    },
  };
}

function latestResponse() {
  return barsResponse([
    bar(BAR_TS - 86400 * 3, BAR_PREV), bar(BAR_TS, BAR_LAST),
  ]);
}

function renderPanel(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: {
    queries: { retry: false, retryDelay: 0, gcTime: 0 },
  } });
  clients.push(client);
  return { client, ...render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  ) };
}

function getHeader() {
  return within(document.querySelector<HTMLElement>('.chart-header')!);
}

beforeEach(() => {
  mocks.bars.mockReset();
  mocks.lastChartBars = [];
  mocks.select = null;
  localStorage.clear();
});

afterEach(() => {
  clients.splice(0).forEach(client => client.clear());
  vi.useRealTimers();
});

describe('PricePanel 头部一对数字同源', () => {
  it('价格、涨幅及日期跟随 bars，quote 给出相反结论也不混源', async () => {
    mocks.bars.mockResolvedValue(latestResponse());
    renderPanel(<PricePanel {...baseProps}
      lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);
    expect(await getHeader().findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(getHeader().getByText(deltaArrow(
      (BAR_LAST / BAR_PREV - 1) * 100,
    ))).toBeInTheDocument();
    expect(getHeader().queryByText(deltaArrow(QUOTE_CHANGE))).toBeNull();
    expect(screen.getAllByText(epochToDate(BAR_TS)).length).toBeGreaterThan(0);
    await screen.findByTestId('candle-chart');
    expect(mocks.lastChartBars.at(-1)?.c).toBe(BAR_LAST);
    expect(mocks.bars).toHaveBeenCalledWith(
      baseProps.instrumentId, '1d', '1y', 'qfq', { profile: 'chart' },
    );
  });

  it('空 chart 包整对回落独立报价，且不渲染图表', async () => {
    mocks.bars.mockResolvedValue(barsResponse([]));
    renderPanel(<PricePanel {...baseProps}
      lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);
    await screen.findByText(copy.chart.empty);
    expect(getHeader().getByText(num(QUOTE_LAST, 2))).toBeInTheDocument();
    expect(getHeader().getByText(deltaArrow(QUOTE_CHANGE))).toBeInTheDocument();
    expect(getHeader().getByText('独立报价')).toBeInTheDocument();
    expect(screen.queryByTestId('candle-chart')).toBeNull();
  });

  it('真正首历史柱缺前收时不编造涨幅或借用 quote', async () => {
    mocks.bars.mockResolvedValue(barsResponse([bar(BAR_TS, BAR_LAST)]));
    renderPanel(<PricePanel {...baseProps}
      lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);
    expect(await getHeader().findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(getHeader().queryByText(deltaArrow(QUOTE_CHANGE))).toBeNull();
    expect(getHeader().getByText(deltaArrow(null))).toBeInTheDocument();
    expect(screen.getByText('缺少有效前收基准')).toBeInTheDocument();
  });

  it('空包且独立报价缺价格时整对为空，不残留孤立涨幅', async () => {
    mocks.bars.mockResolvedValue(barsResponse([]));
    renderPanel(<PricePanel {...baseProps}
      lastPrice={null} changePct={QUOTE_CHANGE} />);
    await screen.findByText(copy.chart.empty);
    expect(getHeader().queryByText(deltaArrow(QUOTE_CHANGE))).toBeNull();
    expect(getHeader().getAllByText('—')).toHaveLength(2);
  });

  it('仅一根显示柱仍使用窗口外前收的后端 stats', async () => {
    const response = barsResponse([bar(BAR_TS, 105)]);
    response.chart!.warmupBarsCount = 1;
    response.chart!.calculationBarsCount = 2;
    Object.assign(response.chart!.stats[0]!, {
      previousClose: 100, change: 5, changePct: 5, amplitudePct: 0,
    });
    mocks.bars.mockResolvedValue(response);
    renderPanel(<PricePanel {...baseProps} changePct={QUOTE_CHANGE} />);
    expect(await getHeader().findByText(deltaArrow(5))).toBeInTheDocument();
    expect(getHeader().getByText('105.00')).toBeInTheDocument();
  });

  it('bars 与 quote 各说各话时头部只出现 bars 价格', async () => {
    mocks.bars.mockResolvedValue(latestResponse());
    renderPanel(<PricePanel {...baseProps}
      lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);
    expect(await getHeader().findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(getHeader().queryByText(num(QUOTE_LAST, 2))).toBeNull();
  });

  it('历史 hover 只更新读数，头部和图表尾价保持最新', async () => {
    mocks.bars.mockResolvedValue(latestResponse());
    renderPanel(<PricePanel {...baseProps} />);
    await screen.findByTestId('candle-chart');
    act(() => mocks.select?.({ index: 0, origin: 'pointer' }));
    const readout = within(screen.getByRole('region', { name: '行情读数' }));
    expect(readout.getByText('历史检视')).toBeInTheDocument();
    expect(readout.getAllByText(num(BAR_PREV, 2)).length).toBeGreaterThan(0);
    expect(getHeader().getByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(getHeader().queryByText(num(BAR_PREV, 2))).toBeNull();
  });
});

describe('PricePanel 跨源提示（PCD-2）', () => {
  it('空包回落真实独立报价时，来源和合成徽标一起切换', async () => {
    mocks.bars.mockResolvedValue(barsResponse([]));
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST}
      changePct={QUOTE_CHANGE} quoteSource="akshare" />);
    await screen.findByText(copy.chart.empty);
    expect(screen.getByText(`${copy.chart.dataSource}：akshare`))
      .toBeInTheDocument();
    expect(screen.queryByText(copy.chart.synthetic)).toBeNull();
  });

  it('空包回落合成独立报价时，不借用空包的真实来源徽标', async () => {
    const response = barsResponse([]);
    response.meta.source = 'akshare';
    mocks.bars.mockResolvedValue(response);
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST}
      changePct={QUOTE_CHANGE} quoteSource="synthetic" />);
    await screen.findByText(copy.chart.empty);
    expect(screen.getByText(`${copy.chart.dataSource}：synthetic`))
      .toBeInTheDocument();
    expect(screen.getByText(copy.chart.synthetic)).toBeInTheDocument();
  });

  it('不同来源的 divergent_source 明确提示且不误称陈旧', async () => {
    const response = latestResponse();
    response.meta = {
      ...response.meta, source: 'beta', reasonCode: 'divergent_source',
    };
    mocks.bars.mockResolvedValue(response);
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST}
      changePct={QUOTE_CHANGE} quoteSource="alpha" />);
    expect(await screen.findByText(copy.chart.sourceDivergent))
      .toBeInTheDocument();
    expect(screen.queryByText(/陈旧/)).toBeNull();
  });

  it('相同来源不显示分叉徽标', async () => {
    mocks.bars.mockResolvedValue(latestResponse());
    renderPanel(<PricePanel {...baseProps} lastPrice={QUOTE_LAST}
      changePct={QUOTE_CHANGE} quoteSource="synthetic" />);
    expect(await getHeader().findByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(screen.queryByText(copy.chart.sourceDivergent)).toBeNull();
  });
});

describe('PricePanel 请求失败及兼容边界', () => {
  it('首次请求失败显示错误并整对回落独立报价', async () => {
    mocks.bars.mockRejectedValue(new Error('行情服务暂不可用'));
    renderPanel(<PricePanel {...baseProps}
      lastPrice={QUOTE_LAST} changePct={QUOTE_CHANGE} />);
    expect(await screen.findByText(/行情服务暂不可用/, {}, {
      timeout: 3000,
    })).toBeInTheDocument();
    expect(getHeader().getByText('独立报价')).toBeInTheDocument();
    expect(getHeader().getByText(num(QUOTE_LAST, 2))).toBeInTheDocument();
    expect(getHeader().getByText(deltaArrow(QUOTE_CHANGE))).toBeInTheDocument();
  });

  it('同 key 刷新失败保留有效包和图表并提示失败', async () => {
    mocks.bars.mockResolvedValueOnce(latestResponse());
    const { client } = renderPanel(<PricePanel {...baseProps} />);
    await screen.findByTestId('candle-chart');
    mocks.bars.mockRejectedValue(new Error('刷新失败现场'));
    await act(async () => {
      await client.refetchQueries({ queryKey: ['bars'] });
    });
    expect(await screen.findByText(/刷新失败，保留旧数据/)).toBeInTheDocument();
    expect(getHeader().getByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(screen.getByTestId('candle-chart')).toBeInTheDocument();
  });

  it('旧服务 chart=null 明确简化模式且关闭完整包工具', async () => {
    const response = latestResponse();
    response.chart = null;
    mocks.bars.mockResolvedValue(response);
    renderPanel(<PricePanel {...baseProps} changePct={QUOTE_CHANGE} />);
    expect(await screen.findByText(/当前服务未支持完整图表数据/))
      .toBeInTheDocument();
    expect(getHeader().getByText(num(BAR_LAST, 2))).toBeInTheDocument();
    expect(getHeader().getByText(deltaArrow(
      (BAR_LAST / BAR_PREV - 1) * 100,
    ))).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '行情读数' })).toBeNull();
    fireEvent.click(screen.getByText('更多'));
    expect(screen.getByRole('button', { name: '测量' }))
      .toBeDisabled();
  });
});

describe('行情展示组件', () => {
  it('零成交量保持零，微价保留精度，缺失成交额不补造', () => {
    const response = barsResponse([{ ...bar(BAR_TS, 0.0005), v: 0 }]);
    render(<ChartReadout dataset={buildChartDataset(response, 1)}
      preferences={DEFAULT_CHART_PREFERENCES} selection={LATEST} />);
    const readout = screen.getByRole('region', { name: '行情读数' });
    expect(within(readout).getAllByText('0.000500').length).toBeGreaterThan(0);
    expect(within(readout).getByText('0单位')).toBeInTheDocument();
    const amount = within(readout).getByText('成交额').parentElement!;
    expect(within(amount).getByText('—')).toBeInTheDocument();
    expect(amount).toHaveAttribute('title', '数据源未提供成交额');
  });

  it('不足历史包含预热计数，边界首桶单独说明', () => {
    const response = barsResponse([bar(BAR_TS, 100)]);
    response.chart!.warmupBarsCount = 12;
    response.chart!.calculationBarsCount = 13;
    const dataset = buildChartDataset(response, 1);
    const { rerender } = render(<ChartReadout dataset={dataset}
      preferences={DEFAULT_CHART_PREFERENCES} selection={LATEST} />);
    expect(screen.getAllByText(/需要 34 根，已有 13 根/).length)
      .toBeGreaterThan(0);
    const partial = structuredClone(response);
    partial.chart!.stats[0]!.leadingPartial = true;
    partial.chart!.warmupBarsCount = 0;
    partial.chart!.calculationBarsCount = 0;
    rerender(<ChartReadout dataset={buildChartDataset(partial, 2)}
      preferences={DEFAULT_CHART_PREFERENCES} selection={LATEST} />);
    expect(screen.getAllByText(/计算边界周期不完整/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/历史不足/)).toBeNull();
  });

  it('仅键盘检视在 150ms 后播报，鼠标不更新 live region', () => {
    vi.useFakeTimers();
    const dataset = buildChartDataset(latestResponse(), 1);
    const props = { dataset, preferences: DEFAULT_CHART_PREFERENCES };
    const { container, rerender } = render(<ChartReadout {...props}
      selection={LATEST} />);
    const live = container.querySelector('[aria-live="polite"]')!;
    rerender(<ChartReadout {...props}
      selection={{ index: 0, origin: 'pointer' }} />);
    act(() => vi.advanceTimersByTime(200));
    expect(live).toHaveTextContent('');
    rerender(<ChartReadout {...props}
      selection={{ index: 0, origin: 'keyboard' }} />);
    act(() => vi.advanceTimersByTime(149));
    expect(live).toHaveTextContent('');
    act(() => vi.advanceTimersByTime(1));
    expect(live).toHaveTextContent('收盘');
    const spoken = live.textContent;
    rerender(<ChartReadout {...props}
      selection={{ index: 1, origin: 'pointer' }} />);
    act(() => vi.advanceTimersByTime(200));
    expect(live.textContent).toBe(spoken);
  });

  it('设置立即回调、恢复默认，非法对数保留原设置', () => {
    const onChange = vi.fn();
    const onInvalidLog = vi.fn();
    render(<ChartSettings open onOpenChange={vi.fn()}
      preferences={DEFAULT_CHART_PREFERENCES} onChange={onChange}
      canUseLog={false} onInvalidLog={onInvalidLog} />);
    fireEvent.click(screen.getByRole('radio', { name: /RSI14/ }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_CHART_PREFERENCES, oscillator: 'rsi',
    });
    fireEvent.click(screen.getByRole('checkbox', { name: /BOLL/ }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_CHART_PREFERENCES,
      main: { ...DEFAULT_CHART_PREFERENCES.main, boll: true },
    });
    fireEvent.change(screen.getByRole('combobox', { name: '主图坐标' }), {
      target: { value: 'log' },
    });
    expect(onInvalidLog).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));
    expect(onChange).toHaveBeenLastCalledWith(DEFAULT_CHART_PREFERENCES);
  });

  it('报告反查禁用时测量仍可用，周期和视区命令独立', () => {
    const props = toolbarProps();
    render(<ChartToolbar {...props} />);
    fireEvent.click(screen.getByText('更多'));
    const report = screen.getByRole('button', { name: '报告框选' });
    expect(report).toBeDisabled();
    expect(report).toHaveAttribute('title', expect.stringContaining('未启用'));
    const measure = screen.getByRole('button', { name: '测量' });
    expect(measure).toBeEnabled();
    fireEvent.click(measure);
    expect(props.onToolChange).toHaveBeenCalledWith('measure');
    fireEvent.click(screen.getByRole('button', { name: '放大' }));
    expect(props.onZoomIn).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '周' }));
    expect(props.onViewChange).toHaveBeenCalledWith({
      ...props.view, timeframe: '1w',
    });
  });

  it('数据表默认末 20 根，可翻页、定位及同步键盘选中', () => {
    const rows = Array.from({ length: 45 }, (_, index) =>
      bar(BAR_TS - (44 - index) * 86400, 100 + index));
    const dataset = buildChartDataset(barsResponse(rows), 1);
    const onSelect = vi.fn();
    const props = { dataset, preferences: DEFAULT_CHART_PREFERENCES, onSelect };
    const { rerender } = render(<ChartDataTable {...props}
      selection={LATEST} />);
    expect(screen.getAllByRole('row')).toHaveLength(21);
    expect(screen.queryByRole('rowheader', {
      name: dataset.stats[0]!.periodEnd,
    })).toBeNull();
    expect(screen.getByRole('rowheader', {
      name: dataset.stats[44]!.periodEnd,
    })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '前 20 根' }));
    expect(screen.getByText('6–25 / 45')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('表格日期'), {
      target: { value: dataset.stats[0]!.periodEnd },
    });
    expect(onSelect).toHaveBeenCalledWith(0);
    rerender(<ChartDataTable {...props}
      selection={{ index: 44, origin: 'keyboard' }} />);
    const selected = screen.getByRole('rowheader', {
      name: dataset.stats[44]!.periodEnd,
    }).parentElement!;
    expect(selected).toHaveAttribute('aria-selected', 'true');
  });

  it('同根数刷新后，数据表仍显示键盘检视的历史行', () => {
    const rows = Array.from({ length: 45 }, (_, index) =>
      bar(BAR_TS - (44 - index) * 86400, 100 + index));
    const dataset = buildChartDataset(barsResponse(rows), 1);
    const selection: ChartSelection = { index: 5, origin: 'keyboard' };
    const props = { preferences: DEFAULT_CHART_PREFERENCES,
      onSelect: vi.fn(), selection };
    const { rerender } = render(<ChartDataTable {...props}
      dataset={dataset} />);
    const selectedRow = () => screen.getByRole('rowheader', {
      name: dataset.stats[5]!.periodEnd,
    }).parentElement!;
    expect(selectedRow()).toHaveAttribute('aria-selected', 'true');
    rerender(<ChartDataTable {...props}
      dataset={buildChartDataset(barsResponse(rows), 2)} />);
    expect(selectedRow()).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('6–25 / 45')).toBeInTheDocument();
  });
});

function toolbarProps(): ChartToolbarProps {
  return {
    view: { timeframe: '1d', range: '1y', adjust: 'qfq' },
    onViewChange: vi.fn(), tool: 'pan', onToolChange: vi.fn(),
    rangeQueryEnabled: false, onZoomIn: vi.fn(), onZoomOut: vi.fn(),
    onFit: vi.fn(), onLatest: vi.fn(), onLocate: vi.fn(),
    onSettings: vi.fn(), onExpand: vi.fn(), onToggleTable: vi.fn(),
    onCsv: vi.fn(), onPng: vi.fn(), onRefresh: vi.fn(),
  };
}
