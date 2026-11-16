'use client';

import { cn } from '@/lib/cn';
import { deltaArrow, num } from '@/lib/format';
import { ungroundedValues, normalizeNumberToken } from '@/lib/grounding';
import type { GroundingQuality, Metric, Tone } from '@/lib/types';

import { CitationChip } from '../CitationChip';
import { GroundingDot } from '../GroundingDot';

const TONE_CLASS: Record<string, string> = {
  pos: 'text-[var(--chart-up)]',
  neg: 'text-[var(--chart-down)]',
  neutral: 'text-[var(--text-primary)]',
};

/**
 * 指标卡。
 *
 * ★v6（D2 / AC-49）：每个值单元格带 `data-grounded`，未接地的值旁边挂一个
 * `GroundingDot`。判定只来自 `quality.grounding.samples`（客户端拿不到"该块全部数字"），
 * 所以只**标注缺失**，绝不把其余格子标成"已核验" —— `samples` 有 20 块上限（R-22）。
 */
export function MetricGrid({
  items,
  columns = 4,
  compact = false,
  className,
  quality,
  blockId,
}: {
  items: Metric[];
  columns?: number;
  compact?: boolean;
  className?: string;
  /** ★v6：`quality.grounding`；缺席则完全不渲染可信度痕迹。 */
  quality?: GroundingQuality | null;
  /** ★v6：块的派生 id（`lib/blocks.ts`），用于把样本索引到这个块上。 */
  blockId?: string | null;
}) {
  if (items.length === 0) return null;
  const missing = ungroundedValues(quality, blockId);
  return (
    <div
      className={cn('grid gap-px overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--border-subtle)]', className)}
      style={{ gridTemplateColumns: `repeat(${Math.min(columns, Math.max(2, items.length))}, minmax(0, 1fr))` }}
    >
      {items.map((item, index) => {
        const ungrounded = missing.size > 0 && missing.has(normalizeNumberToken(item.value));
        return (
          <div
            key={`${item.label}-${index}`}
            className={cn('flex min-w-0 flex-col gap-0.5 bg-[var(--bg-surface-1)]', compact ? 'px-2.5 py-2' : 'px-3 py-2.5')}
          >
            <span className="truncate text-[10.5px] uppercase tracking-wide text-[var(--text-tertiary)]">
              {item.label}
            </span>
            <span className="flex items-center gap-1">
              <span
                data-grounded={ungrounded ? 'false' : undefined}
                className={cn(
                  'num truncate font-semibold tracking-tight',
                  compact ? 'text-[13.5px]' : 'text-[16px]',
                  TONE_CLASS[item.tone ?? 'neutral'],
                )}
              >
                {item.value}
              </span>
              {/* 只在**确认未接地**时挂点。`samples` 有 20 块上限（R-22），所以「不在 missing 里」
              不等于「已核验」——恒定渲染一个 `grounded` 点等于替模型盖章
              （`lib/grounding.ts` 的纪律，与 `TableBlock` 同型）。 */}
              {ungrounded ? <GroundingDot value={item.value} grounded={false} /> : null}
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
        );
      })}
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
