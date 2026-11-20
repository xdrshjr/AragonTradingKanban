'use client';

import { useEffect, useMemo, useState } from 'react';

import type { ChartDataset, ChartSelection } from '@/lib/chartDataset';
import type { ChartPreferences } from '@/lib/chartPreferences';
import {
  formatAmount, formatPercent, formatPrice, formatSigned,
  formatVolume, pricePrecision, timeframeLabel, volumeUnitLabel,
} from '@/lib/chartFormat';

export interface ChartReadoutProps {
  dataset: ChartDataset;
  preferences: ChartPreferences;
  selection: ChartSelection;
  onLatest?: () => void;
}

export type ReadoutIndicatorKey = keyof ChartDataset['indicators'];

const INDICATOR_DETAILS: Record<ReadoutIndicatorKey, [string, number]> = {
  ma5: ['MA5', 5], ma10: ['MA10', 10],
  ma20: ['MA20', 20], ma60: ['MA60', 60],
  ema12: ['EMA12', 12], ema26: ['EMA26', 26],
  bollUpper: ['BOLL UP', 20], bollMiddle: ['BOLL MID', 20],
  bollLower: ['BOLL LOW', 20],
  macd: ['DIF', 26], macdSignal: ['DEA', 34],
  macdHistogram: ['MACD 柱', 34], rsi14: ['RSI14', 15],
  kdjK: ['K', 9], kdjD: ['D', 9], kdjJ: ['J', 9],
  atr14: ['ATR14', 14], volumeMa5: ['MAVOL5', 5],
  volumeMa10: ['MAVOL10', 10],
};

const OSCILLATOR_KEYS: Record<ChartPreferences['oscillator'],
  ReadoutIndicatorKey[]> = {
  macd: ['macd', 'macdSignal', 'macdHistogram'], rsi: ['rsi14'],
  kdj: ['kdjK', 'kdjD', 'kdjJ'], atr: ['atr14'], none: [],
};

const PRECISION_CACHE = new WeakMap<ChartDataset,
  Partial<Record<ReadoutIndicatorKey | 'price', number>>>();

function getPrecisionCache(dataset: ChartDataset) {
  let cached = PRECISION_CACHE.get(dataset);
  if (!cached) {
    cached = {};
    PRECISION_CACHE.set(dataset, cached);
  }
  return cached;
}

/** Returns only the indicator columns enabled by display preferences. */
export function getVisibleIndicatorKeys(preferences: ChartPreferences) {
  const keys: ReadoutIndicatorKey[] = [];
  for (const key of ['ma5', 'ma10', 'ma20', 'ma60'] as const) {
    if (preferences.main[key]) keys.push(key);
  }
  if (preferences.main.ema) keys.push('ema12', 'ema26');
  if (preferences.main.boll) {
    keys.push('bollUpper', 'bollMiddle', 'bollLower');
  }
  if (preferences.volume) keys.push('volumeMa5', 'volumeMa10');
  return [...keys, ...OSCILLATOR_KEYS[preferences.oscillator]];
}

/** Provides consistent labels for the readout, table and export legends. */
export function getIndicatorLabel(key: ReadoutIndicatorKey): string {
  return INDICATOR_DETAILS[key][0];
}

/** Matches DOM price precision to all price series in the current package. */
export function getDatasetPricePrecision(dataset: ChartDataset): number {
  const cached = getPrecisionCache(dataset);
  if (cached.price !== undefined) return cached.price;
  const prices = dataset.bars.flatMap((bar) => [bar.o, bar.h, bar.l, bar.c]);
  for (const key of [
    'ma5', 'ma10', 'ma20', 'ma60', 'ema12', 'ema26',
    'bollUpper', 'bollMiddle', 'bollLower',
  ] as const) prices.push(...dataset.indicators[key].filter(
    (value): value is number => value !== null,
  ));
  cached.price = pricePrecision(prices);
  return cached.price;
}

function getIndicatorPrecision(
  dataset: ChartDataset, key: ReadoutIndicatorKey,
) {
  if (key === 'rsi14' || key.startsWith('kdj')) return 2;
  if (key.startsWith('ma') && !key.startsWith('macd')
    || key.startsWith('ema') || key.startsWith('boll')) {
    return getDatasetPricePrecision(dataset);
  }
  const cached = getPrecisionCache(dataset);
  if (cached[key] !== undefined) return cached[key];
  const keys = key.startsWith('macd')
    ? ['macd', 'macdSignal', 'macdHistogram'] as const : [key];
  const precision = pricePrecision(keys.flatMap(
    (seriesKey) => dataset.indicators[seriesKey],
  ));
  for (const seriesKey of keys) cached[seriesKey] = precision;
  return precision;
}

/** Uses the same precision and units for indicator legends and table cells. */
export function formatIndicatorValue(
  dataset: ChartDataset, key: ReadoutIndicatorKey, index: number,
): string {
  const value = dataset.indicators[key][index];
  if (key.startsWith('volume')) {
    return formatVolume(value, dataset.chart.volumeUnit);
  }
  return formatPrice(value, getIndicatorPrecision(dataset, key));
}

function getChangeTone(value: number | null) {
  if (value === null || value === 0) return 'var(--text-secondary)';
  return value > 0 ? 'var(--chart-up)' : 'var(--chart-down)';
}

