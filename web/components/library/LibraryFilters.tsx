'use client';

/**
 * 4 个 scope tab + 计数徽章 + 搜索框（spec §23.3.8 / ★P1-6）。
 *
 * **5 个 scope ≠ 5 个 tab**：`latest` 不在 tab 里（它服务首页与命令面板），库页默认 `all`
 * ——"我做过哪些报告、有哪些版本"的答案必须包含 superseded 版本。
 *
 * 计数徽章来自 `counts`（**不含** `q` / `instrumentId`）：输入关键词时徽章一个数字都不动，
 * 那正是"过滤器不是数据变更"的可视化版本（★P1-4）。
 */

import { Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { SCOPE_TABS, scopeLabel, type ScopeTab } from '@/lib/library';
import type { LibraryScope } from '@/lib/types';

export interface LibraryFiltersProps {
  scope: ScopeTab;
  counts: Partial<Record<LibraryScope, number>>;
  q: string;
  total: number;
  onScopeChange: (scope: ScopeTab) => void;
  onQueryChange: (q: string) => void;
  /** `/` 键要能把焦点交给搜索框：所以 ref 由父组件持有（页面级 keydown 的落点）。 */
  searchRef?: React.RefObject<HTMLInputElement | null>;
}

const DEBOUNCE_MS = 250;

export function LibraryFilters({
  scope,
  counts,
  q,
  total,
  onScopeChange,
  onQueryChange,
  searchRef,
}: LibraryFiltersProps) {
  const [draft, setDraft] = useState(q);
  const internalRef = useRef<HTMLInputElement>(null);
  const ref = searchRef ?? internalRef;

  // 外部（URL / Esc 清空）改变 `q` 时，输入框要跟上。
  useEffect(() => {
    setDraft(q);
  }, [q]);

  // 250ms 防抖后写回 URL（可收藏、可分享）；`router.replace` 由父组件做。
  useEffect(() => {
    if (draft === q) return;
    const timer = setTimeout(() => onQueryChange(draft), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, q, onQueryChange]);

  return (
    <div className="flex flex-col gap-2 border-b border-[var(--border-subtle)] pb-2 sm:flex-row sm:items-center sm:justify-between">
      <div role="tablist" aria-label={copy.library.title} className="flex items-center gap-1">
        {SCOPE_TABS.map((tab) => {
          const active = tab === scope;
          const count = counts[tab];
          return (
            <button
              key={tab}
              role="tab"
              type="button"
              aria-selected={active}
              onClick={() => onScopeChange(tab)}
              className={cn(
                'inline-flex h-7 items-center gap-1.5 rounded-[var(--radius-sm)] px-2 text-[12px]',
                'transition-colors',
                active
                  ? 'bg-[var(--accent-weak)] text-[var(--accent)]'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
              )}
            >
              {scopeLabel(tab)}
              {typeof count === 'number' ? (
                <span className="mono text-[10px] text-[var(--text-tertiary)]">{count}</span>
              ) : null}
            </button>
          );
        })}
        <span className="ml-1 text-[10.5px] text-[var(--text-tertiary)]">
          {copy.library.count.replace('{total}', String(total))}
        </span>
      </div>

      <label className="relative flex h-7 min-w-0 items-center sm:w-[240px]">
        <Search className="pointer-events-none absolute left-2 size-3 text-[var(--text-tertiary)]" aria-hidden="true" />
        <input
          ref={ref}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              setDraft('');
              onQueryChange('');
            }
          }}
          placeholder={copy.library.searchPlaceholder}
          aria-label={copy.library.searchLabel}
          className={cn(
            'h-7 w-full rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--bg-surface-2)]',
            'pl-7 pr-2 text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)]',
            'focus:border-[var(--border-strong)] focus:outline-none',
          )}
        />
      </label>
    </div>
  );
}
