import { cn } from '@/lib/cn';

export type StatusKind = 'active' | 'disabled' | 'locked' | 'pending' | 'danger';

const DOT: Record<StatusKind, string> = {
  active: 'bg-[var(--accent)]',
  disabled: 'bg-[var(--text-tertiary)]',
  locked: 'bg-[var(--warn)]',
  pending: 'bg-[var(--info)]',
  danger: 'bg-[var(--danger)]',
};

export interface StatusDotProps {
  status: StatusKind;
  /** 旁带文字——**不靠颜色单独传达信息**（§24.3.15e）。 */
  label: string;
  className?: string;
  /** 只显示点，文字仍保留给读屏器。 */
  dotOnly?: boolean;
}

/** 状态点：active＝accent、disabled＝tertiary、locked＝warn；文字永远在场。 */
export function StatusDot({ status, label, className, dotOnly = false }: StatusDotProps) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-[11.5px] text-[var(--text-secondary)]', className)}>
      <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', DOT[status])} />
      <span className={dotOnly ? 'sr-only' : undefined}>{label}</span>
    </span>
  );
}
