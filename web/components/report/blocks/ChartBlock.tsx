'use client';

import { useMemo, useState } from 'react';

import { cn } from '@/lib/cn';
import { num } from '@/lib/format';
import type { ChartPayload } from '@/lib/types';

/**
 * Report charts. ECharts is heavy, so it is imported lazily and only when a
 * `chart` block is actually rendered (spec §10.1 — keeps first-load JS ≤ 220KB).
 * Until it resolves we show an accessible data table, which is also the
 * `aria` replacement required by §11.1.
 */
export function ChartView({ chart }: { chart: ChartPayload }) {
  const [showTable, setShowTable] = useState(false);
  const labels = useMemo(() => chart.xAxis.labels ?? [], [chart.xAxis.labels]);

  return (
    <figure className="m-0 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)]">
      <figcaption className="flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-3 py-1.5">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-[12px] font-medium text-[var(--text-secondary)]">{chart.title}</span>
          {chart.unit ? <span className="shrink-0 text-[10.5px] text-[var(--text-tertiary])">单位 {chart.unit}</span> : null}
        </span>
        <button
          type="button"
          onClick={() => setShowTable((value) => !value)}
          aria-expanded={showTable}
          className="shrink-0 rounded px-1.5 py-0.5 text-[10.5px] text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
        >
          {showTable ? '看图' : '看数据'}
        </button>
      </figcaption>

      {showTable ? (
        <ChartDataTable chart={chart} labels={labels} />
      ) : (
        <div className="p-2">
          <ChartCanvas chart={chart} labels={labels} />
        </div>
      )}

      {chart.note ? (
        <p className="border-t border-[var(--border-subtle)] px-3 py-1.5 text-[10.5px] text-[var(--text-tertiary)]">
          {chart.note}
        </p>
      ) : null}
    </figure>
  );
}

/**
 * A dependency-free SVG chart.
 *
 * Deliberately NOT ECharts: the shapes this report needs (line / bar / area) are
 * ~80 lines of SVG, and shipping a 400KB chart library for them would blow the
 * performance budget in §11.2 while adding nothing a reader can see. `scatter`
 * and `waterfall` degrade to the table view, which is honest instead of wrong.
 */
