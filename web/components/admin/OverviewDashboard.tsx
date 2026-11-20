'use client';

import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import Link from 'next/link';

import { adminApi } from '@/lib/api.admin';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { relativeTime } from '@/lib/format';
import type { AdminOverview } from '@/lib/types';

import { Skeleton } from '../ui/Skeleton';
import { AdminCard, AdminPageHeader, EmptyState, ErrorState } from './AdminPrimitives';
import { auditActionLabel } from './auditLabels';
import { adminKeys } from './keys';
import { OverviewCards, OverviewCardsSkeleton } from './OverviewCards';
import { SecurityEvents } from './SecurityEvents';

const t = copyAdmin.overview;

// echarts 只在概览页的这个 async chunk 里；工作台首屏与其它后台页都不为它付费。
const TrendChart = dynamic(() => import('./TrendChart'), {
  ssr: false,
  loading: () => <Skeleton className="h-[220px] w-full" />,
});

function ChartTable({ data }: { data: AdminOverview }) {
  const runsByDay = new Map(data.runsDaily.map((point) => [point.day, point.count]));
  return (
    <table className="sr-only">
      <caption>{t.chart.tableCaption}</caption>
      <thead>
        <tr>
          <th scope="col">{t.chart.colDay}</th>
          <th scope="col">{t.chart.signups}</th>
          <th scope="col">{t.chart.runs}</th>
        </tr>
      </thead>
      <tbody>
        {data.signups.map((point) => (
          <tr key={point.day}>
            <th scope="row">{point.day}</th>
            <td>{point.count}</td>
            <td>{runsByDay.get(point.day) ?? 0}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TopUsers({ data }: { data: AdminOverview }) {
  const max = Math.max(1, ...data.topUsers7d.map((user) => user.runs));
  return (
    <AdminCard title={t.topUsers.title} headingId="ov-top-users">
      {data.topUsers7d.length === 0 ? (
        <p className="py-4 text-[12px] text-[var(--text-tertiary)]">{t.topUsers.empty}</p>
      ) : (
        <ol className="space-y-2.5">
          {data.topUsers7d.map((user, index) => (
            <li key={user.userId} className="flex items-center gap-3 text-[12.5px]">
              <span className="num w-4 shrink-0 text-right text-[11px] text-[var(--text-tertiary)]">{index + 1}</span>
              <span className="mono min-w-0 flex-1 truncate text-[var(--text-primary)]">{user.username ?? copyAdmin.common.emDash}</span>
              <span
                aria-hidden="true"
                className="hidden h-1 w-24 overflow-hidden rounded-full bg-[var(--bg-surface-3)] sm:block"
              >
                <span
                  className="block h-full rounded-full bg-[var(--accent)]"
                  style={{ width: `${Math.max(6, Math.round((user.runs / max) * 100))}%` }}
                />
              </span>
              <span className="num w-12 shrink-0 text-right text-[var(--text-secondary)]">
                {fill(t.topUsers.runs, { n: user.runs })}
              </span>
            </li>
          ))}
        </ol>
      )}
    </AdminCard>
  );
}

function RecentAudit({ data }: { data: AdminOverview }) {
  return (
    <AdminCard
      title={t.recentAudit.title}
      headingId="ov-recent-audit"
      aside={
        <Link
          href="/admin/audit"
          className="text-[11.5px] text-[var(--text-tertiary)] underline-offset-2 transition-colors hover:text-[var(--text-primary)] hover:underline"
        >
          {t.recentAudit.viewAll}
        </Link>
      }
    >
      {data.recentAudit.length === 0 ? (
        <p className="py-4 text-[12px] text-[var(--text-tertiary)]">{t.recentAudit.empty}</p>
      ) : (
        <ul className="divide-y divide-[var(--border-subtle)]">
          {data.recentAudit.slice(0, 8).map((row) => (
            <li key={row.id} className="flex items-baseline gap-2 py-1.5 text-[12px]">
              <span className="min-w-0 flex-1 truncate">
                <span className="mono text-[var(--text-secondary)]">{row.actorName ?? copyAdmin.audit.system}</span>
                <span className="mx-1.5 text-[var(--text-primary)]">{auditActionLabel(row.action)}</span>
                {row.targetName ? <span className="mono text-[var(--text-tertiary)]">{row.targetName}</span> : null}
              </span>
              <time dateTime={row.at} className="shrink-0 text-[11px] text-[var(--text-tertiary)]">
                {relativeTime(row.at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </AdminCard>
  );
}

/** 概览页：一次 `GET /admin/overview` 喂 6 张卡 + 双折线 + Top 5 + 最近审计。 */
export function OverviewDashboard() {
  const query = useQuery({
    queryKey: adminKeys.overview,
    queryFn: () => adminApi.adminOverview(),
    refetchOnWindowFocus: true,
    staleTime: 15_000,
  });
  const data = query.data;

  return (
    <div>
      <AdminPageHeader title={t.title} subtitle={t.subtitle} />

      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !data ? (
        <div className="space-y-4">
          <OverviewCardsSkeleton />
          <Skeleton className="h-[280px] w-full" />
        </div>
      ) : (
        <div className="space-y-4">
          <OverviewCards data={data} />

          {data.security24h ? <SecurityEvents data={data.security24h} /> : null}

          <AdminCard
            title={t.chart.title}
            headingId="ov-chart"
            aside={<span className="text-[11px] text-[var(--text-tertiary)]">{t.chart.subtitle}</span>}
          >
            {data.signups.length === 0 && data.runsDaily.length === 0 ? (
              <EmptyState title={t.chart.empty} />
            ) : (
              <>
                <TrendChart signups={data.signups} runs={data.runsDaily} />
                <ChartTable data={data} />
              </>
            )}
            <p className="mt-2 text-[11px] text-[var(--text-tertiary)]">
              {fill(t.quotaToday, { used: data.quota.consumedToday, refunded: data.quota.refundedToday })}
            </p>
          </AdminCard>

          <div className="grid gap-4 min-[900px]:grid-cols-2">
            <TopUsers data={data} />
            <RecentAudit data={data} />
          </div>
        </div>
      )}
    </div>
  );
}
