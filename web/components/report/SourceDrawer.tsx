'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area';
import { Check, Copy, ExternalLink, X } from 'lucide-react';
import { useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { dateOnly } from '@/lib/format';
import { restoreFocus } from '@/lib/focus';
import { useUiStore } from '@/lib/store';
import type { SourceRef } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Button, IconButton } from '../ui/Button';

/**
 * Source drawer (spec §3.3 / AC-7 / AC-16).
 *
 * Built on Radix Dialog primitives rather than a hand-rolled `role="dialog"`, so
 * `Esc` closes it, focus moves into the panel on open and returns to the chip
 * that opened it on close, and a click outside dismisses it — all without a
 * bespoke key handler. `modal={false}` keeps the report readable underneath.
 */
export function SourceDrawer({ sources }: { sources: SourceRef[] }) {
  const { open, activeSourceId } = useUiStore((state) => state.sourceDrawer);
  const close = useUiStore((state) => state.closeSourceDrawer);
  const openSourceDrawer = useUiStore((state) => state.openSourceDrawer);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  const active = sources.find((source) => source.id === activeSourceId) ?? null;

  const copyCitation = async (source: SourceRef) => {
    const text = `${source.title} · ${source.publisher}${source.url ? ` · ${source.url}` : ''} [${source.id}]`;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(source.id);
      setTimeout(() => setCopiedId(null), 1500);
    } catch {
      setCopiedId(null);
    }
  };

  return (
    <DialogPrimitive.Root open={open} modal={false} onOpenChange={(next) => (next ? undefined : close())}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content
          aria-describedby={undefined}
          data-print="hide"
          onOpenAutoFocus={() => {
            triggerRef.current = document.activeElement as HTMLElement | null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const trigger = triggerRef.current;
            const fallback = document.querySelector<HTMLElement>(
              '[data-report-source-trigger]',
            );
            restoreFocus(trigger?.isConnected && trigger.getClientRects().length
              && !trigger.matches(':disabled') ? trigger : fallback);
          }}
          onPointerDownOutside={() => close()}
          onEscapeKeyDown={(event) => {
            event.preventDefault();
            close();
          }}
          className={cn(
            'fixed inset-y-0 right-0 z-40 flex h-full w-[min(360px,86vw)] flex-col',
            'border-l border-[var(--border-strong)] bg-[var(--bg-surface-2)] shadow-[var(--shadow-pop)] outline-none',
          )}
        >
          <DialogPrimitive.Title className="sr-only">来源</DialogPrimitive.Title>
          <header className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-2">
            <h2 className="text-[12.5px] font-semibold text-[var(--text-primary)]">来源</h2>
            <DialogPrimitive.Close asChild>
              <IconButton label="关闭">
                <X className="size-3.5" />
              </IconButton>
            </DialogPrimitive.Close>
          </header>

          {active ? (
            <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3 py-2.5">
              <div className="flex items-center gap-1.5">
                <Badge tone="accent">{active.id}</Badge>
                {active.publishedAt ? (
                  <span className="text-[10.5px] text-[var(--text-tertiary)]">{dateOnly(active.publishedAt)}</span>
                ) : null}
              </div>
              <p className="mt-1.5 text-[12.5px] font-medium leading-snug text-[var(--text-primary)]">{active.title}</p>
              <p className="text-[11.5px] text-[var(--text-tertiary)]">{active.publisher}</p>
              <div className="mt-2 flex items-center gap-1.5">
                {active.url ? (
                  <a
                    href={active.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-7 items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-2 text-[11.5px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                  >
                    打开原文 <ExternalLink className="size-3" />
                  </a>
                ) : null}
                <Button size="sm" variant="ghost" onClick={() => void copyCitation(active)}>
                  {copiedId === active.id ? <Check className="size-3 text-[var(--accent)]" /> : <Copy className="size-3" />}
                  复制引用
                </Button>
              </div>
            </div>
          ) : null}

          <ScrollAreaPrimitive.Root className="relative min-h-0 flex-1 overflow-hidden">
            <ScrollAreaPrimitive.Viewport className="size-full px-2 py-2">
              {sources.length === 0 ? (
                <p className="px-2 py-6 text-center text-[11.5px] text-[var(--text-tertiary)]">本报告没有引用来源</p>
              ) : (
                <ul className="space-y-1">
                  {sources.map((source) => (
                    <li key={source.id}>
                      <button
                        type="button"
                        onClick={() => openSourceDrawer(source.id)}
                        aria-current={source.id === activeSourceId ? 'true' : undefined}
                        className={cn(
                          'w-full rounded-[var(--radius-sm)] px-2 py-1.5 text-left transition-colors',
                          source.id === activeSourceId ? 'bg-[var(--accent-weak)]' : 'hover:bg-[var(--bg-hover)]',
                        )}
                      >
                        <span className="flex items-center gap-1.5">
                          <span className="mono text-[10px] font-semibold text-[var(--accent)]">{source.id}</span>
                          <span className="truncate text-[12px] text-[var(--text-primary)]">{source.title}</span>
                        </span>
                        <span className="mt-0.5 block truncate text-[10.5px] text-[var(--text-tertiary)]">
                          {source.publisher}
                          {source.publishedAt ? ` · ${dateOnly(source.publishedAt)}` : ''}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </ScrollAreaPrimitive.Viewport>
            <ScrollAreaPrimitive.Scrollbar orientation="vertical" className="flex w-2 touch-none select-none p-0.5">
              <ScrollAreaPrimitive.Thumb className="flex-1 rounded-full bg-[var(--border-strong)]" />
            </ScrollAreaPrimitive.Scrollbar>
          </ScrollAreaPrimitive.Root>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
