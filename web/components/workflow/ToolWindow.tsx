'use client';

import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';

export interface ToolWindowProps {
  title: string;
  icon?: ReactNode;
  /** 标题条右侧的图标按钮（选项 ⚙ 等），由调用方渲染。 */
  actions?: ReactNode;
  /** 收起时只剩 28px 标题条（正文不渲染）。缺省 = 展开。 */
  collapsed?: boolean;
  /** 提供后标题条左侧出现展开/收起箭头，且点击标题可切换。 */
  onToggle?: () => void;
  /** 区域标签（读屏器用）；缺省取 `title`。 */
  label?: string;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}

/**
 * PyCharm 式工具窗外壳（spec §3.10.3）：28px 标题条（名称 + 图标按钮）、1px 分隔线、可收起。
 * 结构 / 属性 / 问题 / AI 助手共用；本组件**不持有状态**，收起与否由调用方（store 的 `panes`）决定。
 */
export function ToolWindow({
  title,
  icon,
  actions,
  collapsed = false,
  onToggle,
  label,
  className,
  bodyClassName,
  children,
}: ToolWindowProps) {
  const Chevron = collapsed ? ChevronRight : ChevronDown;
  return (
    <section
      aria-label={label ?? title}
      data-collapsed={collapsed || undefined}
      className={cn(
        'flex min-h-0 min-w-0 flex-col bg-[var(--bg-surface-1)] text-[var(--text-primary)]',
        collapsed ? 'h-7 shrink-0' : 'h-full',
        className,
      )}
    >
      <header className="flex h-7 shrink-0 items-center gap-1.5 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-2">
        {onToggle ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={`${collapsed ? '展开' : '收起'}${title}`}
            className="flex min-w-0 flex-1 items-center gap-1.5 rounded-[var(--radius-sm)] text-left outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)]"
          >
            <Chevron className="size-3 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
            {icon ? <span className="shrink-0 text-[var(--text-tertiary)]">{icon}</span> : null}
            <span className="truncate text-[12px] font-medium">{title}</span>
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            {icon ? <span className="shrink-0 text-[var(--text-tertiary)]">{icon}</span> : null}
            <span className="truncate text-[12px] font-medium">{title}</span>
          </div>
        )}
        {actions ? <div className="flex shrink-0 items-center gap-0.5">{actions}</div> : null}
      </header>
      {collapsed ? null : <div className={cn('min-h-0 flex-1 overflow-auto', bodyClassName)}>{children}</div>}
    </section>
  );
}

/** 标题条里的图标按钮（统一 20px 命中区与焦点环）。 */
export function ToolWindowButton({
  label,
  onClick,
  children,
  pressed,
  disabled,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  pressed?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-5 items-center justify-center rounded-[var(--radius-sm)] text-[var(--text-tertiary)] outline-none transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)] disabled:opacity-40"
    >
      {children}
    </button>
  );
}