function Metric({
  label, value, title, tone,
}: { label: string; value: string; title?: string; tone?: string }) {
  return (
    <div className="chart-readout-metric" title={title}>
      <dt>{label}</dt>
      <dd className="num" style={{ color: tone }}>{value}</dd>
    </div>
  );
}

function ReadoutHeader({
  dataset, selection, onLatest, index,
}: ChartReadoutProps & { index: number }) {
  const stat = dataset.stats[index]!;
  const labels = {
    latest: '最新周期', pointer: '历史检视',
    keyboard: '键盘检视', touch: '固定检视',
  };
  return (
    <div className="chart-readout-heading">
      <strong>{labels[selection.origin]}</strong>
      <span className="num">
        {stat.periodStart === stat.periodEnd ? stat.periodEnd
          : `${stat.periodStart} ~ ${stat.periodEnd}`}
      </span>
      {selection.origin !== 'latest' && onLatest ? (
        <button className="chart-tool-button" onClick={onLatest}>
          返回最新
        </button>
      ) : null}
      {dataset.meta.timeframe !== '1d'
        && index === dataset.bars.length - 1 ? (
          <small>末周期可能未结束</small>
        ) : null}
    </div>
  );
}

function PriceMetrics({
  dataset, index, precision,
}: { dataset: ChartDataset; index: number; precision: number }) {
  const bar = dataset.bars[index]!;
  const stat = dataset.stats[index]!;
  const label = timeframeLabel(dataset.meta.timeframe as '1d' | '1w' | '1m');
  const tone = getChangeTone(stat.changePct);
  return (
    <dl className="chart-readout-grid">
      <Metric label="开" value={formatPrice(bar.o, precision)} />
      <Metric label="高" value={formatPrice(bar.h, precision)} />
      <Metric label="低" value={formatPrice(bar.l, precision)} />
      <Metric label="收" value={formatPrice(bar.c, precision)} />
      <Metric label="前收" value={formatPrice(stat.previousClose, precision)} />
      <Metric label={`${label}涨跌`} value={formatSigned(stat.change, precision)}
        title="相邻同复权 K 线收盘比较；可能不同于交易所公布的除权参考涨跌幅"
        tone={tone} />
      <Metric label="涨跌幅" value={formatPercent(stat.changePct)} tone={tone} />
      <Metric label="振幅" value={formatPercent(stat.amplitudePct)} />
      <Metric label="成交量"
        value={formatVolume(bar.v, dataset.chart.volumeUnit)}
        title={`${bar.v} ${volumeUnitLabel(dataset.chart.volumeUnit)}`} />
      <Metric label="成交额"
        value={formatAmount(bar.amount, dataset.chart.amountCurrency)}
        title={bar.amount == null ? '数据源未提供成交额'
          : `${bar.amount} ${dataset.chart.amountCurrency}`} />
    </dl>
  );
}

function IndicatorReadout({
  dataset, preferences, index,
}: Omit<ChartReadoutProps, 'selection'> & { index: number }) {
  const stat = dataset.stats[index]!;
  const available = dataset.chart.warmupBarsCount + index + 1
    - (dataset.stats[0]?.leadingPartial ? 1 : 0);
  return (
    <dl className="chart-readout-indicators">
      {getVisibleIndicatorKeys(preferences).map((key) => {
        const value = dataset.indicators[key][index];
        const [label, needed] = INDICATOR_DETAILS[key];
        const missing = stat.leadingPartial ? '计算边界周期不完整'
          : `历史不足（需要 ${needed} 根，已有 ${available} 根）`;
        return (
          <Metric key={key} label={label}
            value={value == null ? `— ${missing}`
              : formatIndicatorValue(dataset, key, index)}
            title={key === 'atr14' ? dataset.chart.amountCurrency : undefined}
          />
        );
      })}
    </dl>
  );
}

/** Shows one indexed bar while keeping mouse movements out of live regions. */
export function ChartReadout(props: ChartReadoutProps) {
  const { dataset, selection } = props;
  const index = selection.index ?? dataset.bars.length - 1;
  const [announcement, setAnnouncement] = useState('');
  const precision = useMemo(() => getDatasetPricePrecision(dataset), [dataset]);
  useEffect(() => {
    const bar = dataset.bars[index];
    const stat = dataset.stats[index];
    if (selection.origin !== 'keyboard' || !bar || !stat) return;
    const timer = window.setTimeout(() => setAnnouncement(
      `${stat.periodEnd}，收盘 `
      + `${formatPrice(bar.c, precision)}，`
      + `涨跌幅 ${formatPercent(stat.changePct)}`,
    ), 150);
    return () => window.clearTimeout(timer);
  }, [dataset, index, precision, selection.origin]);
  if (!dataset.bars[index]) return null;
  const stat = dataset.stats[index]!;
  return (
    <section className="chart-readout" aria-label="行情读数">
      <ReadoutHeader {...props} index={index} />
      <PriceMetrics dataset={dataset} index={index} precision={precision} />
      {stat.previousClose == null || stat.previousClose <= 0 ? (
          <p className="chart-readout-hint">缺少有效前收基准</p>
        ) : null}
      {stat.leadingPartial ? (
        <p className="chart-readout-hint">计算边界周期不完整</p>
      ) : null}
      <IndicatorReadout {...props} index={index} />
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
    </section>
  );
}
