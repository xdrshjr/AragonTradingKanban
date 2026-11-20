'use client';

import { Loader2, Sparkles, X } from 'lucide-react';
import { forwardRef } from 'react';
import { createPortal } from 'react-dom';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';

import { assistantViewportStyle, useVisualViewport } from './useVisualViewport';

export interface AssistantLauncherProps {
  open: boolean;
  onClick: React.MouseEventHandler<HTMLButtonElement>;
  isActive?: boolean;
  hasUnread?: boolean;
  panelId?: string;
}

/** A viewport-anchored entry point shared with the report toolbar. */
export const AssistantLauncher = forwardRef<
  HTMLButtonElement, AssistantLauncherProps
>(function AssistantLauncher({
  open, onClick, isActive = false, hasUnread = false,
  panelId = 'aragon-ai-assistant',
}, ref) {
  const viewport = useVisualViewport();
  const compact = viewport
    ? viewport.width < 640 || viewport.height < 640 : false;
  // Portals must appear after hydration; the first client tree matches SSR.
  if (!viewport || typeof document === 'undefined' || (compact && open)) return null;
  const label = open ? copy.ask.close
    : hasUnread ? copy.ask.launcherUnread : copy.ask.launcherOpen;
  const Icon = open ? X : isActive ? Loader2 : Sparkles;
  return createPortal(
    <button
      ref={ref}
      type="button"
      aria-label={label}
      aria-controls={panelId}
      aria-expanded={open}
      title={copy.ask.launcherTitle}
      onClick={onClick}
      data-print="hide"
      data-assistant-launcher=""
      style={assistantViewportStyle(viewport)}
      className="assistant-launcher"
    >
      <Icon aria-hidden="true" className={cn(
        'size-[22px]', isActive && !open && 'animate-spin',
      )} />
      {hasUnread && !open ? <span
        className="absolute right-1 top-1 size-2.5 rounded-full bg-[var(--accent)]"
        aria-hidden="true"
      /> : null}
    </button>, document.body,
  );
});
