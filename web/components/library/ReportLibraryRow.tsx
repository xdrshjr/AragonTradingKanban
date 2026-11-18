'use client';

/**
 * 报告库的一行（52px）：stance 条 + 标题 + 元信息 + 行内动作。
 *
 * 三条设计约束来自 spec §23.3.8：
 *
 * 1. **整行是一个链接**（`libraryHref` 100% 带 `?report=`）——点 v12 就必须打开 v12（U-0b）。
 * 2. **stance 不能只靠颜色**：色条旁边永远有文字徽章，`aria-label` 也把结论说出来。
 * 3. **行内动作不抢焦点**：`P` / `E` / `Delete` 由页面级 keydown 负责，按钮只做鼠标路径。
 */

import Link from 'next/link';
import { ArrowRight, RotateCcw, Star } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { relativeTime } from '@/lib/format';
import { daysLeftInTrash, libraryHref } from '@/lib/library';
import type { ReportLibraryItem, Stance } from '@/lib/types';

import { Badge, type BadgeTone } from '../ui/Badge';

const STANCE_TONE: Record<Stance, BadgeTone> = {
  buy: 'up',
  sell: 'down',
  hold: 'neutral',
  watch: 'info',
};

const STANCE_STRIPE: Record<Stance, string> = {
  buy: 'bg-[var(--chart-up)]',
  sell: 'bg-[var(--chart-down)]',
  hold: 'bg-[var(--text-tertiary)]',
  watch: 'bg-[var(--info)]',
};

export interface ReportLibraryRowProps {
  item: ReportLibraryItem;
  selected?: boolean;
  /** 回收站的行只给「恢复」；**没有**"永久删除"（避免"永久"按钮被误点）。 */
  trash?: boolean;
  trashRetentionDays?: number;
  onOpen?: (item: ReportLibraryItem) => void;
  onToggleStar?: (item: ReportLibraryItem) => void;
  onRestore?: (item: ReportLibraryItem) => void;
}

function trustLabel(item: ReportLibraryItem): string {
  const ratio = item.report.groundedRatio;
  if (ratio === null || ratio === undefined) return '—';
  return `${Math.round(ratio * 100)}%`;
}

export function ReportLibraryRow({
  item,
  selected = false,
  trash = false,
  trashRetentionDays = 30,
  onOpen,
  onToggleStar,
  onRestore,
}: ReportLibraryRowProps) {
  const report = item.report;
  const stance = report.stance ?? 'watch';
  const when = relativeTime(report.generatedAt);
  const trust = trustLabel(item);
  const badges: string[] = [];
  if (report.provisional) badges.push(copy.report.provisional);
  if (report.status === 'degraded') badges.push(copy.report.degraded);
  if (item.integrity === 'corrupt') badges.push(copy.library.integrityCorrupt);
  if (report.quality?.source === 'mock') badges.push(copy.report.mock);

  const label = [
    item.instrument.name || item.instrument.symbol,
    `v${report.version}`,
    when,
    `可信 ${trust}`,
    ...badges,
  ].join('，');

  return (
    <div
      role="option"
      aria-selected={selected}
      aria-label={label}
      data-report-id={report.id}
      className={cn(
        'group relative flex h-[52px] items-center gap-3 border-b border-[var(--border-subtle)] pl-3 pr-2',
        'transition-colors',
        selected ? 'bg-[var(--bg-hover)]' : 'hover:bg-[var(--bg-surface-2)]',
      )}
    >
      <span aria-hidden="true" className={cn('h-6 w-[2px] shrink-0 rounded-full', STANCE_STRIPE[stance])} />

      <Link
        href={libraryHref(item.instrument.symbol, report.id)}
        onClick={() => onOpen?.(item)}
        className="flex min-w-0 flex-1 items-center gap-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-1.5">
            <span className="truncate text-[12.5px] font-medium text-[var(--text-primary)]">
              {item.instrument.name || item.instrument.symbol}
            </span>
            <span className="mono shrink-0 text-[10px] text-[var(--text-tertiary)]">
              {item.instrument.symbol}
            </span>
            <Badge tone={STANCE_TONE[stance]} className="shrink-0">
              {copy.report.stance[stance]}
            </Badge>
          </span>
          <span
            className="mt-0.5 block truncate text-[11.5px] text-[var(--text-secondary)]"
            title={report.headline || report.title}
          >
            {report.headline || report.title}
          </span>
        </span>

        <span className="flex shrink-0 items-center gap-2 text-[10.5px] text-[var(--text-tertiary)]">
          <span className="mono">v{report.version}</span>
          <span>{when}</span>
          <span className={cn('mono', report.groundedRatio && report.groundedRatio < 0.9 && 'text-[var(--warn)]')}>
            {trust}
          </span>
          {badges.length > 0 ? (
            <span className="hidden items-center gap-1 sm:flex">
              {badges.map((badge) => (
                <Badge key={badge} tone={badge === copy.report.mock ? 'warn' : 'neutral'}>
                  {badge}
                </Badge>
              ))}
            </span>
          ) : null}
        </span>
      </Link>

      <span className="flex shrink-0 items-center gap-1">
        {trash ? (
          <>
            <span className="hidden text-[10px] text-[var(--text-tertiary)] md:inline">
              {copy.library.trashDaysLeft.replace(
                '{days}',
                String(daysLeftInTrash(item.deletedAt, trashRetentionDays)),
              )}
            </span>
            <button
              type="button"
              onClick={() => onRestore?.(item)}
              className="inline-flex h-6 items-center gap-1 rounded-[var(--radius-sm)] px-1.5 text-[11px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
            >
              <RotateCcw className="size-3" aria-hidden="true" />
              {copy.library.actions.restore}
            </button>
          </>
        ) : (
          <button
            type="button"
            aria-pressed={report.pinned}
            aria-label={report.pinned ? copy.library.actions.unstar : copy.library.actions.star}
            onClick={() => onToggleStar?.(item)}
            className={cn(
              'inline-flex size-6 items-center justify-center rounded-[var(--radius-sm)] text-[var(--text-tertiary)]',
              'hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
              report.pinned && 'text-[var(--accent)]',
            )}
          >
            <Star className={cn('size-3.5', report.pinned && 'fill-current')} aria-hidden="true" />
          </button>
        )}
        <ArrowRight
          aria-hidden="true"
          className="size-3 text-[var(--text-tertiary)] transition-transform group-hover:translate-x-0.5"
        />
      </span>
    </div>
  );
}
