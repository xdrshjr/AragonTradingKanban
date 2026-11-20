'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { forwardRef } from 'react';

import { cn } from '@/lib/cn';

export const Sheet = DialogPrimitive.Root;
export const SheetClose = DialogPrimitive.Close;

export interface SheetContentProps extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /** 读屏器用的标题（可见标题由内容自己渲染）。 */
  title: string;
  description?: string;
  closeLabel?: string;
}

/**
 * 右侧抽屉（480px）；窄屏（< 900px）变全屏 Sheet（§24.3.15e）。
 *
 * 基于 Radix Dialog：焦点陷阱、Esc 关闭、关闭后还原焦点都是默认行为。层级刻意低于 `Dialog`
 * （overlay 40 / content 50）——抽屉里弹出的二次确认对话框必须盖在抽屉上面。
 */
export const SheetContent = forwardRef<React.ComponentRef<typeof DialogPrimitive.Content>, SheetContentProps>(
  function SheetContent({ className, children, title, description, closeLabel = '关闭', ...props }, ref) {
    return (
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-30 bg-[var(--bg-overlay)] backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            'fixed inset-y-0 right-0 z-[35] flex w-full flex-col outline-none',
            'border-l border-[var(--border-strong)] bg-[var(--bg-surface-1)] shadow-[var(--shadow-pop)]',
            'min-[900px]:w-[480px]',
            className,
          )}
          {...props}
        >
          <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">{description ?? title}</DialogPrimitive.Description>
          {children}
          <DialogPrimitive.Close
            aria-label={closeLabel}
            className="absolute right-3 top-3 rounded p-1 text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
          >
            <X className="size-4" aria-hidden="true" />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    );
  },
);
