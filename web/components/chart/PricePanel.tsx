'use client';

import dynamic from 'next/dynamic';
import { createPortal } from 'react-dom';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { deltaArrow, epochToDate } from '@/lib/format';
import { chartBarsQuery } from '@/lib/chartQuery';
import {
  buildChartDataset, findDateIndex, calculateMeasurement, getLatestChartChange,
  type ChartDataset, type ChartSelection, type ChartTool, type Measurement,
} from '@/lib/chartDataset';
import {
  DEFAULT_CHART_PREFERENCES, loadChartPreferences, saveChartPreferences,
  type ChartPreferences,
} from '@/lib/chartPreferences';
import {
  formatPrice, formatPercent, formatVolume, formatAmount, volumeUnitLabel,
} from '@/lib/chartFormat';
import { useUiStore } from '@/lib/store';
import { isEditableTarget } from '@/lib/keyboard';
import type { BarsResponse, ChartView } from '@/lib/types';
import type { CandleChartHandle } from './CandleChart';
import type { ChartRuntime } from './ChartController';
import { ChartToolbar } from './ChartToolbar';
import { ChartReadout, getDatasetPricePrecision } from './ChartReadout';
import { Skeleton } from '../ui/Skeleton';
import { Badge, Tag } from '../ui/Badge';

const CandleChart = dynamic(() => import('./CandleChart')
  .then(module => module.CandleChart), {
  ssr: false, loading: () => <Skeleton className="m-3 min-h-[220px]" />,
});
const ChartSettings = dynamic(() => import('./ChartSettings')
  .then(module => module.ChartSettings), { ssr: false });
const ChartDataTable = dynamic(() => import('./ChartDataTable')
  .then(module => module.ChartDataTable), { ssr: false });

export interface PricePanelProps {
  instrumentId: string;
  symbol: string;
  name: string;
  currency: string;
  timezone?: string;
  lastPrice?: number | null;
  changePct?: number | null;
  quoteSource?: string | null;
  onCloseDrawer?: () => void;
  brushRange?: { from: string; to: string } | null;
  onBrushRange?: (range: { from: string; to: string } | null) => void;
  chartView?: ChartView;
  onChartViewChange?: (next: ChartView) => void;
  rangeQueryEnabled?: boolean;
  /** undefined renders inline; null keeps the query owner without a canvas. */
  portalTarget?: HTMLElement | null;
  expanded?: boolean;
  onExpandedChange?: (open: boolean) => void;
  escapeRef?: { current: ((event: KeyboardEvent) => boolean) | null };
}

const LATEST: ChartSelection = { index: null, origin: 'latest' };

function usePanelState(props: PricePanelProps) {
  const [localView, setLocalView] = useState<ChartView>({
    timeframe: '1d', range: '1y', adjust: 'qfq',
  });
  const view = props.chartView ?? localView;
  const key = [props.instrumentId, view.timeframe, view.range, view.adjust]
    .join('/');
  const chart = useRef<CandleChartHandle>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tableOpen, setTableOpen] = useState(false);
  const [selected, setSelected] = useState({ key, value: LATEST });
  const selection = selected.key === key ? selected.value : LATEST;
  const [tool, setTool] = useState<ChartTool>('pan');
  const [measurement, setMeasurement] = useState<Measurement | null>(null);
  const [message, setMessage] = useState('');
  const [ready, setReady] = useState(0);
  const [measureFrom, setMeasureFrom] = useState('');
  const [measureTo, setMeasureTo] = useState('');
  const theme = useUiStore(state => state.resolvedTheme);
  const onSelection = useCallback((value: ChartSelection) => {
    setSelected(current => current.key === key
      && current.value.index === value.index
      && current.value.origin === value.origin ? current : { key, value });
  }, [key]);
  return { props, view, key, chart, settingsOpen, setSettingsOpen,
    tableOpen, setTableOpen, selection, tool, setTool, measurement,
    setMeasurement, message, setMessage, ready, setReady,
    measureFrom, setMeasureFrom, measureTo, setMeasureTo,
    theme, onSelection, setLocalView };
}
type PanelState = ReturnType<typeof usePanelState>;

