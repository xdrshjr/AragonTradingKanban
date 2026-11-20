import { describe, expect, it, vi } from 'vitest';

import { createController } from '@/components/chart/ChartController';
import { DEFAULT_CHART_PREFERENCES } from '@/lib/chartPreferences';
import type { ChartDataset } from '@/lib/chartDataset';

const charts: ReturnType<typeof fakeChart>[] = [];
function fakeChart() {
  let range = { from: 0, to: 3 };
  const listeners = new Set<(value: typeof range) => void>();
  const series: { setData: ReturnType<typeof vi.fn> }[] = [];
  const add = () => {
    const line = {
      setData: vi.fn(), applyOptions: vi.fn(),
      createPriceLine: vi.fn(), removePriceLine: vi.fn(),
    };
    series.push(line);
    return line;
  };
  return {
    series, remove: vi.fn(), applyOptions: vi.fn(),
    addLineSeries: add, addCandlestickSeries: add,
    addHistogramSeries: add, removeSeries: vi.fn(),
    subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(),
    priceScale: () => ({ width: () => 72, applyOptions: vi.fn() }),
    takeScreenshot: () => document.createElement('canvas'),
    timeScale: () => ({
      getVisibleLogicalRange: () => range,
      setVisibleLogicalRange: (value: typeof range) => {
        range = value;
        listeners.forEach((listener) => listener(value));
      },
      subscribeVisibleLogicalRangeChange: (cb: typeof listeners extends
        Set<infer T> ? T : never) => listeners.add(cb),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      applyOptions: vi.fn(), coordinateToLogical: (x: number) => x,
      logicalToCoordinate: (x: number) => x,
      timeToCoordinate: () => 20,
    }),
    setCrosshairPosition: vi.fn(), clearCrosshairPosition: vi.fn(),
  };
}

vi.mock('lightweight-charts', () => ({
  createChart: () => {
    const chart = fakeChart(); charts.push(chart); return chart;
  },
  ColorType: { Solid: 'solid' }, CrosshairMode: { Normal: 0 },
  LineStyle: { Dashed: 2 }, PriceScaleMode: { Normal: 0, Logarithmic: 1 },
}));

function dataset(): ChartDataset {
  const bars = [1, 2, 3].map((c, i) => ({
    ts: 1700000000 + i * 86400, o: c, h: c, l: c, c, v: 0,
  }));
  return {
    datasetVersion: 1, bars,
    indicators: { ma5: [null, null, null] },
    stats: bars.map((bar, i) => ({
      ts: bar.ts, periodStart: `2023-11-${14 + i}`,
      periodEnd: `2023-11-${14 + i}`, leadingPartial: false,
    })),
    indexByTimestamp: new Map(bars.map((bar, i) => [bar.ts, i])),
    chart: { volumeUnit: 'share', warmupBarsCount: 0 },
    meta: { timeframe: '1d' },
  } as unknown as ChartDataset;
}

