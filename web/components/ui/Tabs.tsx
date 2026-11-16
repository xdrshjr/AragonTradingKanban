'use client';

import * as TabsPrimitive from '@radix-ui/react-tabs';
import { forwardRef } from 'react';

import { cn } from '@/lib/cn';

export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef<
  React.ComponentRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(function TabsList({ className, ...props }, ref) {
  return (
    <TabsPrimitive.List
      ref={ref}
      className={cn('relative flex items-stretch gap-0.5 border-b border-[var(--border-subtle)]', className)}
      {...props}
    />
  );
});

export const TabsTrigger = forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(function TabsTrigger({ className, ...props }, ref) {
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        'relative -mb-px select-none whitespace-nowrap border-b-2 border-transparent px-3 py-2',
        'text-[12.5px] font-medium text-[var(--text-tertiary)] transition-colors duration-[var(--dur-fast)]',
        'hover:text-[var(--text-secondary)]',
        'data-[state=active]:border-[var(--accent)] data-[state=active]:text-[var(--text-primary)]',
        className,
      )}
      {...props}
    />
  );
});

export const TabsContent = forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(function TabsContent({ className, ...props }, ref) {
  return (
    <TabsPrimitive.Content
      ref={ref}
      className={cn('outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]', className)}
      {...props}
    />
  );
});

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export interface SegmentedProps<T extends string> {
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
  size?: 'sm' | 'md';
  className?: string;
}

/**
 * A segmented control with `aria-pressed` (spec §5.5 "关键交互细节"), used for
 * timeframe / range / adjust.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = 'sm',
  className,
}: SegmentedProps<T>) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        'inline-flex items-center gap-px rounded-[var(--radius-sm)] border border-[var(--border-strong)]',
        'bg-[var(--bg-surface-1)] p-px',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            title={option.title ?? option.label}
            onClick={() => onChange(option.value)}
            className={cn(
              'rounded-[5px] font-medium transition-colors duration-[var(--dur-fast)]',
              size === 'sm' ? 'h-6 px-2 text-[11.5px]' : 'h-7 px-2.5 text-[12.5px]',
              active
                ? 'bg-[var(--bg-surface-3)] text-[var(--text-primary)]'
                : 'text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-secondary)]',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
