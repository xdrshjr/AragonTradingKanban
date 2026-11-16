'use client';

import { GitCompareArrows } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';

export interface DiffBadgeProps {
  /** how many structural changes the newest version has vs the previous one */
  count: number;
  active?: boolean;
  onClick?: () => void;
  className?: string;
  /** AC-36: the element `CompareView` returns focus to when it closes. */
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
}

/**
 * 「与上一版比较」入口徽标（A3，spec §18.3.6 / AC-34）。
 *
 * 只做一件事：把"最新版相对上一版有几处结构性变化"这个数字摆在报告头里，
 * 点一下进入 CompareView。数字为 0 时不显示「0 处」——那是噪声，改成动作文案，
 * 让读者知道这里能点。
 *
 * 没有 `onClick` 时降级为 `<span>`：一个点了没反应的按钮比一段静态文本更糟
 * （键盘用户会停在一个无操作的焦点上）。
 */
export function DiffBadge({ count, active = false, onClick, className, triggerRef }: DiffBadgeProps) {
  const changes = Number.isFinite(count) ? Math.max(0, Math.trunc(count)) : 0;
  const label = changes > 0 ? fill(copy.compare.blocksCount, { count: changes }) : copy.compare.open;
  // 可访问名是"版本比较 · 3 处"，完整包含可见文本（WCAG 2.5.3 label-in-name）
  const accessibleName = changes > 0 ? `${copy.compare.title} · ${label}` : copy.compare.open;

  const classes = cn(
    'inline-flex h-7 select-none items-center gap-1 rounded-full border px-2 text-[11px] font-medium leading-none',
    'transition-colors duration-[var(--dur-fast)]',
    active
      ? 'border-[var(--accent)]/35 bg-[var(--accent-weak)] text-[var(--accent)]'
      : 'border-[var(--border-subtle)] bg-[var(--bg-surface-2)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:bg-[var(--bg-surface-3)] hover:text-[var(--text-primary)]',
    className,
  );

  const content = (
    <>
      <GitCompareArrows className="size-3" aria-hidden="true" />
      <span className={cn('num', changes > 0 && 'tracking-tight')}>{label}</span>
    </>
  );

  if (!onClick) {
    return (
      <span className={classes} title={copy.compare.open}>
        {content}
      </span>
    );
  }

  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={accessibleName}
      title={copy.compare.open}
      className={classes}
    >
      {content}
    </button>
  );
}
