'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Settings2 } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { deltaArrow, epochToDate, num } from '@/lib/format';
import { api } from '@/lib/api';
import { useQuery } from '@tanstack/react-query';
import type { BarsResponse } from '@/lib/types';

import { CandleChart } from './CandleChart';
import {
  DEFAULT_INDICATORS,
  IndicatorToggles,
  loadIndicators,
  saveIndicators,
  type IndicatorState,
} from './IndicatorToggles';
import { Segmented } from '../ui/Tabs';
import { Skeleton } from '../ui/Skeleton';
import { Badge, Tag } from '../ui/Badge';
import { IconButton } from '../ui/Button';

const TIMEFRAMES = [
  { value: '1d', label: '日' },
  { value: '1w', label: '周' },
  { value: '1m', label: '月' },
] as const;

const RANGES = [
  { value: '1m', label: '1月' },
  { value: '3m', label: '3月' },
  { value: '6m', label: '6月' },
  { value: '1y', label: '1年' },
  { value: '3y', label: '3年' },
] as const;

const ADJUSTS = [
  { value: 'qfq', label: '前复权' },
  { value: 'none', label: '不复权' },
  { value: 'hfq', label: '后复权' },
] as const;

type TimeframeValue = (typeof TIMEFRAMES)[number]['value'];
type RangeValue = (typeof RANGES)[number]['value'];
type AdjustValue = (typeof ADJUSTS)[number]['value'];

export interface PricePanelProps {
  instrumentId: string;
  symbol: string;
  name: string;
  currency: string;
  timezone?: string;
  lastPrice?: number | null;
  changePct?: number | null;
  onCloseDrawer?: () => void;
  /** ★v6 G2a：当前框选的区间（来自 URL），用于提示条与再框一次替换的语义。 */
  brushRange?: { from: string; to: string } | null;
  /** 框选完成 → 上行给 `WorkspaceClient`（它写 URL，`ReportPanel` 消费）。 */
  onBrushRange?: (range: { from: string; to: string } | null) => void;
}

