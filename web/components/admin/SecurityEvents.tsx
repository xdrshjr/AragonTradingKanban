import Link from 'next/link';

import { cn } from '@/lib/cn';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import type { AdminSecurity24h } from '@/lib/types';

import { AdminCard } from './AdminPrimitives';

const t = copyAdmin.overview.security;

interface Tile {
  key: keyof typeof t.items;
  action: string;
  value: number;
  /** 失败 / 锁定类：非零时用 `--danger`（文字永远在，不靠颜色单独表达）。 */
  alarm: boolean;
  hint: string;
  capped?: boolean;
}

export function buildSecurityTiles(s: AdminSecurity24h): Tile[] {
  return [
    {
      key: 'loginFailures',
      action: 'auth.login.failure',
      value: s.loginFailures,
      alarm: true,
      hint: fill(t.items.loginFailures.hint, { bursts: s.loginFailuresCapped ? fill(t.capped, { n: 5000 }) : s.loginFailureBursts }),
      capped: s.loginFailuresCapped,
    },
    { key: 'lockouts', action: 'auth.login.lockout', value: s.lockouts, alarm: true, hint: t.items.lockouts.hint },
    {
      key: 'recoveryFailures',
      action: 'auth.recovery.failure',
      value: s.recoveryFailures,
      alarm: true,
      hint: t.items.recoveryFailures.hint,
    },
    { key: 'recoveries', action: 'auth.password.recover', value: s.recoveries, alarm: false, hint: t.items.recoveries.hint },
    { key: 'selfDeletes', action: 'user.self_delete', value: s.selfDeletes, alarm: false, hint: t.items.selfDeletes.hint },
    { key: 'exports', action: 'admin.export', value: s.exports, alarm: false, hint: t.items.exports.hint },
  ];
}

/**
 * ★Y5(c) 安全事件（24 小时滚动窗口）。失败类数字是**尝试次数**（后端对合并行求 `Σ detail.count`），
 * 不是审计行数；达到 5000 组上限时显示 “5000+ 组”。非零的数字是链接，跳到按该动作筛选的审计页。
 */
export function SecurityEvents({ data }: { data: AdminSecurity24h }) {
  const tiles = buildSecurityTiles(data);
  const quiet = tiles.every((tile) => tile.value === 0);
  return (
    <AdminCard
      title={t.title}
      headingId="ov-security"
      aside={<span className="text-[11px] text-[var(--text-tertiary)]">{t.subtitle}</span>}
    >
      <ul aria-label={t.title} data-testid="security-events" className="grid grid-cols-2 gap-2.5 min-[900px]:grid-cols-6">
        {tiles.map((tile) => {
          const item = t.items[tile.key];
          const href = tile.value > 0 ? `/admin/audit?action=${encodeURIComponent(tile.action)}` : null;
          const body = (
            <>
              <div className="text-[11px] text-[var(--text-tertiary)]">{item.label}</div>
              <div
                className={cn(
                  'num mt-0.5 text-[20px] font-semibold leading-tight tracking-tight',
                  tile.value > 0 && tile.alarm ? 'text-[var(--danger)]' : 'text-[var(--text-primary)]',
                )}
              >
                {tile.value}
                {tile.capped ? '+' : ''}
              </div>
              <div className="mt-0.5 text-[10.5px] leading-snug text-[var(--text-tertiary)]">{tile.hint}</div>
            </>
          );
          const base = 'block h-full rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-3 py-2.5';
          return (
            <li key={tile.key} data-testid={`security-${tile.key}`}>
              {href ? (
                <Link href={href} className={cn(base, 'transition-colors hover:bg-[var(--bg-surface-3)]')}>
                  {body}
                </Link>
              ) : (
                <div className={base}>{body}</div>
              )}
            </li>
          );
        })}
      </ul>
      {quiet ? <p className="mt-2 text-[11.5px] text-[var(--text-tertiary)]">{t.none}</p> : null}
    </AdminCard>
  );
}
