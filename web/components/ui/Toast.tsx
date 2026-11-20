'use client';

import { Toaster as SonnerToaster, toast } from 'sonner';
import type { CSSProperties } from 'react';
import { useUiStore } from '@/lib/store';
import { useVisualViewport } from '../ask/useVisualViewport';

/**
 * Toasts: bottom-left; compact assistant notifications avoid its controls.
 * Errors never auto-dismiss
 * faster than a person can read them.
 */
export function ToastProvider() {
  const assistantOpen = useUiStore((state) => state.assistantOpen);
  const viewport = useVisualViewport(assistantOpen);
  const compact = assistantOpen && Boolean(viewport &&
    (viewport.width < 640 || viewport.height < 640));
  return (
    <SonnerToaster
      position={compact ? 'top-left' : 'bottom-left'}
      offset={compact ? {
        top: `${(viewport?.offsetTop ?? 0) + 16}px`,
        left: `${(viewport?.offsetLeft ?? 0) + 16}px`,
      } : 16}
      mobileOffset={compact ? {
        top: `${(viewport?.offsetTop ?? 0) + 16}px`,
        left: `${(viewport?.offsetLeft ?? 0) + 16}px`,
      } : 16}
      visibleToasts={compact ? 1 : 3}
      style={compact ? {
        '--width': `calc(${viewport?.width}px - 104px)`,
        width: `calc(${viewport?.width}px - 104px)`,
      } as CSSProperties : undefined}
      closeButton
      toastOptions={{
        style: compact ? {
          width: `calc(${viewport?.width}px - 104px)`,
          maxWidth: `calc(${viewport?.width}px - 104px)`,
        } : undefined,
        className:
          'rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-3)] text-[var(--text-primary)] text-[12.5px] shadow-[var(--shadow-pop)]',
        duration: 4000,
      }}
    />
  );
}

export const notify = {
  info: (message: string) => toast(message),
  success: (message: string, description?: string) => toast.success(message, { description }),
  error: (message: string, description?: string) => toast.error(message, { description, duration: 6000 }),
  warn: (message: string, description?: string) => toast.warning(message, { description }),
  copied: (message = '已复制到剪贴板') => toast.success(message),
};

export { toast };
