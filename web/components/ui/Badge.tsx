import { cn } from '@/lib/cn';

export type BadgeTone = 'neutral' | 'accent' | 'info' | 'warn' | 'danger' | 'up' | 'down';

const TONES: Record<BadgeTone, string> = {
  neutral: 'border-[var(--border-strong)] text-[var(--text-secondary)] bg-[var(--bg-surface-2)]',
  accent: 'border-[var(--accent)]/35 text-[var(--accent)] bg-[var(--accent-weak)]',
  info: 'border-[var(--info)]/35 text-[var(--info)] bg-[var(--info-weak)]',
  warn: 'border-[var(--warn)]/40 text-[var(--warn)] bg-[var(--warn-weak)]',
  danger: 'border-[var(--danger)]/40 text-[var(--danger)] bg-[var(--danger-weak)]',
  up: 'border-[var(--chart-up)]/40 text-[var(--chart-up)] bg-[var(--chart-up)]/10',
  down: 'border-[var(--chart-down)]/40 text-[var(--chart-down)] bg-[var(--chart-down)]/10',
};

export interface BadgeProps {
  tone?: BadgeTone;
  className?: string;
  children: React.ReactNode;
  title?: string;
  icon?: React.ReactNode;
}

export function Badge({ tone = 'neutral', className, children, title, icon }: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-px text-[10.5px] font-medium leading-[16px]',
        'whitespace-nowrap',
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** Square-ish tag used for metadata rows (model, provider, tokens). */
export function Tag({ className, children, title }: { className?: string; children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--border-subtle)]',
        'px-1.5 py-px text-[10.5px] leading-[16px] text-[var(--text-tertiary)]',
        className,
      )}
    >
      {children}
    </span>
  );
}
