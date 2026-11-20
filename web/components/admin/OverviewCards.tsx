import Link from 'next/link';

import { cn } from '@/lib/cn';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import type { AdminOverview } from '@/lib/types';

import { Skeleton } from '../ui/Skeleton';

const t = copyAdmin.overview.cards;

interface CardSpec {
  key: string;
  label: string;
  value: string;
  hint: string;
  /** 数字需要引起注意时转 `--warn`（文字永远在，不靠颜色）。 */
  attention?: boolean;
  href?: string;
}

function weekSignups(signups: AdminOverview['signups']): number {
  return signups.slice(-7).reduce((sum, point) => sum + point.count, 0);
}

export function buildOverviewCards(data: AdminOverview): CardSpec[] {
  const { users, runs, pendingQuotaRequests } = data;
  const rate = runs.successRate;
  return [
    { key: 'total', label: t.total.label, value: String(users.total), hint: t.total.hint },
    { key: 'new7d', label: t.newWeek.label, value: String(weekSignups(data.signups)), hint: t.newWeek.hint },
    { key: 'runs', label: t.runsToday.label, value: String(runs.today), hint: t.runsToday.hint },
    {
      key: 'rate',
      label: t.successRate.label,
      value: rate === null || rate === undefined ? copyAdmin.common.emDash : `${Math.round(rate * 100)}%`,
      hint:
        rate === null || rate === undefined
          ? t.successRate.empty
          : fill(t.successRate.hint, { ok: runs.succeeded, fail: runs.failed }),
    },
    {
      key: 'pending',
      label: t.pending.label,
      value: String(pendingQuotaRequests),
      hint: pendingQuotaRequests > 0 ? t.pending.hint : t.pending.hintNone,
      attention: pendingQuotaRequests > 0,
      href: pendingQuotaRequests > 0 ? '/admin/quota' : undefined,
    },
    {
      key: 'locked',
      label: t.locked.label,
      value: String(users.locked),
      hint: users.locked > 0 ? t.locked.hint : t.locked.hintNone,
      attention: users.locked > 0,
      // ★Y2(c)：不再是死数字——跳到“仅看已锁定账号”的用户表。
      href: users.locked > 0 ? '/admin/users?locked=1' : undefined,
    },
  ];
}

const CARD =
  'block rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-4 py-3.5';

/** 6 张指标卡。数字来自一次 `GET /admin/overview`，这里不做任何推断。 */
export function OverviewCards({ data }: { data: AdminOverview }) {
  const cards = buildOverviewCards(data);
  return (
    <ul aria-label={copyAdmin.overview.title} className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-3">
      {cards.map((card) => {
        const body = (
          <>
            <div className="text-[11.5px] text-[var(--text-tertiary)]">{card.label}</div>
            <div
              className={cn(
                'num mt-1 text-[26px] font-semibold leading-tight tracking-tight',
                card.attention ? 'text-[var(--warn)]' : 'text-[var(--text-primary)]',
              )}
            >
              {card.value}
            </div>
            <div className="mt-1 text-[11px] text-[var(--text-tertiary)]">{card.hint}</div>
          </>
        );
        return (
          <li key={card.key} data-testid={`overview-card-${card.key}`}>
            {card.href ? (
              <Link href={card.href} className={cn(CARD, 'transition-colors hover:bg-[var(--bg-surface-2)]')}>
                {body}
              </Link>
            ) : (
              <div className={CARD}>{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function OverviewCardsSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={copyAdmin.common.loading}
      className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-3"
    >
      {Array.from({ length: 6 }).map((_, index) => (
        <div
          key={index}
          className="rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-4 py-3.5"
        >
          <Skeleton className="h-3 w-20" />
          <Skeleton className="mt-2.5 h-7 w-16" />
          <Skeleton className="mt-2 h-2.5 w-28" />
        </div>
      ))}
    </div>
  );
}
