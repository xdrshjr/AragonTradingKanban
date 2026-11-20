import {
  ColorType, CrosshairMode, LineStyle, PriceScaleMode, createChart,
  type IChartApi, type ISeriesApi, type SeriesType,
  type LogicalRange, type MouseEventParams, type Time,
  type UTCTimestamp, type AutoscaleInfo,
} from 'lightweight-charts';

import type {
  ChartDataset, ChartSelection, ChartTool,
} from '@/lib/chartDataset';
import type { ChartPreferences } from '@/lib/chartPreferences';
import { formatVolume, pricePrecision } from '@/lib/chartFormat';

type PaneName = 'main' | 'volume' | 'oscillator';
type Series = ISeriesApi<SeriesType>;
interface Range { from: number; to: number }
interface Pane {
  name: PaneName;
  container: HTMLElement;
  chart: IChartApi;
  series: Map<string, Series>;
  carrier: ISeriesApi<'Line'>;
  guide: HTMLDivElement;
  rangeListener: (range: LogicalRange | null) => void;
  crosshairListener: (event: MouseEventParams<Time>) => void;
  pointerListener: (event: PointerEvent) => void;
  pointerHost: HTMLElement;
}
export interface ChartRuntime {
  datasetVersion?: number;
  viewport: Range | null;
  selection: ChartSelection;
  selectionTs?: number;
  leftTs?: number;
  leftOffset?: number;
  rightTs?: number;
  rightOffset?: number;
  followsLatest?: boolean;
  tool: ChartTool;
}
export interface ControllerOptions {
  containers: Record<PaneName, HTMLElement>;
  initialSettings: ChartPreferences;
  onSelection: (selection: ChartSelection) => void;
  onViewport?: (range: Range) => void;
  onMessage?: (message: string) => void;
}
export interface ChartControllerApi {
  setDataset: (dataset: ChartDataset) => void;
  applySettings: (settings: ChartPreferences) => void;
  setTool: (tool: ChartTool) => void;
  setGesturing: (active: boolean) => void;
  setSelection: (index: number | null,
    origin: ChartSelection['origin']) => void;
  releasePointer: () => void;
  setViewport: (range: Range) => void;
  zoom: (factor: number) => void;
  fit: () => void;
  latest: () => void;
  locate: (index: number) => void;
  coordinateToIndex: (target: HTMLElement, clientX: number,
    clamp?: boolean, clientY?: number) => number | null;
  snapshotRuntime: () => ChartRuntime;
  restoreRuntime: (runtime: ChartRuntime) => void;
  capturePanes: () => {
    canvas: HTMLCanvasElement; width: number; height: number; label: string;
  }[];
  refreshTheme: () => void;
  destroy: () => void;
}

const COLORS: Record<string, string> = {
  ma5: '#f5a623', ma10: '#4c9aff', ma20: '#a78bfa', ma60: '#22d3ee',
  ema12: '#fb923c', ema26: '#818cf8', bollUpper: '#f472b6',
  bollMiddle: '#eab308', bollLower: '#f472b6', volumeMa5: '#eab308',
  volumeMa10: '#60a5fa', macd: '#eab308', macdSignal: '#a78bfa',
  kdjK: '#eab308', kdjD: '#60a5fa', kdjJ: '#e879f9',
  rsi14: '#c084fc', atr14: '#38bdf8',
};
const OSCILLATORS: Record<string, string[]> = {
  macd: ['macd', 'macdSignal', 'macdHistogram'], rsi: ['rsi14'],
  kdj: ['kdjK', 'kdjD', 'kdjJ'], atr: ['atr14'], none: [],
};

function readTheme() {
  const css = getComputedStyle(document.documentElement);
  const value = (key: string, fallback: string) =>
    css.getPropertyValue(key).trim() || fallback;
  return {
    text: value('--text-tertiary', '#89919d'),
    grid: value('--chart-grid', 'rgba(120,130,150,.12)'),
    border: value('--border-strong', '#39404d'),
    up: value('--chart-up', '#f2564d'),
    down: value('--chart-down', '#2fbf71'),
  };
}