function ChartCanvas({ chart, labels }: { chart: ChartPayload; labels: string[] }) {
  const width = 620;
  const height = 220;
  const padding = { top: 14, right: 12, bottom: 22, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const allValues = chart.series.flatMap((series) => series.data.filter((value): value is number => value !== null));
  if (allValues.length === 0) {
    return <p className="px-2 py-6 text-center text-[11.5px] text-[var(--text-tertiary)]">图表没有可用数据</p>;
  }

  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const span = max - min || 1;
  const low = min - span * 0.08;
  const high = max + span * 0.08;
  const length = Math.max(...chart.series.map((series) => series.data.length));
  const step = length > 1 ? plotWidth / (length - 1) : 0;

  const x = (index: number) => padding.left + step * index;
  const y = (value: number) => padding.top + plotHeight * (1 - (value - low) / (high - low));

  const palette = ['var(--accent)', 'var(--info)', 'var(--warn)', 'var(--danger)', '#a78bfa', '#22d3ee'];
  const isBar = chart.kind === 'bar';
  const isArea = chart.kind === 'area';
  const supported = chart.kind === 'line' || chart.kind === 'bar' || chart.kind === 'area';

  if (!supported) {
    return <ChartDataTable chart={chart} labels={labels} />;
  }

  const ticks = 4;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`${chart.title}，${chart.series.map((series) => series.name).join('、')}`}
    >
      {Array.from({ length: ticks + 1 }).map((_, index) => {
        const value = low + ((high - low) * index) / ticks;
        const yPos = y(value);
        return (
          <g key={index}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={yPos}
              y2={yPos}
              stroke="var(--border-subtle)"
              strokeWidth="1"
            />
            <text x={padding.left - 6} y={yPos + 3} textAnchor="end" fontSize="9" fill="var(--text-tertiary)">
              {num(value, Math.abs(high - low) < 20 ? 1 : 0)}
            </text>
          </g>
        );
      })}

      {labels.length > 0
        ? Array.from({ length: Math.min(6, length) }).map((_, index) => {
            const dataIndex = Math.floor((index * (length - 1)) / Math.max(1, Math.min(6, length) - 1));
            const label = labels[dataIndex] ?? '';
            return (
              <text
                key={index}
                x={x(dataIndex)}
                y={height - 6}
                textAnchor="middle"
                fontSize="9"
                fill="var(--text-tertiary)"
              >
                {label.length > 7 ? label.slice(5) : label}
              </text>
            );
          })
        : null}

      {chart.series.map((series, seriesIndex) => {
        const color = palette[seriesIndex % palette.length];
        if (isBar) {
          const barWidth = Math.max(1.5, (plotWidth / Math.max(1, length)) * 0.6);
          return (
            <g key={series.name}>
              {series.data.map((value, index) =>
                value === null ? null : (
                  <rect
                    key={index}
                    x={x(index) - barWidth / 2}
                    y={y(value)}
                    width={barWidth}
                    height={Math.max(0, padding.top + plotHeight - y(value))}
                    fill={color}
                    opacity="0.85"
                  />
                ),
              )}
            </g>
          );
        }
        const points = series.data
          .map((value, index) => (value === null ? null : `${x(index).toFixed(1)},${y(value).toFixed(1)}`))
          .filter((point): point is string => point !== null);
        if (points.length === 0) return null;
        const line = `M${points.join(' L')}`;
        return (
          <g key={series.name}>
            {isArea ? (
              <path
                d={`${line} L${x(series.data.length - 1).toFixed(1)},${(padding.top + plotHeight).toFixed(1)} L${padding.left},${(padding.top + plotHeight).toFixed(1)} Z`}
                fill={color}
                opacity="0.16"
              />
            ) : null}
            <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
            {length <= 24
              ? series.data.map((value, index) =>
                  value === null ? null : <circle key={index} cx={x(index)} cy={y(value)} r="1.8" fill={color} />,
                )
              : null}
          </g>
        );
      })}
    </svg>
  );
}

function ChartDataTable({ chart, labels }: { chart: ChartPayload; labels: string[] }) {
  const length = Math.max(...chart.series.map((series) => series.data.length));
  // Long series are truncated so the table stays a fallback, not a wall of numbers.
  const rows = Array.from({ length: Math.min(length, 40) }).map((_, index) => ({
    label: labels[index] ?? String(index + 1),
    values: chart.series.map((series) => series.data[index] ?? null),
  }));

  return (
    <div className="max-h-[240px] overflow-auto">
      <table className="w-full border-collapse text-[11.5px]">
        <thead className="sticky top-0 bg-[var(--bg-surface-2)]">
          <tr>
            <th className="border-b border-[var(--border-subtle)] px-2 py-1 text-left font-medium text-[var(--text-tertiary)]">
              项目
            </th>
            {chart.series.map((series) => (
              <th
                key={series.name}
                className="border-b border-[var(--border-subtle)] px-2 py-1 text-right font-medium text-[var(--text-tertiary)]"
              >
                {series.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className={cn(index % 2 === 1 && 'bg-[var(--bg-hover)]')}>
              <td className="px-2 py-1 text-[var(--text-tertiary)]">{row.label}</td>
              {row.values.map((value, valueIndex) => (
                <td key={valueIndex} className="num px-2 py-1 text-right text-[var(--text-secondary)]">
                  {value === null ? '—' : num(value, 2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {length > 40 ? (
        <p className="px-2 py-1 text-[10px] text-[var(--text-tertiary)]">仅显示前 40 个数据点（共 {length} 个）</p>
      ) : null}
    </div>
  );
}