function usePanelDataset(state: PanelState) {
  const bridge = useMemo(() => ({ key: state.key, revision: 0,
    lastResponse: null as BarsResponse | null,
    runtime: { current: null as ChartRuntime | null },
  }), [state.key]);
  const barsQuery = useQuery(chartBarsQuery(
    state.props.instrumentId, state.view));
  const response = barsQuery.data;
  const dataset = useMemo(() => {
    if (!response?.chart) return null;
    if (bridge.lastResponse !== response) {
      bridge.lastResponse = response; bridge.revision += 1;
    }
    return buildChartDataset(response, bridge.revision);
  }, [bridge, response]);
  const datasetRef = useRef(dataset);
  datasetRef.current = dataset;
  return { bridge, barsQuery, response, dataset, datasetRef };
}
type DatasetState = ReturnType<typeof usePanelDataset>;

function hasPositivePrices(dataset: ChartDataset | null,
  preferences: ChartPreferences) {
  if (!dataset) return true;
  const overlays = (['ma5', 'ma10', 'ma20', 'ma60'] as const)
    .filter(k => preferences.main[k]);
  const values = dataset.bars.flatMap(b => [b.o, b.h, b.l, b.c]);
  overlays.forEach(k => values.push(...dataset.indicators[k]
    .filter((v): v is number => v !== null)));
  if (preferences.main.ema) ['ema12', 'ema26'].forEach(k =>
    values.push(...dataset.indicators[k as 'ema12']
      .filter((v): v is number => v !== null)));
  if (preferences.main.boll) ['bollUpper', 'bollMiddle', 'bollLower']
    .forEach(k => values.push(...dataset.indicators[k as 'bollUpper']
      .filter((v): v is number => v !== null)));
  return values.every(value => value > 0);
}

function usePanelPreferences(state: PanelState, data: DatasetState) {
  const [preferences, setPreferences] = useState(DEFAULT_CHART_PREFERENCES);
  useEffect(() => { setPreferences(loadChartPreferences()); }, []);
  const changePreferences = useCallback((next: ChartPreferences) => {
    setPreferences(next); saveChartPreferences(next);
  }, []);
  const canUseLog = useMemo(() => hasPositivePrices(data.dataset, preferences),
    [data.dataset, preferences]);
  const setMessage = state.setMessage;
  const invalidLog = useCallback(() => {
    setMessage('当前价格包含非正数，无法使用对数坐标');
  }, [setMessage]);
  useEffect(() => {
    if (!canUseLog && preferences.scale === 'log') {
      changePreferences({ ...preferences, scale: 'linear' }); invalidLog();
    }
  }, [canUseLog, changePreferences, invalidLog, preferences]);
  const effectivePreferences = !canUseLog && preferences.scale === 'log'
    ? { ...preferences, scale: 'linear' as const } : preferences;
  return { preferences, effectivePreferences, canUseLog,
    changePreferences, invalidLog };
}
type PreferenceState = ReturnType<typeof usePanelPreferences>;

function usePanelLifecycle(state: PanelState, data: DatasetState) {
  const { key, chart, props, setTool, setMeasurement, setMessage,
    setMeasureFrom, setMeasureTo, measurement } = state;
  const lastDataset = useRef(data.dataset);
  useEffect(() => {
    setTool('pan'); setMeasurement(null); setMessage('');
    setMeasureFrom(''); setMeasureTo('');
  }, [key, setTool, setMeasurement, setMessage, setMeasureFrom, setMeasureTo]);
  useEffect(() => {
    if (lastDataset.current && data.dataset !== lastDataset.current
      && measurement) {
      setMeasurement(null); setMessage('数据已更新，请重新测量');
    }
    lastDataset.current = data.dataset;
  }, [data.dataset, measurement, setMeasurement, setMessage]);
  useEffect(() => {
    if (props.escapeRef) props.escapeRef.current = event =>
      chart.current?.consumeEscape(event) ?? false;
    return () => { if (props.escapeRef) props.escapeRef.current = null; };
  }, [chart, props.escapeRef]);
}

function useReportFocus(state: PanelState, data: DatasetState) {
  const chartFocus = useUiStore(store => store.chartFocus);
  const clearChartFocus = useUiStore(store => store.clearChartFocus);
  const { chart, ready, props, setMessage } = state;
  const { dataset } = data;
  useEffect(() => {
    if (!chartFocus || !dataset || props.portalTarget === null) return;
    const first = dataset.stats.findIndex(stat =>
      stat.periodEnd >= chartFocus.from && stat.periodStart <= chartFocus.to);
    if (first < 0) setMessage('日期不在已加载区间');
    else {
      if (!chart.current?.controller) return;
      let last = first;
      while (last + 1 < dataset.stats.length
        && dataset.stats[last + 1]!.periodStart <= chartFocus.to) last += 1;
      const width = Math.max(Math.min(10, dataset.bars.length),
        last - first + 7);
      const center = (first + last) / 2;
      chart.current.controller.setViewport({
        from: center - width / 2, to: center + width / 2,
      });
    }
    clearChartFocus();
  }, [chartFocus, clearChartFocus, dataset, ready, props.portalTarget,
    chart, setMessage]);
}