function rangesEqual(a: Range | null, b: Range | null) {
  return Boolean(a && b && Math.abs(a.from - b.from) <= 1e-6
    && Math.abs(a.to - b.to) <= 1e-6);
}

/** Keeps native chart state separate from PricePanel's data and preferences. */
export function createController(
  options: ControllerOptions,
): ChartControllerApi {
  return new ChartController(options);
}

class ChartController implements ChartControllerApi {
  private panes = new Map<PaneName, Pane>();
  private dataset: ChartDataset | null = null;
  private settings: ChartPreferences;
  private selection: ChartSelection = { index: null, origin: 'latest' };
  private tool: ChartTool = 'pan';
  private viewport: Range | null = null;
  private pendingRange: Range | null = null;
  private rangeFrame = 0;
  private selectFrame = 0;
  private layoutFrame = 0;
  private pendingSelection: ChartSelection | null = null;
  private pendingClearPane: PaneName | null = null;
  private activePane: PaneName | null = null;
  private inputType = 'mouse';
  private syncing = false;
  private destroyed = false;
  private axisWidth = 72;
  private observer: ResizeObserver;

  constructor(private options: ControllerOptions) {
    this.settings = options.initialSettings;
    this.observer = new ResizeObserver(() => this.resize());
    this.addPane('main');
    this.reconcilePanes();
    this.resize();
  }

  private axisDate(time: Time) {
    const ts = Number(time);
    const index = this.dataset?.indexByTimestamp.get(ts);
    return index === undefined ? '' : this.dataset!.stats[index]!.periodEnd;
  }

  private addPane(name: PaneName) {
    const container = this.options.containers[name];
    const theme = readTheme();
    const chart = createChart(container, {
      width: Math.max(1, container.clientWidth),
      height: Math.max(name === 'main' ? 220 : 110, container.clientHeight),
      layout: { background: { type: ColorType.Solid, color: 'transparent' },
        textColor: theme.text, fontSize: 10 },
      grid: { vertLines: { color: theme.grid },
        horzLines: { color: theme.grid } },
      crosshair: { mode: CrosshairMode.Normal,
        vertLine: { visible: false }, horzLine: { color: theme.text } },
      rightPriceScale: { minimumWidth: this.axisWidth,
        borderColor: theme.border, scaleMargins: { top: .1, bottom: .08 } },
      leftPriceScale: { visible: false },
      timeScale: { rightOffset: 3, minBarSpacing: .01,
        borderColor: theme.border,
        tickMarkFormatter: (time: Time) => this.axisDate(time) },
      localization: { locale: 'zh-CN',
        timeFormatter: (time: Time) => this.axisDate(time) },
    });
    const carrier = chart.addLineSeries({ priceScaleId: '_carrier',
      color: 'transparent', lineVisible: false, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null });
    chart.priceScale('_carrier').applyOptions({ visible: false });
    const guide = document.createElement('div');
    guide.className = 'chart-shared-crosshair';
    guide.hidden = true; container.appendChild(guide);
    this.registerPane({ name, container, chart, carrier, guide,
      pointerHost: container.parentElement ?? container,
      series: new Map(), rangeListener: () => {},
      crosshairListener: () => {}, pointerListener: () => {} });
  }

  private registerPane(pane: Pane) {
    pane.rangeListener = (range) => this.queueRange(range);
    pane.crosshairListener = (event) => this.onCrosshair(pane, event);
    pane.pointerListener = (event) => {
      this.activePane = pane.name; this.inputType = event.pointerType;
    };
    pane.chart.timeScale()
      .subscribeVisibleLogicalRangeChange(pane.rangeListener);
    pane.chart.subscribeCrosshairMove(pane.crosshairListener);
    pane.pointerHost.addEventListener('pointermove', pane.pointerListener);
    pane.pointerHost.addEventListener('pointerenter', pane.pointerListener);
    this.observer.observe(pane.container);
    this.panes.set(pane.name, pane);
  }

