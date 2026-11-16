'use client';

import { cn } from '@/lib/cn';
import { deltaArrow, num } from '@/lib/format';
import type { Metric, Tone } from '@/lib/types';

import { CitationChip } from '../CitationChip';

const TONE_CLASS: Record<string, string> = {
  pos: 'text-[var(--chart-up)]',
  neg: 'text-[var(--chart-down)]',
  neutral: 'text-[var(--text-primary)]',
};

export function MetricGrid({
  items,
  columns = 4,
  compact = false,
  className,
}: {
  items: Metric[];
  columns?: number;
  compact?: boolean;
  className?: string;
}) {
  if (items.length === 0) return null;
  return (
    <div
      className={cn('grid gap-px overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--border-subtle)]', className)}
      style={{ gridTemplateColumns: `repeat(${Math.min(columns, Math.max(2, items.length))}, minmax(0, 1fr))` }}
    >
      {items.map((item, index) => (
        <div
          key={`${item.label}-${index}`}
          className={cn('flex min-w-0 flex-col gap-0.5 bg-[var(--bg-surface-1)]', compact ? 'px-2.5 py-2' : 'px-3 py-2.5')}
        >
          <span className="truncate text-[10.5px] uppercase tracking-wide text-[var(--text-tertiary)]">
            {item.label}
          </span>
          <span
            className={cn(
              'num truncate font-semibold tracking-tight',
              compact ? 'text-[13.5px]' : 'text-[16px]',
              TONE_CLASS[item.tone ?? 'neutral'],
            )}
          >
            {item.value}
          </span>
          <span className="flex items-center gap-1 text-[10.5px] text-[var(--text-tertiary)]">
            {item.deltaPct !== null && item.deltaPct !== undefined ? (
              <span className={TONE_CLASS[(item.deltaPct > 0 ? 'pos' : item.deltaPct < 0 ? 'neg' : 'neutral') as Tone]}>
                {deltaArrow(item.deltaPct)}
              </span>
            ) : null}
            {item.sourceRefs?.map((ref) => (
              <CitationChip key={ref} sourceId={ref} className="!h-4 !min-w-[16px] !text-[9px]" />
            ))}
          </span>
        </div>
      ))}
    </div>
  );
}

export function MetricRow({ metric }: { metric: Metric }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--border-subtle)] py-1.5 last:border-b-0">
      <span className="truncate text-[12px] text-[var(--text-secondary)]">{metric.label}</span>
      <span className="flex items-baseline gap-2">
        <span className={cn('num text-[13px] font-medium', TONE_CLASS[metric.tone ?? 'neutral'])}>{metric.value}</span>
        {metric.deltaPct !== null && metric.deltaPct !== undefined ? (
          <span className="num text-[10.5px] text-[var(--text-tertiary)]">{num(metric.deltaPct, 1)}%</span>
        ) : null}
      </span>
    </div>
  );
}
