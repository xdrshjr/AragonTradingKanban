'use client';

import { forwardRef, type TextareaHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

/** 多行输入，外观与 `Input` 对齐。 */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, invalid, rows = 3, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      data-input-field=""
      aria-invalid={invalid || undefined}
      className={cn(
        'w-full resize-y rounded-[var(--radius-sm)] border bg-[var(--bg-surface-1)] px-2.5 py-2 text-[13px] leading-relaxed',
        'border-[var(--border-strong)] text-[var(--text-primary)] outline-none transition-colors duration-[var(--dur-fast)]',
        'placeholder:text-[var(--text-tertiary)]',
        'focus:border-[var(--accent)]/60 focus:ring-2 focus:ring-[var(--accent-ring)]/40',
        invalid && 'border-[var(--danger)]/60',
        className,
      )}
      {...props}
    />
  );
});
