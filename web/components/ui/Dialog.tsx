'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { forwardRef } from 'react';

import { cn } from '@/lib/cn';

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export const DialogContent = forwardRef<
  React.ComponentRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
    title: string;
    description?: string;
    hideClose?: boolean;
    className?: string;
  }
>(function DialogContent({ className, children, title, description, hideClose, ...props }, ref) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        className={cn(
          'fixed inset-0 z-40 bg-[var(--bg-overlay)] backdrop-blur-[2px]',
          'data-[state=open]:animate-in data-[state=open]:fade-in',
        )}
      />
      <DialogPrimitive.Content
        ref={ref}
        className={cn(
          'fixed left-1/2 top-1/2 z-50 w-[min(92vw,640px)] -translate-x-1/2 -translate-y-1/2',
          'rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)]',
          'shadow-[var(--shadow-pop)] outline-none',
          className,
        )}
        {...props}
      >
        <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
        {description ? (
          <DialogPrimitive.Description className="sr-only">{description}</DialogPrimitive.Description>
        ) : null}
        {children}
        {hideClose ? null : (
          <DialogPrimitive.Close
            aria-label="关闭"
            className="absolute right-3 top-3 rounded p-1 text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
          >
            <X className="size-4" />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});
