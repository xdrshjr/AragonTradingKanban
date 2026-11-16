'use client';

import { useCallback, useMemo } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { useUiStore } from '@/lib/store';
import type { Citation } from '@/lib/types';

/**
 * 一次回答的引用清单（spec §18.3.2 / §18.5.1）。
 *
 * 这里的 `Citation` 是后端**已校验过**的条目（每条都 ∈ `doc.sources`，非法项已被
 * 丢弃并计入 `citationsDropped`），所以渲染层不再猜测、不再过滤——只负责点击联动。
 */
export interface CitationLegendProps {
  citations: Citation[];
  onJumpToSource?: (sourceId: string) => void;
  className?: string;
}

export function CitationLegend({ citations, onJumpToSource, className }: CitationLegendProps) {
  const openSourceDrawer = useUiStore((state) => state.openSourceDrawer);

  // 去重且保序：顺序本身是「按命中分数」的语义，Key 也必须唯一。
  const items = useMemo(() => {
    const seen = new Set<string>();
    const out: Citation[] = [];
    for (const citation of citations) {
      if (!citation.sourceId || seen.has(citation.sourceId)) continue;
      seen.add(citation.sourceId);
      out.push(citation);
    }
    return out;
  }, [citations]);

  const handleClick = useCallback(
    (sourceId: string) => {
      // 没接联动回调时退回全局来源抽屉，chip 不会变成死按钮。
      if (onJumpToSource) onJumpToSource(sourceId);
      else openSourceDrawer(sourceId);
    },
    [onJumpToSource, openSourceDrawer],
  );

  if (items.length === 0) return null;

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      <span className="text-[10.5px] uppercase tracking-wide text-[var(--text-tertiary)]">
        {copy.ask.citationsTitle}
      </span>
      {items.map((citation) => {
        const label = [citation.sourceId, citation.title, citation.publisher].filter(Boolean).join(' · ');
        return (
          <button
            key={citation.sourceId}
            type="button"
            onClick={() => handleClick(citation.sourceId)}
            title={label}
            aria-label={`${copy.ask.citationsTitle} ${label}`}
            className={cn(
              'inline-flex max-w-[220px] items-center gap-1 rounded-[var(--radius-sm)]',
              'border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-1.5 py-px',
              'text-[10.5px] leading-[16px] text-[var(--text-secondary)] transition-colors duration-[var(--dur-fast)]',
              'hover:border-[var(--accent)]/40 hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]',
            )}
          >
            <span className="font-mono font-semibold text-[var(--accent)]">{citation.sourceId}</span>
            {citation.title ? <span className="truncate">{citation.title}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export default CitationLegend;
