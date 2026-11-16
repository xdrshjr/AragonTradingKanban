'use client';

import { forwardRef, type InputHTMLAttributes, useId, useRef } from 'react';

import { cn } from '@/lib/cn';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  leadingIcon?: React.ReactNode;
  trailingSlot?: React.ReactNode;
  invalid?: boolean;
  inputSize?: 'sm' | 'md';
  onClear?: () => void;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, leadingIcon, trailingSlot, invalid, inputSize = 'md', onClear, value, ...props },
  ref,
) {
  const showClear = Boolean(onClear) && typeof value === 'string' && value.length > 0;
  return (
    <div
      className={cn(
        'group relative flex items-center gap-2 rounded-[var(--radius-sm)] border bg-[var(--bg-surface-1)]',
        'border-[var(--border-strong)] transition-colors duration-[var(--dur-fast)]',
        'focus-within:border-[var(--accent)]/60 focus-within:ring-2 focus-within:ring-[var(--accent-ring)]/40',
        invalid && 'border-[var(--danger)]/60',
        inputSize === 'sm' ? 'h-7 px-2' : 'h-9 px-2.5',
        className,
      )}
    >
      {leadingIcon ? (
        <span aria-hidden="true" className="shrink-0 text-[var(--text-tertiary)]">
          {leadingIcon}
        </span>
      ) : null}
      <input
        ref={ref}
        value={value}
        aria-invalid={invalid || undefined}
        className={cn(
          'min-w-0 flex-1 bg-transparent text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]',
          inputSize === 'sm' ? 'text-[12px]' : 'text-[13px]',
        )}
        {...props}
      />
      {showClear ? (
        <button
          type="button"
          onClick={onClear}
          aria-label="清除"
          className="shrink-0 rounded p-0.5 text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
        >
          <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
          </svg>
        </button>
      ) : null}
      {trailingSlot}
    </div>
  );
});

export function useInputId(provided?: string): string {
  const generated = useId();
  const ref = useRef(provided ?? generated);
  return ref.current;
}
