'use client';

import { ChevronDown } from 'lucide-react';
import { forwardRef, type SelectHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export interface NativeSelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  selectSize?: 'sm' | 'md';
  invalid?: boolean;
}

/**
 * 原生 `<select>` 的统一外观：键盘、读屏器与移动端选择器都由浏览器负责，
 * 只在外观上对齐 `Input`（边框、圆角、聚焦环）。
 */
export const NativeSelect = forwardRef<HTMLSelectElement, NativeSelectProps>(function NativeSelect(
  { className, selectSize = 'md', invalid, children, ...props },
  ref,
) {
  return (
    <div
      className={cn(
        'relative inline-flex items-center rounded-[var(--radius-sm)] border bg-[var(--bg-surface-1)]',
        'border-[var(--border-strong)] transition-colors duration-[var(--dur-fast)]',
        'focus-within:border-[var(--accent)]/60 focus-within:ring-2 focus-within:ring-[var(--accent-ring)]/40',
        invalid && 'border-[var(--danger)]/60',
        selectSize === 'sm' ? 'h-7' : 'h-9',
        className,
      )}
    >
      <select
        ref={ref}
        data-input-field=""
        aria-invalid={invalid || undefined}
        className={cn(
          'h-full w-full appearance-none bg-transparent pl-2.5 pr-7 text-[var(--text-primary)] outline-none',
          selectSize === 'sm' ? 'text-[12px]' : 'text-[13px]',
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-2 size-3.5 text-[var(--text-tertiary)]"
      />
    </div>
  );
});
