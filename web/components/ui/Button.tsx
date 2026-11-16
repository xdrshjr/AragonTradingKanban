'use client';

import { forwardRef, type ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'accent-ghost';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  block?: boolean;
  leadingIcon?: React.ReactNode;
  trailingIcon?: React.ReactNode;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-[var(--accent)] text-[var(--accent-text)] hover:brightness-110 active:brightness-95 shadow-[0_1px_0_rgba(255,255,255,.12)_inset]',
  secondary:
    'bg-[var(--bg-surface-2)] text-[var(--text-primary)] border border-[var(--border-strong)] hover:bg-[var(--bg-surface-3)]',
  ghost: 'bg-transparent text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
  danger: 'bg-[var(--danger-weak)] text-[var(--danger)] border border-[var(--danger)]/40 hover:bg-[var(--danger)]/20',
  'accent-ghost':
    'bg-[var(--accent-weak)] text-[var(--accent)] border border-[var(--accent)]/30 hover:bg-[var(--accent)]/20',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-[12px] gap-1.5 rounded-[var(--radius-sm)]',
  md: 'h-8 px-3 text-[13px] gap-2 rounded-[var(--radius-sm)]',
  lg: 'h-10 px-4 text-[14px] gap-2 rounded-[var(--radius-md)]',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'md', loading = false, block = false, leadingIcon, trailingIcon, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={props.type ?? 'button'}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium',
        'transition-[background-color,color,filter,opacity] duration-[var(--dur-fast)]',
        'disabled:cursor-not-allowed disabled:opacity-45',
        VARIANTS[variant],
        SIZES[size],
        block && 'w-full',
        className,
      )}
      {...props}
    >
      {loading ? <Spinner /> : leadingIcon}
      {children}
      {!loading && trailingIcon}
    </button>
  );
});

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-3.5 shrink-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent',
        className,
      )}
    />
  );
}

export const IconButton = forwardRef<HTMLButtonElement, ButtonProps & { label: string }>(function IconButton(
  { label, className, children, ...props },
  ref,
) {
  return (
    <Button
      ref={ref}
      variant="ghost"
      size="sm"
      aria-label={label}
      title={label}
      className={cn('size-7 !px-0', className)}
      {...props}
    >
      {children}
    </Button>
  );
});
