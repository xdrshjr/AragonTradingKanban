'use client';

import Link from 'next/link';
import { useId } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { useAuthUi, useMe } from '@/lib/auth';
import { bucketView, chipText, tooltipText } from '@/lib/quota';

export interface QuotaChipProps {
  className?: string;
  /** 默认对"不限次"账号不渲染（头部保持安静）；账号页传 `true`。 */
  showUnlimited?: boolean;
}

const TONES = {
  neutral: 'border-[var(--border-strong)] bg-[var(--bg-surface-2)] text-[var(--text-secondary)]',
  warn: 'border-[var(--warn)]/40 bg-[var(--warn-weak)] text-[var(--warn)]',
  danger: 'border-[var(--danger)]/40 bg-[var(--danger-weak)] text-[var(--danger)]',
} as const;

/**
 * 额度芯片：`今日剩余 2/3`（≤1 转 `--warn`，0 转 `--danger` 并变成"打开额度对话框"的按钮）。
 *
 * 数字全部来自 `me.quota.report`（服务端账本推出的权威余额），这里不做任何自己的推断。
 * 悬停 / 聚焦提示说清口径：日额度 + 赠送 + 重置时间。**不只靠颜色**：文字永远在。
 *
 * ★为什么提示是纯 CSS（`group-hover` / `group-focus-within`）而不是 `ui/Tooltip`：芯片是首屏静态代码，
 * 而 Radix Tooltip 会把 popper + floating-ui（约 130 kB 源码）拖进首屏 chunk——实测 `/[symbol]`
 * 首屏 gzip +20 kB，直接顶破 AC-35 / AC-202。提示内容同时挂在 `aria-describedby` 上，读屏器不依赖悬停。
 */
export function QuotaChip({ className, showUnlimited = false }: QuotaChipProps) {
  const tipId = useId();
  const me = useMe();
  const openQuota = useAuthUi((state) => state.openQuota);
  const bucket = me.quota?.report ?? null;
  const view = bucketView(bucket);
  if (!me.authenticated || !bucket || !view) return null;
  if (view.unlimited && !showUnlimited) return null;

  const text = chipText(view);
  const tip = tooltipText(view, bucket.resetAt, me.quota?.tz);
  const tone = view.exhausted ? 'danger' : view.low ? 'warn' : 'neutral';
  const classes = cn(
    'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11.5px] font-medium leading-none whitespace-nowrap',
    'transition-colors duration-[var(--dur-fast)] hover:brightness-110',
    TONES[tone],
    className,
  );
  const label = `${copy.quota.chipLabel}：${text}。${tip}`;
  const props = { 'data-testid': 'quota-chip', 'aria-label': label, 'aria-describedby': tipId, className: classes };
  const body = (
    <>
      <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-current opacity-80" />
      <span className="num">{text}</span>
    </>
  );

  return (
    <span className="group relative inline-flex">
      {view.exhausted ? (
        <button type="button" aria-haspopup="dialog" onClick={() => openQuota(null)} {...props}>
          {body}
        </button>
      ) : (
        <Link href="/account" {...props}>
          {body}
        </Link>
      )}
      <span
        id={tipId}
        role="tooltip"
        className="pointer-events-none absolute right-0 top-full z-40 mt-1.5 hidden w-max max-w-[260px] rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-3)] px-2 py-1 text-[11.5px] leading-relaxed text-[var(--text-secondary)] shadow-[var(--shadow-pop)] group-focus-within:block group-hover:block"
      >
        {tip}
      </span>
    </span>
  );
}