export function PricePanel({
  instrumentId,
  symbol,
  name,
  currency,
  timezone = 'Asia/Shanghai',
  lastPrice,
  changePct,
  onCloseDrawer,
  brushRange = null,
  onBrushRange,
}: PricePanelProps) {
  const [timeframe, setTimeframe] = useState<TimeframeValue>('1d');
  const [range, setRange] = useState<RangeValue>('1y');
  const [adjust, setAdjust] = useState<AdjustValue>('qfq');
  const [showSettings, setShowSettings] = useState(false);
  const [indicatorState, setIndicatorState] = useState<IndicatorState>(DEFAULT_INDICATORS);

  // localStorage is read after mount (never during render) to stay hydration-safe,
  // and every change is written back so the toggles survive a reload (§6.3).
  useEffect(() => {
    setIndicatorState(loadIndicators());
  }, []);

  const updateIndicators = useCallback((next: IndicatorState) => {
    setIndicatorState(next);
    saveIndicators(next);
  }, []);

  const barsQuery = useQuery<BarsResponse>({
    queryKey: ['bars', instrumentId, timeframe, range, adjust],
    queryFn: () => api.bars(instrumentId, timeframe, range, adjust),
    staleTime: 60_000,
    retry: 1,
  });

  const bars = useMemo(() => barsQuery.data?.bars ?? [], [barsQuery.data]);
  const indicators = barsQuery.data?.indicators ?? {};
  const latest = bars[bars.length - 1];

  // ★BUG-2 / R-3: one pair, one source. The candles' own price axis prints
  // `bars[-1].c` (CandleChart leaves `lastValueVisible` on), so pairing a bars
  // close with a quote `changePct` rendered a data point that never existed
  // ("1,729.52 ▼2.2%" on bars that were +0.46%). Preferring `quote` instead would
  // only move the contradiction next to the axis label, so the pair stays on bars
  // whenever there are bars to draw — price, change and the date Tag all follow
  // it — and falls back to `quote` as a whole pair only when the chart is empty
  // (no axis label, hence nothing to contradict).
  const pairSource: 'bars' | 'quote' | 'none' = latest ? 'bars' : lastPrice != null ? 'quote' : 'none';

  const lastClose = pairSource === 'bars' && latest ? latest.c : pairSource === 'quote' ? (lastPrice ?? null) : null;
  const change = useMemo(() => {
    if (pairSource === 'bars' && latest) {
      if (bars.length < 2) return null;
      const previous = bars[bars.length - 2];
      if (!previous) return null;
      return (latest.c / previous.c - 1) * 100;
    }
    if (pairSource === 'quote') return changePct ?? null;
    return null;
  }, [bars, changePct, latest, pairSource]);

  const changeTone = (change ?? 0) >= 0 ? 'text-[var(--chart-up)]' : 'text-[var(--chart-down)]';

  const retry = useCallback(() => {
    void barsQuery.refetch();
  }, [barsQuery]);

  return (
    <section
      aria-label="K 线面板"
      className="flex h-full min-w-0 flex-col overflow-hidden border-r border-[var(--border-subtle)] bg-[var(--bg-canvas)]"
      data-print="hide"
    >
      <header className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--border-subtle)] px-3 py-2">
        <div className="flex min-w-0 flex-col">
          <span className="flex items-baseline gap-1.5">
            <span className="truncate text-[13px] font-semibold text-[var(--text-primary)]">{name}</span>
            <span className="mono shrink-0 text-[10.5px] text-[var(--text-tertiary)]">{symbol}</span>
          </span>
          <span className="flex items-baseline gap-1.5">
            <span className="num text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
              {lastClose === null ? '—' : num(lastClose, 2)}
            </span>
            <span className={cn('num text-[11px] font-medium', changeTone)}>{deltaArrow(change)}</span>
            <span className="text-[10px] text-[var(--text-tertiary)]">{currency}</span>
          </span>
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <Segmented options={TIMEFRAMES} value={timeframe} onChange={setTimeframe} ariaLabel={copy.chart.timeframe} />
          <Segmented options={RANGES} value={range} onChange={setRange} ariaLabel={copy.chart.range} />
          <IconButton
            label={copy.chart.indicators}
            aria-expanded={showSettings}
            onClick={() => setShowSettings((value) => !value)}
          >
            <Settings2 className="size-3.5" />
          </IconButton>
          {onCloseDrawer ? <IconButton label={copy.chart.closeDrawer} onClick={onCloseDrawer}>×</IconButton> : null}
        </div>
      </header>

      {showSettings ? (
        <div className="shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Segmented options={ADJUSTS} value={adjust} onChange={setAdjust} ariaLabel={copy.chart.adjust} />
            <IndicatorToggles state={indicatorState} onChange={updateIndicators} />
          </div>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col">
        {barsQuery.isLoading ? (
          <div className="flex flex-1 flex-col gap-2 p-3">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="flex-1" />
          </div>
        ) : barsQuery.isError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
            <p className="text-[12.5px] text-[var(--text-secondary)]">{copy.chart.error}</p>
            <p className="text-[11px] text-[var(--text-tertiary)]">
              {barsQuery.error instanceof Error ? barsQuery.error.message : ''}
            </p>
            <button
              type="button"
              onClick={retry}
              className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-2 py-1 text-[11.5px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
            >
              {copy.chart.retry}
            </button>
          </div>
        ) : bars.length === 0 ? (
          <div className="flex flex-1 items-center justify-center p-6 text-[12px] text-[var(--text-tertiary)]">
            {copy.chart.empty}
          </div>
        ) : (
          <CandleChart
            bars={bars}
            indicators={indicators}
            timezone={timezone}
            showVolume={indicatorState.volume}
            showBoll={indicatorState.boll}
            showMa={{ ma5: indicatorState.ma5, ma10: indicatorState.ma10, ma20: indicatorState.ma20, ma60: indicatorState.ma60 }}
            brushEnabled={Boolean(onBrushRange)}
            onBrushRange={onBrushRange}
          />
        )}
      </div>

      {/* ★v6 G2a：框选提示。区间反查的**结果**在报告头下方（`RangeHint`），
          这里只负责说你可以框一段、以及当前框的是哪一段 —— 两者不重复。 */}
      <div className="flex shrink-0 items-center gap-1.5 border-t border-[var(--border-subtle)] px-3 py-1 text-[10px] text-[var(--text-tertiary)]">
        <span>{copy.range.brushHint}</span>
        {brushRange ? (
          <span className="num truncate text-[var(--accent)]">
            {brushRange.from} ~ {brushRange.to}
          </span>
        ) : null}
      </div>

      <footer className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-[var(--border-subtle)] px-3 py-1.5 text-[10px] text-[var(--text-tertiary)]">
        <span>{copy.chart.dataSource}：{barsQuery.data?.meta.source ?? '—'}</span>
        <span aria-hidden="true">·</span>
        <span className="num">{barsQuery.data?.meta.barsCount ?? 0} 根</span>
        {barsQuery.data?.meta.cached ? <Badge tone="neutral">{copy.chart.cached}</Badge> : null}
        {barsQuery.data?.meta.source === 'synthetic' ? <Badge tone="warn">{copy.chart.synthetic}</Badge> : null}
        {latest ? <Tag>{epochToDate(latest.ts, timezone)}</Tag> : null}
      </footer>
    </section>
  );
}