describe('同步图表控制器', () => {
  it('空指标保留全部时间点，开关pane不重建主图，销毁幂等', () => {
    charts.length = 0;
    const containers = {
      main: document.createElement('div'),
      volume: document.createElement('div'),
      oscillator: document.createElement('div'),
    };
    const controller = createController({
      containers, initialSettings: DEFAULT_CHART_PREFERENCES,
      onSelection: vi.fn(),
    });
    controller.setDataset(dataset());
    const main = charts[0]!;
    expect(main.series.some((s) => s.setData.mock.calls.some(([points]) =>
      points.length === 3 && points.every((p: { time: number }) =>
        typeof p.time === 'number')))).toBe(true);
    controller.applySettings({
      ...DEFAULT_CHART_PREFERENCES, volume: false, oscillator: 'none',
    });
    expect(main.remove).not.toHaveBeenCalled();
    expect(charts[1]!.remove).toHaveBeenCalledTimes(1);
    controller.locate(0);
    expect(controller.snapshotRuntime().viewport).toEqual({ from: -.5, to: 5 });
    controller.setViewport({ from: -10, to: 20 });
    const sameDataset = controller.snapshotRuntime();
    controller.setViewport({ from: 0, to: 3 });
    controller.restoreRuntime(sameDataset);
    expect(controller.snapshotRuntime().viewport).toEqual({ from: -10, to: 20 });
    controller.setViewport({ from: -1, to: 2 });
    expect(controller.snapshotRuntime().viewport).toEqual({ from: -1, to: 2 });
    controller.setViewport({ from: 0, to: 1 });
    controller.setSelection(2, 'keyboard');
    const visible = controller.snapshotRuntime().viewport!;
    expect(visible.from).toBeLessThanOrEqual(2);
    expect(visible.to).toBeGreaterThanOrEqual(2);
    controller.destroy(); controller.destroy();
    expect(main.remove).toHaveBeenCalledTimes(1);
  });

  it('同索引刷新为null时清除旧水平光标，选择按ts恢复', () => {
    charts.length = 0;
    const onSelection = vi.fn();
    const controller = createController({
      containers: {
        main: document.createElement('div'),
        volume: document.createElement('div'),
        oscillator: document.createElement('div'),
      }, initialSettings: {
        ...DEFAULT_CHART_PREFERENCES, oscillator: 'rsi',
      }, onSelection,
    });
    const original = dataset();
    original.indicators.rsi14 = [null, 60, 70];
    controller.setDataset(original);
    controller.setSelection(1, 'keyboard');
    const oscillator = charts[2]!;
    expect(oscillator.setCrosshairPosition).toHaveBeenCalled();
    oscillator.clearCrosshairPosition.mockClear();
    const updated = { ...original, datasetVersion: 2, indicators: {
      ...original.indicators, rsi14: [null, null, null],
    } };
    controller.setDataset(updated);
    expect(oscillator.clearCrosshairPosition).toHaveBeenCalled();
    expect(controller.snapshotRuntime().selection).toEqual({
      index: 1, origin: 'keyboard',
    });
    const inserted = { ...updated, datasetVersion: 3,
      bars: [{ ...updated.bars[0]!, ts: 1699900000 }, ...updated.bars],
    };
    inserted.indexByTimestamp = new Map(inserted.bars.map((b, i) => [b.ts, i]));
    inserted.stats = [updated.stats[0]!, ...updated.stats];
    controller.setDataset(inserted);
    expect(controller.snapshotRuntime().selection.index).toBe(2);
    controller.destroy();
  });

  it('程序事件不覆盖固定选择，跨pane待清理不会闪回最新', () => {
    vi.useFakeTimers();
    charts.length = 0;
    const containers = {
      main: document.createElement('div'),
      volume: document.createElement('div'),
      oscillator: document.createElement('div'),
    };
    const controller = createController({ containers,
      initialSettings: DEFAULT_CHART_PREFERENCES, onSelection: vi.fn() });
    controller.setDataset(dataset());
    const move = charts[0]!.subscribeCrosshairMove.mock.calls[0]![0];
    containers.main.dispatchEvent(new Event('pointerenter'));
    controller.setSelection(1, 'touch');
    move({ time: undefined });
    vi.advanceTimersByTime(20);
    expect(controller.snapshotRuntime().selection.origin).toBe('touch');
    controller.setSelection(1, 'pointer');
    move({ time: undefined, sourceEvent: {} });
    containers.volume.dispatchEvent(new Event('pointerenter'));
    vi.advanceTimersByTime(20);
    expect(controller.snapshotRuntime().selection.index).toBe(1);
    controller.destroy(); vi.useRealTimers();
  });

  it('原生范围延迟应用后在下一帧重新定位共享竖线', () => {
    vi.useFakeTimers(); charts.length = 0;
    const main = document.createElement('div');
    const controller = createController({ containers: { main,
      volume: document.createElement('div'),
      oscillator: document.createElement('div'),
    }, initialSettings: DEFAULT_CHART_PREFERENCES, onSelection: vi.fn() });
    controller.setDataset(dataset());
    vi.advanceTimersByTime(40);
    let coordinate = 20;
    const chart = charts[0]!;
    const scale = chart.timeScale();
    chart.timeScale = () => ({ ...scale,
      timeToCoordinate: () => coordinate,
    });
    controller.setSelection(1, 'keyboard');
    controller.setViewport({ from: -.5, to: 2 });
    coordinate = 40;
    vi.advanceTimersByTime(20);
    expect(main.querySelector<HTMLElement>('.chart-shared-crosshair')
      ?.style.left).toBe('40px');
    controller.destroy(); vi.useRealTimers();
  });
});
