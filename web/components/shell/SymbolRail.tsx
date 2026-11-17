'use client';

import { Clock, Search, Sparkles, Star } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { deltaArrow, num, shortSymbol } from '@/lib/format';
import type { ChartAdjust, Instrument, WatchlistEntry } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Input } from '../ui/Input';
import { Skeleton } from '../ui/Skeleton';
import { SymbolSearchInput } from './SymbolSearchInput';

export interface RailItem {
  instrument: Instrument;
  last?: number | null;
  changePct?: number | null;
  hasReport?: boolean;
  /** A4 / §18.4.5：这个标的有启用中的定时刷新（`schedules` 里 `enabled=1`）。 */
  scheduled?: boolean;
  /**
   * ★PCD-5：这一行的价格是谁给的。以前只有图表 footer 会标「合成数据」，左栏把
   * AAPL 的 100% 合成价格当真实价格展示。徽标复用同一个 `copy.chart.synthetic`。
   */
  source?: string | null;
  /**
   * ★PCD-3：这一行价格的口径。选中行跟随图表的复权口径，所以非前复权时要明说，
   * 否则"左栏 1,439.78 / 图表 8,840.24"看起来像 bug 而不是复权。
   */
  adjust?: ChartAdjust | null;
}

export interface SymbolRailProps {
  instruments: RailItem[];
  watchlist: WatchlistEntry[];
  selectedId: string | null;
  loading?: boolean;
  collapsed?: boolean;
  onSelect: (instrumentId: string) => void;
  onResolved: (instrument: Instrument) => void;
  onToggleWatchlist?: (instrumentId: string, inWatchlist: boolean) => void;
  onGenerate?: (instrumentId: string) => void;
  onToggleCollapsed?: () => void;
}

/**
 * 15% left rail (spec §6.3). Search on top, then 自选 and 最近. Hover reveals the
 * generate / watchlist actions, and they are reachable by keyboard because they
 * are always in the DOM (§5.5).
 */
export function SymbolRail({
  instruments,
  watchlist,
  selectedId,
  loading,
  collapsed,
  onSelect,
  onResolved,
  onToggleWatchlist,
  onGenerate,
  onToggleCollapsed,
}: SymbolRailProps) {
  const [query, setQuery] = useState('');
  const watchlistIds = useMemo(() => new Set(watchlist.map((entry) => entry.instrument.id)), [watchlist]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return instruments;
    return instruments.filter(
      (item) =>
        item.instrument.symbol.toLowerCase().includes(needle) ||
        item.instrument.name.toLowerCase().includes(needle),
    );
  }, [instruments, query]);

  const pinned = filtered.filter((item) => watchlistIds.has(item.instrument.id));
  const rest = filtered.filter((item) => !watchlistIds.has(item.instrument.id));

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const list = Array.from(
        (event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[data-rail-item]'),
      );
      const index = list.findIndex((element) => element === document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      const next = list[event.key === 'ArrowDown' ? Math.min(index + 1, list.length - 1) : Math.max(index - 1, 0)];
      next?.focus();
    },
    [],
  );

  if (collapsed) {
    return (
      <nav
        aria-label={copy.a11y.railNav}
        className="flex h-full w-14 flex-col items-center gap-1 border-r border-[var(--border-subtle)] bg-[var(--bg-surface-1)] py-2"
      >
        <button
          type="button"
          aria-label={copy.rail.expand}
          onClick={onToggleCollapsed}
          className="mb-1 rounded-[var(--radius-sm)] p-1.5 text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)]"
        >
          <Search className="size-4" />
        </button>
        {instruments.slice(0, 12).map((item) => (
          <button
            key={item.instrument.id}
            type="button"
            title={`${item.instrument.name} ${item.instrument.symbol}`}
            onClick={() => onSelect(item.instrument.id)}
            className={cn(
              'mono size-8 rounded-[var(--radius-sm)] text-[9px] transition-colors',
              item.instrument.id === selectedId
                ? 'bg-[var(--accent-weak)] text-[var(--accent)]'
                : 'text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)]',
            )}
          >
            {shortSymbol(item.instrument.symbol).slice(0, 5)}
          </button>
        ))}
      </nav>
    );
  }

  return (
    <nav
      aria-label={copy.a11y.railNav}
      className="flex h-full min-w-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--bg-surface-1)]"
    >
      <div className="shrink-0 space-y-1.5 border-b border-[var(--border-subtle)] px-2 py-2">
        <SymbolSearchInput
          onResolved={(instrument) => {
            onResolved(instrument);
            setQuery('');
          }}
        />
        <div className="flex items-center justify-between px-0.5">
          <span className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
            {instruments.length} 个标的
          </span>
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onClear={() => setQuery('')}
            placeholder={copy.rail.searchPlaceholder}
            aria-label={copy.rail.search}
            inputSize="sm"
            className="h-6 w-[52%] border-transparent bg-transparent px-1"
            leadingIcon={<Search className="size-3" />}
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto py-1" onKeyDown={handleKeyDown}>
        {loading ? (
          <div className="space-y-1 px-2 py-1">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-9 w-full" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <p className="px-3 py-6 text-center text-[11px] text-[var(--text-tertiary)]">{copy.rail.noResults}</p>
        ) : (
          <>
            {pinned.length > 0 ? (
              <RailGroup label={copy.rail.watchlist}>
                {pinned.map((item) => (
                  <RailRow
                    key={item.instrument.id}
                    item={item}
                    selected={item.instrument.id === selectedId}
                    inWatchlist
                    onSelect={onSelect}
                    onToggleWatchlist={onToggleWatchlist}
                    onGenerate={onGenerate}
                  />
                ))}
              </RailGroup>
            ) : null}
            {rest.length > 0 ? (
              <RailGroup label={pinned.length > 0 ? copy.rail.recent : copy.rail.recent}>
                {rest.map((item) => (
                  <RailRow
                    key={item.instrument.id}
                    item={item}
                    selected={item.instrument.id === selectedId}
                    inWatchlist={false}
                    onSelect={onSelect}
                    onToggleWatchlist={onToggleWatchlist}
                    onGenerate={onGenerate}
                  />
                ))}
              </RailGroup>
            ) : null}
          </>
        )}
      </div>
    </nav>
  );
}

function RailGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-1">
      <p className="px-3 py-1 text-[10px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">{label}</p>
      <ul>{children}</ul>
    </div>
  );
}

function RailRow({
  item,
  selected,
  inWatchlist,
  onSelect,
  onToggleWatchlist,
  onGenerate,
}: {
  item: RailItem;
  selected: boolean;
  inWatchlist: boolean;
  onSelect: (id: string) => void;
  onToggleWatchlist?: (id: string, inWatchlist: boolean) => void;
  onGenerate?: (id: string) => void;
}) {
  const change = item.changePct ?? null;
  const tone = change === null ? 'text-[var(--text-tertiary)]' : change >= 0 ? 'text-[var(--chart-up)]' : 'text-[var(--chart-down)]';

  return (
    <li className="group relative">
      <button
        type="button"
        data-rail-item
        onClick={() => onSelect(item.instrument.id)}
        aria-current={selected ? 'true' : undefined}
        className={cn(
          'flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors',
          selected ? 'bg-[var(--accent-weak)]' : 'hover:bg-[var(--bg-hover)]',
        )}
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1">
            <span
              className={cn(
                'truncate text-[12.5px] font-medium',
                selected ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]',
              )}
            >
              {item.instrument.name}
            </span>
            {item.hasReport ? (
              <span aria-label="已有报告" className="size-1 rounded-full bg-[var(--accent)]/70" />
            ) : null}
            {item.scheduled ? (
              <Clock aria-label={copy.schedule.railBadge} className="size-2.5 shrink-0 text-[var(--accent)]/80" />
            ) : null}
          </span>
          <span className="mono block truncate text-[10px] text-[var(--text-tertiary)]">
            {item.instrument.symbol}
          </span>
        </span>
        <span className="shrink-0 text-right">
          <span className="num block text-[11.5px] text-[var(--text-primary)]">
            {item.last === null || item.last === undefined ? '—' : num(item.last, 2)}
          </span>
          <span className="flex items-center justify-end gap-1">
            {item.adjust && item.adjust !== 'qfq' ? (
              <span className="text-[9px] text-[var(--text-tertiary)]">{copy.chart.adjustApplied[item.adjust]}</span>
            ) : null}
            {item.source === 'synthetic' ? (
              <Badge tone="warn" className="px-1 py-0 text-[9px] leading-[13px]">
                {copy.chart.synthetic}
              </Badge>
            ) : null}
            <span className={cn('num block text-[10px]', tone)}>{deltaArrow(change)}</span>
          </span>
        </span>
      </button>

      <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        {onGenerate ? (
          <button
            type="button"
            aria-label={copy.rail.generate}
            title={copy.rail.generate}
            onClick={(event) => {
              event.stopPropagation();
              onGenerate(item.instrument.id);
            }}
            className="rounded bg-[var(--bg-surface-2)]/90 p-1 text-[var(--text-tertiary)] backdrop-blur transition-colors hover:text-[var(--accent)]"
          >
            <Sparkles className="size-3" />
          </button>
        ) : null}
        {onToggleWatchlist ? (
          <button
            type="button"
            aria-label={inWatchlist ? copy.rail.remove : copy.rail.add}
            title={inWatchlist ? copy.rail.remove : copy.rail.add}
            onClick={(event) => {
              event.stopPropagation();
              onToggleWatchlist(item.instrument.id, inWatchlist);
            }}
            className={cn(
              'rounded bg-[var(--bg-surface-2)]/90 p-1 backdrop-blur transition-colors',
              inWatchlist ? 'text-[var(--accent)]' : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]',
            )}
          >
            <Star className="size-3" fill={inWatchlist ? 'currentColor' : 'none'} />
          </button>
        ) : null}
      </div>
    </li>
  );
}