function getVisibleDates(data: ChartDataset, runtime: ChartRuntime | null) {
  const limit = (index: number) => Math.max(0,
    Math.min(data.stats.length - 1, index));
  const start = limit(Math.floor(runtime?.viewport?.from ?? 0));
  const end = limit(Math.ceil(runtime?.viewport?.to ?? data.stats.length - 1));
  return `${data.stats[start]?.periodStart} ~ ${data.stats[end]?.periodEnd}`;
}

function pngCapture(state: PanelState, dataset: ChartDataset,
  handle: CandleChartHandle, runtime: ChartRuntime | null) {
  const { props, view } = state;
  const css = getComputedStyle(document.documentElement);
  return { panes: handle.capturePanes(),
    title: `${props.name} ${props.symbol} · ${props.currency}`,
    subtitle: `${getVisibleDates(dataset, runtime)} · ${view.timeframe} `
      + `请求${view.adjust}/实际${dataset.chart.effectiveAdjust} · `
      + `${volumeUnitLabel(dataset.chart.volumeUnit)} · 当前图形与图例`,
    source: dataset.meta.source,
    watermark: [dataset.meta.source === 'synthetic' ? '合成行情 · 仅供演示' : '',
      dataset.meta.cached ? '缓存' : '', dataset.meta.stale ? '数据陈旧' : '',
      ...dataset.meta.warnings].filter(Boolean).join(' · '),
    background: css.getPropertyValue('--bg-canvas').trim(),
    textColor: css.getPropertyValue('--text-primary').trim(),
  };
}

function usePanelExports(state: PanelState, data: DatasetState,
  settings: PreferenceState) {
  const [exporting, setExporting] = useState(false);
  const identity = useRef({
    preferences: settings.preferences, theme: state.theme,
  });
  identity.current = { preferences: settings.preferences, theme: state.theme };
  const download = async (format: 'csv' | 'png') => {
    const { dataset, datasetRef } = data;
    if (!dataset || exporting) return;
    const handle = state.chart.current;
    const runtime = handle?.getRuntime() ?? null;
    const frozenIdentity = identity.current;
    const viewport = JSON.stringify(runtime?.viewport);
    setExporting(true);
    try {
      const exports = await import('@/lib/chartExport');
      if (format === 'csv') exports.downloadChartCsv(dataset);
      else await exports.downloadChartPng({
        filename: exports.chartExportFilename(state.props.symbol,
          dataset.meta, 'png'),
        isCurrent: () => datasetRef.current === dataset
          && state.chart.current === handle && Boolean(handle?.controller)
          && JSON.stringify(state.chart.current?.getRuntime()?.viewport)
            === viewport
          && identity.current.preferences === frozenIdentity.preferences
          && identity.current.theme === frozenIdentity.theme,
        capture: () => pngCapture(state, dataset, handle!, runtime),
      });
      state.setMessage(`${format.toUpperCase()} 已导出`);
    } catch (error) {
      state.setMessage(error instanceof Error ? error.message : '导出失败，请重试');
    } finally { setExporting(false); }
  };
  return { exporting, download };
}

function useWorkbench(props: PricePanelProps) {
  const state = usePanelState(props);
  const data = usePanelDataset(state);
  const preferences = usePanelPreferences(state, data);
  const exports = usePanelExports(state, data, preferences);
  usePanelLifecycle(state, data);
  useReportFocus(state, data);
  const latest = data.response?.bars.at(-1);
  const latestChange = latest
    ? getLatestChartChange(data.response)
    : props.lastPrice != null ? props.changePct ?? null : null;
  const lastPrice = latest ? latest.c : props.lastPrice ?? null;
  const date = latest ? data.response?.chart?.stats.at(-1)?.periodEnd
    ?? epochToDate(latest.ts, props.timezone) : null;
  return { ...state, ...data, ...preferences, ...exports,
    latest, latestChange, lastPrice, date,
    enabled: Boolean(data.dataset?.bars.length) };
}
type Workbench = ReturnType<typeof useWorkbench>;

function locateDate(state: Workbench, date: string) {
  const { dataset } = state;
  if (!dataset) return;
  const index = findDateIndex(dataset, date);
  if (index < 0) return;
  state.chart.current?.controller?.locate(index);
  const stat = dataset.stats[index]!;
  const outside = date < dataset.stats[0]!.periodStart
    || date > dataset.stats.at(-1)!.periodEnd;
  state.setMessage(outside ? `超出已加载区间，已定位至 ${stat.periodEnd}`
    : date < stat.periodStart ? `休市日，已定位至 ${stat.periodEnd}` : '');
}