  private removePane(pane: Pane) {
    this.observer.unobserve(pane.container);
    pane.pointerHost.removeEventListener('pointermove', pane.pointerListener);
    pane.pointerHost.removeEventListener('pointerenter', pane.pointerListener);
    pane.chart.timeScale()
      .unsubscribeVisibleLogicalRangeChange(pane.rangeListener);
    pane.chart.unsubscribeCrosshairMove(pane.crosshairListener);
    pane.guide.remove(); pane.chart.remove();
    this.panes.delete(pane.name);
  }

  private reconcilePanes() {
    const desired: PaneName[] = ['main'];
    if (this.settings.volume) desired.push('volume');
    if (this.settings.oscillator !== 'none') desired.push('oscillator');
    for (const pane of this.panes.values()) {
      if (!desired.includes(pane.name)) this.removePane(pane);
    }
    for (const name of desired) {
      if (!this.panes.has(name)) this.addPane(name);
    }
    desired.forEach((name, index) => this.panes.get(name)!.chart
      .timeScale().applyOptions({ visible: index === desired.length - 1 }));
  }

  private keysFor(pane: Pane) {
    if (pane.name === 'volume') return ['volume', 'volumeMa5', 'volumeMa10'];
    if (pane.name === 'oscillator') {
      return OSCILLATORS[this.settings.oscillator]!;
    }
    const keys: string[] = [this.settings.style];
    Object.entries(this.settings.main).forEach(([key, enabled]) => {
      if (!enabled) return;
      if (key === 'ema') keys.push('ema12', 'ema26');
      else if (key === 'boll') {
        keys.push('bollUpper', 'bollMiddle', 'bollLower');
      }
      else keys.push(key);
    });
    return keys;
  }

  private values(key: string) {
    if (!this.dataset) return [];
    if (key === 'volume') return this.dataset.bars.map((bar) => bar.v);
    if (key === 'line' || key === 'candlestick') {
      return this.dataset.bars.map((bar) => bar.c);
    }
    const fields = this.dataset.indicators as unknown as
      Record<string, (number | null)[]>;
    return fields[key] ?? this.dataset.bars.map(() => null);
  }

  private valueAt(key: string, index: number) {
    if (!this.dataset) return null;
    const bar = this.dataset.bars[index];
    if (key === 'volume') return bar?.v ?? null;
    if (key === 'line' || key === 'candlestick') return bar?.c ?? null;
    const fields = this.dataset.indicators as unknown as
      Record<string, (number | null)[]>;
    return fields[key]?.[index] ?? null;
  }

  private paneRange(pane: Pane): AutoscaleInfo | null {
    const from = Math.max(0, Math.floor(this.viewport?.from ?? 0));
    const to = Math.min(this.dataset?.bars.length ?? 0,
      Math.ceil(this.viewport?.to ?? Infinity) + 1);
    const values = this.keysFor(pane).flatMap((key) =>
      this.values(key).slice(from, to).filter((v): v is number => v !== null));
    if (!values.length) return null;
    let min = Math.min(...values), max = Math.max(...values);
    if (pane.name === 'volume' || this.settings.oscillator === 'atr') min = 0;
    if (this.settings.oscillator === 'rsi' && pane.name === 'oscillator') {
      min = 0; max = 100;
    } else if (pane.name === 'oscillator') {
      min = Math.min(min, this.settings.oscillator === 'kdj' ? 20 : 0);
      max = Math.max(max, this.settings.oscillator === 'kdj' ? 80 : 0);
    }
    return { priceRange: { minValue: min, maxValue: max || 1 } };
  }

  private addSeries(pane: Pane, key: string): Series {
    const theme = readTheme();
    const common = { priceLineVisible: false, lastValueVisible: false };
    if (key === 'candlestick') return pane.chart.addCandlestickSeries({
      ...common, borderVisible: false, upColor: theme.up,
      downColor: theme.down, wickUpColor: theme.up, wickDownColor: theme.down,
      lastValueVisible: true,
    });
    const autoscale = pane.name === 'main' ? undefined
      : () => this.paneRange(pane);
    if (key === 'volume' || key === 'macdHistogram') {
      return pane.chart.addHistogramSeries({ ...common,
        autoscaleInfoProvider: autoscale });
    }
    const line = pane.chart.addLineSeries({ ...common,
      color: COLORS[key] ?? theme.text, lineWidth: 1,
      crosshairMarkerVisible: false, autoscaleInfoProvider: autoscale });
    if (pane.name === 'oscillator') this.addReferences(line, key);
    return line;
  }

