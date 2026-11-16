'use client';

import { ArrowUpRight, X } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';

import { Button } from '../ui/Button';
import { useBrushRange } from '../chart/useBrushRange';

/**
 * K 线框选后的「区间联动条」（spec §19.3.3 / §19.5.2 / AC-54 / AC-64）。
 *
 * 为什么它是一条**独立于阅读模式**的横条：读者在 Tab 视图与全文视图之间切换时，
 * "这段行情关联哪几段"这个问题不该跟着换位置（§19.5.7 的 `view` × `range` 正交），
 * 所以组件只管"给我区间、给我回调"，不持有模式与 URL state——切换模式不会重建它，
 * 清除区间也不会碰 `view`。
 *
 * 三段降级文案是刻意分开的，因为它们说的不是一件事：
 *  - `provisional`：报告本身没有结构化章节，反查无意义（说"没有"而不是让空数组像坏了）；
 *  - `empty`：报告有章节，但这段时间确实没被提及；
 *  - `error`：反查失败/超时（`useBrushRange` 已经把 408 翻成 `timeout` 文案）。
 *
 * 成本：一次框选只发一次请求（防抖 250ms），命中段最多列 6 条——更多的靠"仅显示前 N 段"
 * 去报告里看，联动条不是第二个报告列表。
 */

/** 联动条上最多列出的命中段数。 */
const MAX_HITS = 6;

export interface RangeHintProps {
  reportId: string;
  /** 闭区间左端 `YYYY-MM-DD`。 */
  from: string;
  to: string;
  /** 清除区间：调用方负责删 URL 里的 `?range=`，**不得**顺带改 `?view=`。 */
  onClear: () => void;
  onJumpToBlock?: (blockId: string) => void;
  className?: string;
  /** 仅测试/低频场景调整；产品默认 250ms（spec §19.3.3）。 */
  debounceMs?: number;
}

export function RangeHint({
  reportId,
  from,
  to,
  onClear,
  onJumpToBlock,
  className,
  debounceMs,
}: RangeHintProps) {
  const { state, data, error } = useBrushRange({ reportId, from, to, debounceMs });

  // 没有区间就没有联动条：渲染一个空条只会占掉报告头的一行。
  if (state === 'idle' || state === 'disabled') return null;

  const hits = data ? data.items.slice(0, MAX_HITS) : [];
  const provisional = data?.reason === 'provisional_no_dates';
  const empty = state === 'ready' && data !== null && data.items.length === 0;

  return (
    <section
      aria-label={copy.range.title}
      className={cn(
        'border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3 py-1.5',
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10.5px] font-semibold tracking-wide text-[var(--text-tertiary)]">
          {copy.range.title}
        </span>

        {/* 计数行是这条横条的唯一"活"内容：读屏器只该听到它变化一次，而不是每段命中。 */}
        <div role="status" aria-live="polite" className={cn('min-w-0 flex-1', state === 'error' && 'hidden')}>
          {state === 'loading' ? (
            <p className="truncate text-[11.5px] text-[var(--text-tertiary)]">{copy.range.loading}</p>
          ) : null}

          {state === 'ready' && data ? (
            provisional ? (
              <p className="truncate text-[11.5px] text-[var(--text-tertiary)]">{copy.range.provisional}</p>
            ) : empty ? (
              <p className="truncate text-[11.5px] text-[var(--text-tertiary)]">{copy.range.empty}</p>
            ) : (
              <p className="truncate text-[11.5px] text-[var(--text-secondary)]">
                {fill(copy.range.hint, { from, to, count: data.total })}
                {data.truncated
                  ? ` ${fill(copy.range.more, { shown: data.items.length, total: data.total })}`
                  : ''}
              </p>
            )
          ) : null}
        </div>

        {state === 'error' ? (
          <p role="alert" className="min-w-0 flex-1 truncate text-[11.5px] text-[var(--danger)]">
            {error ?? copy.range.failed}
          </p>
        ) : null}

        <Button
          size="sm"
          variant="ghost"
          onClick={onClear}
          leadingIcon={<X className="size-3" aria-hidden="true" />}
        >
          {copy.range.clear}
        </Button>
      </div>

      {hits.length > 0 ? (
        <ul className="mt-1 flex items-center gap-1.5 overflow-x-auto pb-0.5">
          {hits.map((hit) => (
            <li
              key={hit.blockId}
              className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-[var(--radius-sm)]',
                'border border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-1.5 py-0.5',
              )}
            >
              <span className="mono max-w-[18ch] truncate text-[10.5px] text-[var(--text-tertiary)]">
                {hit.blockId}
              </span>
              <span className="max-w-[14ch] truncate text-[11px] text-[var(--text-secondary)]">
                {hit.sectionTitle}
              </span>
              {/* 重叠天数：copy.range 没有"天"这个单位的键，这里只给数字，不加自造中文。 */}
              <span className="mono text-[10.5px] text-[var(--text-tertiary)]">{hit.overlapDays}</span>
              {onJumpToBlock ? (
                <button
                  type="button"
                  onClick={() => onJumpToBlock(hit.blockId)}
                  aria-label={`${copy.range.jump} · ${hit.sectionTitle}`}
                  title={copy.range.jump}
                  className="rounded p-0.5 text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                >
                  <ArrowUpRight className="size-3" aria-hidden="true" />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