function PanelHeader({ state }: { state: Workbench }) {
  const { props, latestChange, lastPrice, latest, dataset } = state;
  return <header className="chart-header">
    <div><strong>{props.name}</strong> <span className="mono">
      {props.symbol}</span></div>
    <div className="chart-latest-price">
      <strong>{formatPrice(lastPrice,
        dataset ? getDatasetPricePrecision(dataset) : undefined)}</strong>
      <span className={cn((latestChange ?? 0) > 0 ? 'chart-positive'
        : (latestChange ?? 0) < 0 ? 'chart-negative' : '')}>
        {deltaArrow(latestChange, latestChange === 0 ? 2 : 1)}
      </span> <span>{props.currency}</span>
      {!latest && props.lastPrice != null ? <Badge>独立报价</Badge> : null}
    </div>
    {props.onCloseDrawer ? <button type="button" className="chart-close-drawer"
      onClick={props.onCloseDrawer}
      aria-label={copy.chart.closeDrawer}>×</button>
      : null}
  </header>;
}

function PanelToolbar({ state: s }: { state: Workbench }) {
  return <ChartToolbar view={s.view} onViewChange={next => {
    s.setLocalView(next); s.props.onChartViewChange?.(next);
  }} tool={s.tool} onToolChange={s.setTool} disabled={!s.enabled}
    rangeQueryEnabled={s.props.rangeQueryEnabled}
    onZoomIn={() => s.chart.current?.controller?.zoom(.8)}
    onZoomOut={() => s.chart.current?.controller?.zoom(1.25)}
    onFit={() => s.chart.current?.controller?.fit()}
    onLatest={() => s.chart.current?.controller?.latest()}
    onLocate={date => locateDate(s, date)}
    onSettings={() => s.setSettingsOpen(true)}
    onExpand={() => s.props.onExpandedChange?.(!s.props.expanded)}
    onToggleTable={() => s.setTableOpen(open => !open)}
    tableOpen={s.tableOpen} expanded={s.props.expanded}
    exporting={s.exporting} onCsv={() => void s.download('csv')}
    onPng={() => void s.download('png')}
    onRefresh={() => void s.barsQuery.refetch()}
    isRefreshing={s.barsQuery.isFetching} />;
}

function PanelNotices({ state: s }: { state: Workbench }) {
  return <>
    {s.barsQuery.isError ? <div role="status" className="chart-notice">
      {s.response ? '刷新失败，保留旧数据' : copy.chart.error}：
      {s.barsQuery.error instanceof Error ? s.barsQuery.error.message : ''}
      <button type="button" onClick={() => void s.barsQuery.refetch()}>
        {copy.chart.retry}</button>
    </div> : null}
    {s.message ? <p role="status" className="chart-notice">
      {s.message}</p> : null}
  </>;
}

function PanelCharts({ state: s }: { state: Workbench }) {
  return <>
    {s.dataset && s.enabled ? <ChartReadout dataset={s.dataset}
      preferences={s.effectivePreferences} selection={s.selection}
      onLatest={() => s.chart.current?.controller
        ?.setSelection(null, 'latest')} />
      : null}
    {s.latest ? <CandleChart ref={s.chart} key={s.key} bars={s.response!.bars}
      indicators={s.response!.indicators} dataset={s.dataset}
      preferences={s.effectivePreferences} selection={s.selection} tool={s.tool}
      onSelection={s.onSelection} onToolChange={s.setTool}
      onMeasurement={s.setMeasurement} onBrushRange={s.props.onBrushRange}
      rangeQueryEnabled={s.props.rangeQueryEnabled} onMessage={s.setMessage}
      runtimeRef={s.bridge.runtime}
      onReady={() => s.setReady(value => value + 1)} />
      : s.response ? <p className="chart-empty">{copy.chart.empty}</p> : null}
  </>;
}