  private addReferences(line: ISeriesApi<'Line'>, key: string) {
    const levels = key === 'rsi14' ? [30, 70]
      : key === 'kdjK' ? [20, 80] : key === 'macd' ? [0] : [];
    levels.forEach((price) => line.createPriceLine({ price,
      color: readTheme().text, lineStyle: LineStyle.Dashed,
      lineWidth: 1, axisLabelVisible: false }));
  }

  private setSeriesData(pane: Pane, key: string, series: Series) {
    if (!this.dataset) return;
    const theme = readTheme();
    const values = this.values(key);
    const points = this.dataset.bars.map((bar, index) => {
      const time = bar.ts as UTCTimestamp;
      if (key === 'candlestick') return { time,
        open: bar.o, high: bar.h, low: bar.l, close: bar.c };
      const value = values[index];
      if (value === null || value === undefined) return { time };
      const isUp = key === 'volume' ? bar.c >= bar.o : value >= 0;
      const isHistogram = key === 'volume' || key === 'macdHistogram';
      return { time, value,
        color: isHistogram ? (isUp ? theme.up : theme.down) : undefined };
    });
    series.setData(points);
    const numeric = values.filter((v): v is number => v !== null);
    const priceKeys = ['ma5', 'ma10', 'ma20', 'ma60', 'ema12', 'ema26',
      'bollUpper', 'bollMiddle', 'bollLower'];
    const priceValues = pane.name === 'main'
      ? [...this.dataset.bars.flatMap((b) => [b.o, b.h, b.l, b.c]),
        ...priceKeys.flatMap((field) => this.values(field))] : numeric;
    const oscillatorValues = this.settings.oscillator === 'macd'
      ? OSCILLATORS.macd!.flatMap(field => this.values(field)) : priceValues;
    const precision = pane.name === 'oscillator'
      && ['rsi', 'kdj'].includes(this.settings.oscillator)
      ? 2 : pricePrecision(pane.name === 'oscillator'
        ? oscillatorValues : priceValues);
    const isVolume = pane.name === 'volume';
    series.applyOptions({ priceFormat: isVolume ? {
      type: 'custom', minMove: 1,
      formatter: (value: number) => formatVolume(value, this.dataset!.chart),
    }
      : { type: 'price', precision, minMove: 10 ** -precision } });
  }

  private renderData() {
    if (!this.dataset) return;
    for (const pane of this.panes.values()) {
      const keys = this.keysFor(pane);
      for (const [key, series] of pane.series) {
        if (!keys.includes(key)) {
          pane.chart.removeSeries(series); pane.series.delete(key);
        }
      }
      for (const key of keys) {
        if (!pane.series.has(key)) {
          pane.series.set(key, this.addSeries(pane, key));
        }
        this.setSeriesData(pane, key, pane.series.get(key)!);
      }
      pane.carrier.setData(this.dataset.bars.map((bar) => ({
        time: bar.ts as UTCTimestamp, value: 0,
      })));
    }
    this.applyScale(); this.scheduleLayout();
  }

  setDataset(dataset: ChartDataset) {
    if (this.dataset === dataset) return;
    const previous = this.dataset;
    const runtime = previous ? this.snapshotRuntime() : null;
    this.dataset = dataset;
    this.renderData();
    if (runtime) this.restoreRuntime(runtime);
    else this.fit();
    this.syncAllCrosshairs();
    this.publishSelection(this.selection.index, this.selection.origin, true);
  }

  applySettings(settings: ChartPreferences) {
    if (this.destroyed) return;
    const viewport = this.viewport;
    this.settings = settings; this.reconcilePanes(); this.renderData();
    if (viewport) this.setViewport(viewport);
    this.syncAllCrosshairs();
  }

