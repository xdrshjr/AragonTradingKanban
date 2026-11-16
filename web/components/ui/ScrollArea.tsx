'use client';

import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area';
import { forwardRef } from 'react';

import { cn } from '@/lib/cn';

export const ScrollArea = forwardRef<
  React.ComponentRef<typeof ScrollAreaPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> & {
    viewportClassName?: string;
    viewportRef?: React.Ref<HTMLDivElement>;
  }
>(function ScrollArea({ className, children, viewportClassName, viewportRef, ...props }, ref) {
  return (
    <ScrollAreaPrimitive.Root ref={ref} className={cn('relative overflow-hidden', className)} {...props}>
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        className={cn('size-full rounded-[inherit] outline-none', viewportClassName)}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollAreaPrimitive.Scrollbar
        orientation="vertical"
        className="flex w-2 touch-none select-none p-0.5 transition-colors"
      >
        <ScrollAreaPrimitive.Thumb className="flex-1 rounded-full bg-[var(--border-strong)] hover:bg-[var(--text-tertiary)]" />
      </ScrollAreaPrimitive.Scrollbar>
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  );
});
