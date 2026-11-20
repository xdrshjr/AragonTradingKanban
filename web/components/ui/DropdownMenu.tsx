'use client';

import * as MenuPrimitive from '@radix-ui/react-dropdown-menu';
import { forwardRef } from 'react';

import { cn } from '@/lib/cn';

export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;

export const DropdownMenuContent = forwardRef<
  React.ComponentRef<typeof MenuPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof MenuPrimitive.Content>
>(function DropdownMenuContent({ className, sideOffset = 4, ...props }, ref) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        collisionPadding={8}
        className={cn(
          'z-50 min-w-[168px] rounded-[var(--radius-md)] border border-[var(--border-strong)]',
          'bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]',
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
});

export const DropdownMenuItem = forwardRef<
  React.ComponentRef<typeof MenuPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof MenuPrimitive.Item> & { tone?: 'default' | 'danger' }
>(function DropdownMenuItem({ className, tone = 'default', ...props }, ref) {
  return (
    <MenuPrimitive.Item
      ref={ref}
      className={cn(
        'flex cursor-pointer select-none items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-[12.5px] outline-none',
        'data-[highlighted]:bg-[var(--bg-hover)] data-[disabled]:pointer-events-none data-[disabled]:opacity-45',
        tone === 'danger'
          ? 'text-[var(--danger)]'
          : 'text-[var(--text-secondary)] data-[highlighted]:text-[var(--text-primary)]',
        className,
      )}
      {...props}
    />
  );
});

export const DropdownMenuSeparator = forwardRef<
  React.ComponentRef<typeof MenuPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof MenuPrimitive.Separator>
>(function DropdownMenuSeparator({ className, ...props }, ref) {
  return (
    <MenuPrimitive.Separator ref={ref} className={cn('my-1 h-px bg-[var(--border-subtle)]', className)} {...props} />
  );
});