  private applyScale() {
    this.panes.get('main')?.chart.priceScale('right').applyOptions({
      mode: this.settings.scale === 'log'
        ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    });
  }

  private queueRange(range: LogicalRange | null) {
    if (!range || this.syncing || this.destroyed
      || rangesEqual(this.viewport, range)) return;
    this.pendingRange = { from: range.from, to: range.to };
    if (this.rangeFrame) return;
    this.rangeFrame = requestAnimationFrame(() => {
      this.rangeFrame = 0;
      if (this.pendingRange) this.setViewport(this.pendingRange);
      this.pendingRange = null;
    });
  }

  setViewport(range: Range) {
    if (this.destroyed) return;
    if (this.rangeFrame) cancelAnimationFrame(this.rangeFrame);
    this.rangeFrame = 0; this.pendingRange = null;
    this.viewport = range; this.syncing = true;
    try {
      for (const pane of this.panes.values()) {
        const current = pane.chart.timeScale().getVisibleLogicalRange();
        if (!rangesEqual(current, range)) {
          pane.chart.timeScale().setVisibleLogicalRange(range);
        }
      }
    } finally { this.syncing = false; }
    this.options.onViewport?.(range);
    this.drawGuides();
    this.scheduleLayout();
  }

  fit() {
    const n = this.dataset?.bars.length ?? 0;
    if (n) this.setViewport({ from: -.5, to: n - 1 + 3 });
    this.panes.get('main')?.chart.priceScale('right')
      .applyOptions({ autoScale: true });
  }

  zoom(factor: number) {
    const n = this.dataset?.bars.length ?? 0;
    if (!n || !this.viewport) return;
    const middle = (this.viewport.from + this.viewport.to) / 2;
    const width = Math.max(Math.min(10, n), Math.min(Math.max(n, 10) + 6,
      (this.viewport.to - this.viewport.from) * factor));
    this.setViewport({ from: middle - width / 2, to: middle + width / 2 });
  }

  latest() {
    const n = this.dataset?.bars.length ?? 0;
    if (!n) return;
    const width = this.viewport ? this.viewport.to - this.viewport.from : n;
    this.setViewport({ from: n + 2 - width, to: n + 2 });
    this.setSelection(null, 'latest');
  }

  locate(index: number) {
    const n = this.dataset?.bars.length ?? 0;
    if (n <= 40) {
      this.fit(); this.setSelection(index, 'keyboard'); return;
    }
    const width = Math.min(40, n);
    this.setViewport({ from: index - width / 2, to: index + width / 2 });
    this.setSelection(index, 'keyboard');
  }

  private onCrosshair(pane: Pane, event: MouseEventParams<Time>) {
    if (this.destroyed || this.syncing || !event.sourceEvent
      || this.activePane !== pane.name
      || this.inputType === 'touch') return;
    const index = event.time === undefined ? null
      : this.dataset?.indexByTimestamp.get(Number(event.time)) ?? null;
    if (index === null && this.selection.origin !== 'pointer'
      && this.selection.origin !== 'latest') return;
    this.pendingSelection = {
      index, origin: index === null ? 'latest' : 'pointer',
    };
    this.pendingClearPane = index === null ? pane.name : null;
    if (this.selectFrame) return;
    this.selectFrame = requestAnimationFrame(() => {
      this.selectFrame = 0;
      const selection = this.pendingSelection;
      this.pendingSelection = null;
      if (this.pendingClearPane
        && this.pendingClearPane !== this.activePane) return;
      if (selection) this.setSelection(selection.index, selection.origin);
    });
  }

  setSelection(index: number | null, origin: ChartSelection['origin']) {
    if (this.destroyed) return;
    if (index !== null && !this.dataset?.bars[index]) return;
    if (this.selection.index === index
      && this.selection.origin === origin) return;
    if (index !== null && origin === 'keyboard') this.ensureVisible(index);
    this.publishSelection(index, origin);
    this.syncAllCrosshairs();
  }

