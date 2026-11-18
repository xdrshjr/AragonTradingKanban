'use client';

/**
 * 首页主区块：**最近生成的研报**（spec §23.3.7 / U1 + U6）。
 *
 * 它替代的是两件事，而不是"多一个区块"：
 *
 * 1. **可发现性挂在错的表上**：过去首页列的是**自选标的**，所以把茅台移出自选，它的 8 份
 *    存活版本就从首页彻底消失，而 `reports` 一行没少（U-0a / E-120）。"我的报告在哪"不该由
 *    "我的自选股"回答。
 * 2. **14 次串行 fetch**：`1 × /watchlist + 1 × /instruments + 最多 12 × latest`（E-126）。
 *    现在是一次 `GET /reports?scope=latest&limit=6&includeActive=1&withCounts=1`。
 *
 * ★P1-5：这里**必须** `withCounts=1` —— 底部「查看全部 N 份报告」用的是 `counts.all`，
 * 而它是唯一能给出"全库总份数"的出口（`total` 在 `scope=latest` 下等于"有报告的标的数"）。
 * 逃生阀是 `staleTime` + 骨架 + `limit`，不是关掉计数。
 */

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';

import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { relativeTime } from '@/lib/format';
import { libraryHref } from '@/lib/library';
import type { LibraryResponse, LibraryScope, Stance } from '@/lib/types';

import { Badge, type BadgeTone } from '../ui/Badge';
import { Skeleton } from '../ui/Skeleton';

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

export interface RecentReportsProps {
  initialData: LibraryResponse;
  initialDataUpdatedAt: number;
  /** `features.reportLibrary`：false 时整块不渲染（不留死链）。 */
  enabled?: boolean;
}

export const RECENT_REPORTS_KEY = ['reports', 'library', 'latest', 6] as const;

export function RecentReports({ initialData, initialDataUpdatedAt, enabled = true }: RecentReportsProps) {
  const query = useQuery({
    queryKey: RECENT_REPORTS_KEY,
    queryFn: () =>
      api.reportLibrary({ scope: 'latest' as LibraryScope, limit: 6, includeActive: true, withCounts: true }),
    initialData,
    // ★P2-10：与 `initialData` 成对，否则挂载瞬间会再请求一次（AC-137 会红）。
    initialDataUpdatedAt,
    staleTime: 30_000,
    enabled,
  });

  if (!enabled) return null;

  const items = query.data?.items ?? [];
  const active = query.data?.active ?? [];
  const total = query.data?.counts?.all ?? query.data?.total ?? 0;

  return (
    <section className="mt-10" aria-labelledby="recent-reports-heading">
      <div className="mb-2 flex items-baseline justify-between">
        <h2
          id="recent-reports-heading"
          className="text-[11px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]"
        >
          {copy.landing.recentReports}
        </h2>
        {active.length > 0 ? (
          <span aria-live="polite" className="text-[10.5px] text-[var(--accent)]">
            {copy.library.generating} {active.length}
          </span>
        ) : null}
      </div>

      {query.isPending ? (
        <div className="space-y-1.5">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-[46px] w-full motion-reduce:animate-none" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--border-subtle)] px-3 py-6 text-center">
          <p className="text-[12px] text-[var(--text-secondary)]">{copy.landing.recentReportsEmpty}</p>
          <p className="mt-1 text-[11px] text-[var(--text-tertiary)]">{copy.landing.recentReportsEmptyBody}</p>
        </div>
      ) : (
        <ul className="space-y-1.5">
          {active.map((run) => (
            <li key={run.runId}>
              <Link
                href={`/${encodeURIComponent(run.symbol)}`}
                className="flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--accent)]/25 bg-[var(--accent-weak)] px-3 py-2 text-[11.5px] text-[var(--accent)]"
              >
                <span className="truncate">
                  {copy.library.generating} {run.name || run.symbol}
                  {run.stage ? ` · ${run.stage}` : ''} {run.pct}%
                </span>
                <ArrowRight className="ml-auto size-3 shrink-0" aria-hidden="true" />
              </Link>
            </li>
          ))}
          {items.map((item) => {
            const stance = item.report.stance ?? 'watch';
            const badge =
              item.report.provisional
                ? copy.report.provisional
                : item.report.status === 'degraded'
                  ? copy.report.degraded
                  : item.report.quality?.source === 'mock'
                    ? copy.report.mock
                    : null;
            return (
              <li key={item.report.id}>
                <Link
                  href={libraryHref(item.instrument.symbol, item.report.id)}
                  data-report-id={item.report.id}
                  className={cn(
                    'group flex items-center gap-2.5 rounded-[var(--radius-md)] border border-[var(--border-subtle)]',
                    'bg-[var(--bg-surface-1)] px-3 py-2.5 transition-colors',
                    'hover:border-[var(--border-strong)] hover:bg-[var(--bg-surface-2)]',
                  )}
                >
                  <span aria-hidden="true" className={cn('h-6 w-[2px] shrink-0 rounded-full', STANCE_STRIPE[stance])} />
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
                      title={item.report.headline || item.report.title}
                    >
                      {item.report.headline || item.report.title}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5 text-[10.5px] text-[var(--text-tertiary)]">
                    <span className="mono">v{item.report.version}</span>
                    <span>{relativeTime(item.report.generatedAt)}</span>
                    {item.report.groundedRatio != null ? (
                      <span className="mono">{Math.round(item.report.groundedRatio * 100)}%</span>
                    ) : null}
                    {badge ? <Badge tone="warn">{badge}</Badge> : null}
                    <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <div className="mt-2 flex justify-end">
        <Link
          href="/reports?scope=all"
          className="inline-flex items-center gap-1 text-[11.5px] text-[var(--accent)] underline-offset-2 hover:underline"
        >
          {copy.landing.viewAllReports.replace('{total}', String(total))}
          <ArrowRight className="size-3" aria-hidden="true" />
        </Link>
      </div>
    </section>
  );
}