function PanelMeasurement({ state: s }: { state: Workbench }) {
  const { measurement: m, dataset } = s;
  return <>
    {s.enabled && s.tool === 'measure' ? <form className="chart-measure-form"
      onSubmit={event => {
        event.preventDefault();
        if (dataset && s.measureFrom && s.measureTo) s.setMeasurement(
          calculateMeasurement(dataset, findDateIndex(dataset, s.measureFrom),
            findDateIndex(dataset, s.measureTo)));
      }}>
      <label>测量起始日期<input type="date" value={s.measureFrom}
        onChange={event => s.setMeasureFrom(event.target.value)}
        required /></label>
      <label>测量终止日期<input type="date" value={s.measureTo}
        onChange={event => s.setMeasureTo(event.target.value)}
        required /></label>
      <button type="submit" className="chart-tool-button">计算测量</button>
    </form> : null}
    {m && dataset ? <div className="chart-measurement" role="status">
      <strong>首末收盘收益 {formatPercent(m.returnPct)}</strong>
      <span>{m.from} ~ {m.to} · {m.count} 根</span>
      <span>最高 {formatPrice(m.high)} · 最低 {formatPrice(m.low)}</span>
      <span>累计量 {formatVolume(m.volume, dataset.chart)} ·
        累计额 {formatAmount(m.amount, s.props.currency)}</span>
      {m.returnPct === null ? <span>缺少有效起点价格</span> : null}
      <button type="button" onClick={() => s.setMeasurement(null)}>清除测量</button>
    </div> : null}
  </>;
}

function PanelBody({ state: s }: { state: Workbench }) {
  return <div className="chart-scroll-body">
    {s.barsQuery.isLoading ? <Skeleton className="m-3 h-80" /> : null}
    {s.response && !s.response.chart ? <p className="chart-notice">
      当前服务未支持完整图表数据，正在使用简化模式</p> : null}
    <PanelCharts state={s} /><PanelMeasurement state={s} />
    {s.tableOpen && s.dataset ? <ChartDataTable dataset={s.dataset}
      preferences={s.effectivePreferences} selection={s.selection}
      onSelect={index => s.chart.current?.controller?.setSelection(index,
        'keyboard')} /> : null}
  </div>;
}

function PanelFooter({ state: s }: { state: Workbench }) {
  const { response, dataset, view, date } = s;
  const displayedSource = s.latest
    ? response?.meta.source : s.props.lastPrice != null
      ? s.props.quoteSource : null;
  return <footer className="chart-footer">
    <span>{copy.chart.dataSource}：{displayedSource ?? '—'}</span>
    <span>{response?.meta.barsCount ?? 0} 根</span>
    {response?.meta.cached ? <Badge>{copy.chart.cached}</Badge> : null}
    {displayedSource === 'synthetic' ? <Badge tone="warn">
      {copy.chart.synthetic}</Badge> : null}
    {response?.meta.stale ? <Badge tone="warn">数据陈旧</Badge> : null}
    {response?.meta.reasonCode === 'divergent_source' ? <Badge tone="warn">
      {copy.chart.sourceDivergent}</Badge> : null}
    {date ? <Tag>{date}</Tag> : null}
    {dataset ? <span>请求{view.adjust} / 实际{dataset.chart.effectiveAdjust}</span>
      : null}
    {dataset?.chart.effectiveAdjust === 'auto' && view.adjust === 'hfq'
      ? <span>请求后复权；数据源返回自动复权，非可验证后复权</span> : null}
    {dataset?.chart.historyLimited ? <span>预热历史不足十年</span> : null}
    {view.timeframe !== '1d' ? <span>末周期可能未结束</span> : null}
    {response?.meta.warnings.map((warning, i) => <span key={i}>
      {warning}</span>)}
  </footer>;
}

/** Portal moves keep the owner, query, preferences and runtime snapshots. */
export function PricePanel(props: PricePanelProps) {
  const state = useWorkbench(props);
  const content = <section aria-label="K 线面板" className="chart-workbench"
    data-print="hide" tabIndex={-1} onKeyDown={event => {
      if (event.key === 'Escape' && isEditableTarget(event.target)) {
        event.stopPropagation();
      }
    }}>
    <PanelHeader state={state} /><PanelToolbar state={state} />
    <PanelNotices state={state} /><PanelBody state={state} />
    {props.brushRange ? <div className="chart-notice">报告区间：
      {props.brushRange.from} ~ {props.brushRange.to}
      <button type="button" onClick={() => props.onBrushRange?.(null)}>
        清除报告区间</button>
    </div> : null}
    <PanelFooter state={state} />
    {state.settingsOpen ? <ChartSettings open={state.settingsOpen}
      onOpenChange={state.setSettingsOpen} preferences={state.preferences}
      onChange={state.changePreferences} canUseLog={state.canUseLog}
      onInvalidLog={state.invalidLog} /> : null}
  </section>;
  if (props.portalTarget === null) return null;
  return props.portalTarget
    ? createPortal(content, props.portalTarget) : content;
}