  private syncAllCrosshairs() {
    this.syncing = true;
    try { for (const pane of this.panes.values()) this.syncCrosshair(pane); }
    finally { this.syncing = false; }
    this.drawGuides();
  }

  private ensureVisible(index: number) {
    if (!this.viewport || index >= this.viewport.from
      && index <= this.viewport.to) return;
    const width = this.viewport.to - this.viewport.from;
    const from = Math.max(-.5, index - width / 2);
    this.setViewport({ from, to: from + width });
  }

  private publishSelection(index: number | null,
    origin: ChartSelection['origin'], force = false) {
    if (!force && this.selection.index === index
      && this.selection.origin === origin) return;
    this.selection = { index, origin };
    this.options.onSelection(this.selection);
  }

  private syncCrosshair(pane: Pane) {
    const index = this.selection.index;
    const bar = index === null ? null : this.dataset?.bars[index];
    if (!bar || index === null) { pane.chart.clearCrosshairPosition(); return; }
    const key = this.keysFor(pane).find((field) =>
      this.valueAt(field, index) != null);
    const series = key ? pane.series.get(key) : null;
    if (!series || !key) { pane.chart.clearCrosshairPosition(); return; }
    pane.chart.setCrosshairPosition(this.valueAt(key, index)!,
      bar.ts as UTCTimestamp, series);
  }

  private drawGuides() {
    const bar = this.selection.index === null ? null
      : this.dataset?.bars[this.selection.index];
    for (const pane of this.panes.values()) {
      const x = bar ? pane.chart.timeScale()
        .timeToCoordinate(bar.ts as UTCTimestamp) : null;
      const width = pane.container.clientWidth - this.axisWidth;
      pane.guide.hidden = x === null || x < 0 || x > width;
      if (x !== null) pane.guide.style.left = `${x}px`;
    }
  }

  releasePointer() {
    this.activePane = null;
    if (this.selectFrame) cancelAnimationFrame(this.selectFrame);
    this.selectFrame = 0; this.pendingSelection = null;
    if (this.selection.origin === 'pointer') this.setSelection(null, 'latest');
  }

  coordinateToIndex(target: HTMLElement, clientX: number,
    clamp = false, clientY?: number) {
    const pane = [...this.panes.values()]
      .find(p => p.container.contains(target));
    if (!pane || !this.dataset?.bars.length) return null;
    const rect = pane.container.getBoundingClientRect();
    const rawX = clientX - rect.left;
    if (clientY !== undefined && !clamp) {
      const y = clientY - rect.top;
      const height = pane.container.clientHeight
        - pane.chart.timeScale().height();
      if (y < 0 || y >= height) return null;
    }
    const width = pane.container.clientWidth - this.axisWidth;
    if (!clamp && (rawX < 0 || rawX > width)) return null;
    const x = Math.max(0, Math.min(width, rawX));
    const logical = pane.chart.timeScale().coordinateToLogical(x);
    if (logical === null) return null;
    if (!clamp && (logical < -.5
      || logical > this.dataset.bars.length - .5)) return null;
    return Math.max(0,
      Math.min(this.dataset.bars.length - 1, Math.round(logical)));
  }

  setTool(tool: ChartTool) { this.tool = tool; }

  setGesturing(active: boolean) {
    for (const pane of this.panes.values()) pane.chart.applyOptions({
      handleScroll: { pressedMouseMove: !active,
        horzTouchDrag: !active, vertTouchDrag: false, mouseWheel: !active },
      handleScale: { mouseWheel: !active, pinch: !active,
        axisPressedMouseMove: !active },
    });
  }

  snapshotRuntime(): ChartRuntime {
    const bars = this.dataset?.bars ?? [];
    const limit = (index: number) => Math.max(0,
      Math.min(bars.length - 1, index));
    const left = limit(Math.floor(this.viewport?.from ?? 0));
    const right = limit(Math.ceil(this.viewport?.to ?? 0));
    return { datasetVersion: this.dataset?.datasetVersion,
      viewport: this.viewport, selection: this.selection,
      tool: this.tool,
      selectionTs: this.selection.index === null ? undefined
        : bars[this.selection.index]?.ts,
      leftTs: bars[left]?.ts, rightTs: bars[right]?.ts,
      leftOffset: (this.viewport?.from ?? 0) - left,
      rightOffset: (this.viewport?.to ?? 0) - right,
      followsLatest: (this.viewport?.to ?? 0) >= bars.length - 1 };
  }

