'use client';

import { useEffect, useMemo, useState } from 'react';

import {
  findDateIndex, type ChartDataset, type ChartSelection,
} from '@/lib/chartDataset';
import type { ChartPreferences } from '@/lib/chartPreferences';
import {
  formatAmount, formatPercent, formatPrice, formatSigned,
  formatVolume,
} from '@/lib/chartFormat';

import {
  formatIndicatorValue, getDatasetPricePrecision, getIndicatorLabel,
  getVisibleIndicatorKeys, type ReadoutIndicatorKey,
} from './ChartReadout';

export interface ChartDataTableProps {
  dataset: ChartDataset;
  preferences: ChartPreferences;
  selection: ChartSelection;
  onSelect: (index: number) => void;
}

const PAGE_SIZE = 20;
const BASE_COLUMNS = [
  '日期', '开', '高', '低', '收', '前收', '涨跌', '涨跌幅',
  '振幅', '成交量', '成交额',
];

function TableNavigation({
  start, end, count, onPrevious, onNext, onLocate,
}: {
  start: number; end: number; count: number;
  onPrevious: () => void; onNext: () => void;
  onLocate: (date: string) => void;
}) {
  return (
    <div className="chart-table-navigation">
      <button className="chart-tool-button" onClick={onPrevious}
        disabled={start === 0}>前 20 根</button>
      <span className="num">{count ? start + 1 : 0}–{end} / {count}</span>
      <button className="chart-tool-button" onClick={onNext}
        disabled={end >= count}>后 20 根</button>
      <label>表格日期
        <input type="date" onChange={(event) => {
          if (event.target.value) onLocate(event.target.value);
        }} />
      </label>
    </div>
  );
}

function getRowValues(dataset: ChartDataset, index: number, precision: number) {
  const bar = dataset.bars[index]!;
  const stat = dataset.stats[index]!;
  return [
    ...[bar.o, bar.h, bar.l, bar.c, stat.previousClose].map(
      (value) => formatPrice(value, precision),
    ),
    formatSigned(stat.change, precision), formatPercent(stat.changePct),
    formatPercent(stat.amplitudePct),
    formatVolume(bar.v, dataset.chart.volumeUnit),
    formatAmount(bar.amount, dataset.chart.amountCurrency),
  ];
}

function TableRow({
  dataset, index, precision, indicatorKeys, selected, onSelect,
}: {
  dataset: ChartDataset; index: number; precision: number;
  indicatorKeys: ReadoutIndicatorKey[]; selected: boolean;
  onSelect: (index: number) => void;
}) {
  const stat = dataset.stats[index]!;
  const period = stat.periodStart === stat.periodEnd ? stat.periodEnd
    : `${stat.periodStart} ~ ${stat.periodEnd}`;
  return (
    <tr aria-selected={selected} data-selected={selected || undefined}>
      <th scope="row">
        <button onClick={() => onSelect(index)} aria-pressed={selected}>
          {period}
        </button>
      </th>
      {getRowValues(dataset, index, precision).map((value, column) => (
        <td key={BASE_COLUMNS[column + 1]} className="num">{value}</td>
      ))}
      {indicatorKeys.map((key) => (
        <td key={key} className="num"
          title={dataset.indicators[key][index]?.toString()}>
          {formatIndicatorValue(dataset, key, index)}
        </td>
      ))}
    </tr>
  );
}

/** Provides a paged text alternative using the exact chart response values. */
export function ChartDataTable({
  dataset, preferences, selection, onSelect,
}: ChartDataTableProps) {
  const count = dataset.bars.length;
  const [end, setEnd] = useState(count);
  const start = Math.max(0, end - PAGE_SIZE);
  const keys = getVisibleIndicatorKeys(preferences);
  const precision = useMemo(() => getDatasetPricePrecision(dataset), [dataset]);
  useEffect(() => setEnd(count), [dataset, count]);
  useEffect(() => {
    const index = selection.index;
    if (index === null || selection.origin !== 'keyboard') return;
    setEnd((current) => index < current - PAGE_SIZE || index >= current
      ? Math.min(count, index + PAGE_SIZE) : current);
  }, [dataset, selection.index, selection.origin, count]);
  return (
    <section className="chart-data-table" aria-label="K 线行情数据表">
      <TableNavigation start={start} end={Math.min(end, count)} count={count}
        onPrevious={() => setEnd(start)}
        onNext={() => setEnd(Math.min(count, end + PAGE_SIZE))}
        onLocate={(date) => {
          const index = findDateIndex(dataset, date);
          if (index < 0) return;
          setEnd(Math.min(count, index + PAGE_SIZE));
          onSelect(index);
        }} />
      <div className="chart-table-scroll" tabIndex={0}>
        <table>
          <caption className="sr-only">行情与当前启用的指标，每页 20 根</caption>
          <thead><tr>
            {[...BASE_COLUMNS, ...keys.map(getIndicatorLabel)].map((label) => (
              <th key={label} scope="col">{label}</th>
            ))}
          </tr></thead>
          <tbody>{dataset.bars.slice(start, end).map((bar, offset) => (
            <TableRow key={bar.ts} dataset={dataset} index={start + offset}
              precision={precision} indicatorKeys={keys}
              selected={selection.index === start + offset}
              onSelect={onSelect} />
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}
