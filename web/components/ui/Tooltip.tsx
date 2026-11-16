'use client';

import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { forwardRef } from 'react';

import { cn } from '@/lib/cn';

export const TooltipProvider = TooltipPrimitive.Provider;

export interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  delayMs?: number;
  className?: string;
}

/**
 * 200ms delay (spec §6.3), disabled on touch so it never fights a tap. Content is
 * always plain text/nodes — never HTML.
 */
export function Tooltip({ content, children, side = 'top', delayMs = 200, className }: TooltipProps) {
  return (
    <TooltipPrimitive.Root delayDuration={delayMs} disableHoverableContent>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            'z-50 max-w-[280px] rounded-[var(--radius-sm)] border border-[var(--border-strong)]',
            'bg-[var(--bg-surface-3)] px-2 py-1 text-[11.5px] leading-relaxed text-[var(--text-secondary)]',
            'shadow-[var(--shadow-pop)] data-[state=delayed-open]:animate-in',
            className,
          )}
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export const TooltipTrigger = TooltipPrimitive.Trigger;

export interface KbdProps {
  children: React.ReactNode;
  className?: string;
}

export function Kbd({ children, className }: KbdProps) {
  return (
    <kbd
      className={cn(
        'inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-[var(--border-strong)]',
        'bg-[var(--bg-surface-2)] px-1 font-mono text-[10.5px] leading-none text-[var(--text-tertiary)]',
        className,
      )}
    >
      {children}
    </kbd>
  );
}

export const TooltipRoot = TooltipPrimitive.Root;
export const TooltipContent = forwardRef<
  React.ComponentRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(function TooltipContent({ className, ...props }, ref) {
  return (
    <TooltipPrimitive.Content
      ref={ref}
      className={cn(
        'z-50 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-3)]',
        'px-2 py-1 text-[11.5px] text-[var(--text-secondary)] shadow-[var(--shadow-pop)]',
        className,
      )}
      {...props}
    />
  );
});