  restoreRuntime(runtime: ChartRuntime) {
    if (!this.dataset) return;
    this.tool = runtime.tool;
    const index = runtime.selectionTs === undefined ? null
      : this.dataset.indexByTimestamp.get(runtime.selectionTs) ?? null;
    this.setSelection(index,
      index === null ? 'latest' : runtime.selection.origin);
    if (!runtime.viewport) return;
    if (runtime.datasetVersion === this.dataset.datasetVersion) {
      this.setViewport(runtime.viewport); return;
    }
    const width = runtime.viewport.to - runtime.viewport.from;
    if (runtime.followsLatest) {
      const to = this.dataset.bars.length + 2;
      this.setViewport({ from: to - width, to }); return;
    }
    const left = this.anchorIndex(runtime.leftTs);
    const right = this.anchorIndex(runtime.rightTs);
    this.setViewport({ from: left + (runtime.leftOffset ?? 0),
      to: Math.max(left + 1, right + (runtime.rightOffset ?? 0)) });
  }

  private anchorIndex(ts: number | undefined) {
    const bars = this.dataset!.bars;
    if (ts === undefined) return 0;
    let low = 0, high = bars.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (bars[middle]!.ts < ts) low = middle + 1; else high = middle;
    }
    const index = Math.min(low, Math.max(0, bars.length - 1));
    if (bars[index]?.ts !== ts) this.options.onMessage?.('视区边界已按新数据调整');
    return index;
  }

  capturePanes() {
    if (this.destroyed) throw new Error('图表已关闭，请重新导出');
    return [...this.panes.values()].map((pane) => ({
      canvas: pane.chart.takeScreenshot(),
      width: pane.container.clientWidth, height: pane.container.clientHeight,
      label: pane.name === 'main' ? '价格'
        : pane.name === 'volume' ? 'VOL · MAVOL5 · MAVOL10'
          : this.keysFor(pane).join(' · ').toUpperCase(),
    }));
  }

  private resize() {
    if (this.destroyed) return;
    for (const pane of this.panes.values()) pane.chart.applyOptions({
      width: Math.max(1, pane.container.clientWidth),
      height: Math.max(pane.name === 'main' ? 220 : 110,
        pane.container.clientHeight),
    });
    this.scheduleLayout();
  }

  private scheduleLayout() {
    if (this.layoutFrame || this.destroyed) return;
    this.layoutFrame = requestAnimationFrame(() => {
      this.layoutFrame = 0;
      const width = Math.max(72, ...[...this.panes.values()]
        .map((pane) => pane.chart.priceScale('right').width()));
      if (Math.abs(width - this.axisWidth) > 1) {
        this.axisWidth = width;
        for (const pane of this.panes.values()) pane.chart
          .priceScale('right').applyOptions({ minimumWidth: width });
        this.scheduleLayout();
      }
      this.syncAllCrosshairs();
    });
  }

  refreshTheme() {
    const theme = readTheme();
    for (const pane of this.panes.values()) pane.chart.applyOptions({
      layout: { textColor: theme.text },
      grid: { vertLines: { color: theme.grid },
        horzLines: { color: theme.grid } },
      rightPriceScale: { borderColor: theme.border },
      timeScale: { borderColor: theme.border },
    });
    this.panes.get('main')?.series.get('candlestick')?.applyOptions({
      upColor: theme.up, downColor: theme.down,
      wickUpColor: theme.up, wickDownColor: theme.down,
    });
    this.renderData();
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    [this.rangeFrame, this.selectFrame, this.layoutFrame]
      .forEach((frame) => cancelAnimationFrame(frame));
    this.observer.disconnect();
    [...this.panes.values()].forEach((pane) => this.removePane(pane));
  }
}
