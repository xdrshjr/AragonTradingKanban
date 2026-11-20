'use client';

import { Component, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { copy } from '@/lib/copy';
import { useReturnFocus } from '@/lib/focus';

import { Button, IconButton } from '../ui/Button';
import { assistantViewportStyle, useVisualViewport } from './useVisualViewport';

interface AskPanelBoundaryProps {
  children: ReactNode;
  onClose: () => void;
  open?: boolean;
  triggerRef?: React.RefObject<HTMLElement | null>;
}

/** Loading and chunk failure share the usable floating surface. */
function LoadingSurface({ onClose, failed = false, open = true, triggerRef }: {
  onClose: () => void; failed?: boolean; open?: boolean;
  triggerRef?: React.RefObject<HTMLElement | null>;
}) {
  const viewport = useVisualViewport(open);
  const fallbackTrigger = useRef<HTMLElement | null>(
    typeof document === 'undefined' ? null
      : document.activeElement as HTMLElement | null,
  );
  useReturnFocus(triggerRef ?? fallbackTrigger, open, onClose, {
    handleEscape: false, returnFocus: true,
  });
  if (!open || typeof document === 'undefined') return null;
  return createPortal(<section id="aragon-ai-assistant" role="region" aria-label={copy.ask.title}
    data-print="hide" className="assistant-panel"
    data-compact={viewport ? viewport.width < 640 || viewport.height < 640 : undefined}
    data-short={viewport ? viewport.height < 480 : undefined}
    style={assistantViewportStyle(viewport)}>
    <header className="flex shrink-0 items-center border-b border-[var(--border-subtle)] px-4 py-2">
      <h2 className="flex-1 text-[13px] font-semibold">{copy.ask.title}</h2>
      <IconButton label={copy.ask.close} onClick={onClose}
        className="!size-11">×</IconButton>
    </header>
    <div role="status" className="p-4 text-[12px] text-[var(--text-secondary)]">
      {failed ? copy.ask.loadingFailed : copy.ask.loadingPanel}
      {failed ? <Button className="mt-3" size="sm"
        onClick={() => window.location.reload()}>{copy.ask.reloadPage}</Button> : null}
    </div>
  </section>, document.body);
}

export function AskPanelLoading({ onClose, open = true }: {
  onClose: () => void; open?: boolean;
}) {
  return <LoadingSurface onClose={onClose} open={open} />;
}

export class AskPanelBoundary extends Component<
  AskPanelBoundaryProps, { failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  override render() {
    return this.state.failed
      ? <LoadingSurface onClose={this.props.onClose}
        open={this.props.open} triggerRef={this.props.triggerRef} failed />
      : this.props.children;
  }
}
