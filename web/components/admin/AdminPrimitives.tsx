'use client';

import { Check, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { describeAuthError } from '@/lib/authErrorText';
import { cn } from '@/lib/cn';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';

import { Button } from '../ui/Button';
import { Skeleton } from '../ui/Skeleton';
import { notify } from '../ui/Toast';

const t = copyAdmin.common;

/** 页面标题区：一个 `h1`、一句话副标题、右侧主操作。 */
export function AdminPageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">{title}</h1>
        {subtitle ? <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--text-tertiary)]">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function AdminCard({
  title,
  aside,
  children,
  className,
  headingId,
}: {
  title?: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  headingId?: string;
}) {
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cn(
        'rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] p-4',
        className,
      )}
    >
      {title ? (
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 id={headingId} className="text-[12.5px] font-semibold tracking-tight text-[var(--text-primary)]">
            {title}
          </h2>
          {aside}
        </div>
      ) : null}
      {children}
    </section>
  );
}

/** 错误态：一句人话 + 带 `requestId` 的排障编号 + 重试（§24.3.15d）。 */
export function ErrorState({
  error,
  title,
  onRetry,
  className,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  className?: string;
}) {
  const view = describeAuthError(error);
  return (
    <div
      role="alert"
      className={cn(
        'rounded-[var(--radius-md)] border border-[var(--danger)]/35 bg-[var(--danger-weak)] px-4 py-3 text-[12.5px]',
        className,
      )}
    >
      <div className="font-medium text-[var(--danger)]">{title ?? t.loadFailed}</div>
      <div className="mt-0.5 text-[var(--text-secondary)]">
        {view.message}
        {view.hint ? ` ${view.hint}` : ''}
      </div>
      <div className="mt-2 flex items-center gap-3">
        {view.requestId ? (
          <span className="mono text-[10.5px] text-[var(--text-tertiary)]">
            {fill(t.withRequestId, { id: view.requestId })}
          </span>
        ) : null}
        {onRetry ? (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            {copyAdmin.retry}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** 空态：一句话 + 下一步（有主操作就放主操作）。 */
export function EmptyState({
  title,
  hint,
  action,
  className,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-1 px-4 py-10 text-center', className)}>
      <p className="text-[13px] font-medium text-[var(--text-secondary)]">{title}</p>
      {hint ? <p className="max-w-[420px] text-[12px] leading-relaxed text-[var(--text-tertiary)]">{hint}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

/** 表格骨架：`aria-busy` 的整体 + 装饰性的灰条（沿用 `Skeleton`）。 */
export function TableSkeleton({ rows = 6, label = t.loading }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="divide-y divide-[var(--border-subtle)]">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-4 px-3 py-3">
          <Skeleton className="size-7 shrink-0 rounded-full" />
          <Skeleton className="h-3 w-32" />
          <Skeleton className="ml-auto h-3 w-20" />
          <Skeleton className="hidden h-3 w-24 min-[900px]:block" />
        </div>
      ))}
    </div>
  );
}

/** 复制按钮：成功后 1.6s 内显示“已复制”（文字 + 图标，不靠颜色）。 */
export function CopyButton({
  value,
  label,
  className,
  size = 'sm',
}: {
  value: string;
  /** 读屏器用的完整名称，如“复制邀请码 K7QM-X2PD”。 */
  label?: string;
  className?: string;
  size?: 'sm' | 'md';
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      notify.error(t.copyFailed);
    }
  };

  return (
    <Button
      size={size}
      variant="secondary"
      aria-label={label ?? t.copy}
      onClick={() => void onCopy()}
      leadingIcon={
        copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />
      }
      className={className}
    >
      <span aria-live="polite">{copied ? t.copied : t.copy}</span>
    </Button>
  );
}

/** 只出现一次的秘密（临时密码 / 新邀请码）：等宽、可选中、带复制。 */
export function SecretBox({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] p-3">
      <div className="mb-1.5 text-[11px] text-[var(--text-tertiary)]">{label}</div>
      <div className="flex items-center justify-between gap-3">
        <code
          data-testid="secret-value"
          className="mono select-all break-all text-[15px] font-medium tracking-wide text-[var(--text-primary)]"
        >
          {value}
        </code>
        <CopyButton value={value} label={`${t.copy}${label}`} />
      </div>
    </div>
  );
}

/** 表格里的“用户名 + 显示名”两行。 */
export function UserCell({ username, displayName }: { username: string; displayName?: string | null }) {
  return (
    <span className="flex min-w-0 flex-col leading-tight">
      <span className="mono truncate text-[12.5px] font-medium text-[var(--text-primary)]">{username}</span>
      {displayName ? <span className="truncate text-[11px] text-[var(--text-tertiary)]">{displayName}</span> : null}
    </span>
  );
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--bg-surface-3)] text-[11px] font-semibold uppercase text-[var(--text-primary)]',
        className,
      )}
    >
      {name.slice(0, 1)}
    </span>
  );
}

/** 用户状态点：locked 优先于 active（被锁的账号 status 仍是 active）。 */
export function userStatusKind(user: { status: string; locked: boolean }): 'active' | 'disabled' | 'locked' | 'danger' {
  if (user.status === 'deleted') return 'danger';
  if (user.status === 'disabled') return 'disabled';
  if (user.locked) return 'locked';
  return 'active';
}

export function userStatusLabel(user: { status: string; locked: boolean }): string {
  const kind = userStatusKind(user);
  if (kind === 'danger') return t.status.deleted;
  return t.status[kind];
}
