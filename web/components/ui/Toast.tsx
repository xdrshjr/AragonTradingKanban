'use client';

import { Toaster as SonnerToaster, toast } from 'sonner';

/**
 * Toasts: bottom-right, non-blocking, max 3 at once. Errors never auto-dismiss
 * faster than a person can read them.
 */
export function ToastProvider() {
  return (
    <SonnerToaster
      position="bottom-right"
      offset={16}
      visibleToasts={3}
      closeButton
      toastOptions={{
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
