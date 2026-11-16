'use client';

import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Loader2, X } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { durationLabel } from '@/lib/format';
import { useUiStore } from '@/lib/store';
import type { RunStageId } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Button, IconButton } from '../ui/Button';
import type { GenerationState } from './useGenerationStream';

const STAGES: { id: RunStageId; pct: number }[] = [
  { id: 'resolve', pct: 5 },
  { id: 'snapshot', pct: 15 },
  { id: 'prepare', pct: 20 },
  { id: 'research', pct: 70 },
  { id: 'validate', pct: 85 },
  { id: 'persist', pct: 100 },
];

export interface GenerationOverlayProps {
  state: GenerationState;
  onCancel: () => void;
  onDismiss: () => void;
  onRetry: () => void;
  agentMode: 'real' | 'mock';
  onOpenReport?: (reportId: string) => void;
}

/**
 * Non-modal progress card, fixed bottom-right (spec §5.5 "生成覆盖层为非模态侧边
 * 卡片"). It must never block reading the previous version — that is the whole
 * point of "生成中不阻塞阅读".
 */
export function GenerationOverlay({
  state,
  onCancel,
  onDismiss,
  onRetry,
  agentMode,
  onOpenReport,
}: GenerationOverlayProps) {
  // Collapse lives in the store so the header capsule can expand the card
  // (spec §6.3: clicking the running badge opens the progress overlay).
  const collapsed = useUiStore((state) => state.overlayCollapsed);
  const setCollapsed = useUiStore((state) => state.setOverlayCollapsed);
  const visible = state.phase !== 'idle';
  if (!visible) return null;

  const running = state.phase === 'submitting' || state.phase === 'running';
  const done = state.phase === 'done';
  const failed = state.phase === 'error';
  const cancelled = state.phase === 'cancelled';
  const activeIndex = Math.max(
    0,
    STAGES.findIndex((stage) => stage.id === state.stage),
  );
  const elapsed = state.startedAt ? (state.finishedAt ?? Date.now()) - state.startedAt : 0;

  return (
    <section
      aria-label={copy.generation.title}
      aria-live="polite"
      data-print="hide"
      className={cn(
        'fixed bottom-4 right-4 z-40 w-[340px] overflow-hidden rounded-[var(--radius-lg)]',
        'border border-[var(--border-strong)] bg-[var(--bg-surface-2)] shadow-[var(--shadow-pop)]',
      )}
    >
      <header className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          {running ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-[var(--accent)]" aria-hidden="true" />
          ) : done ? (
            <CheckCircle2 className="size-3.5 shrink-0 text-[var(--accent)]" aria-hidden="true" />
          ) : (
            <AlertCircle className="size-3.5 shrink-0 text-[var(--warn)]" aria-hidden="true" />
          )}
          <h2 className="truncate text-[12.5px] font-semibold text-[var(--text-primary)]">
            {running
              ? copy.generation.title
              : done
                ? copy.generation.doneTitle
                : cancelled
                  ? copy.generation.cancelled
                  : copy.generation.failedTitle}
          </h2>
        </span>
        {running ? <Badge tone="accent">{(state.pct ?? 0).toString()}%</Badge> : null}
        <IconButton label={collapsed ? '展开' : '收起'} onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        </IconButton>
        {!running ? (
          <IconButton label={copy.generation.close} onClick={onDismiss}>
            <X className="size-3.5" />
          </IconButton>
        ) : null}
      </header>

      <div className="h-[3px] w-full bg-[var(--bg-surface-3)]" aria-hidden="true">
        <div
          className={cn(
            'h-full transition-[width] duration-500 ease-out',
            failed ? 'bg-[var(--warn)]' : 'bg-[var(--accent)]',
          )}
          style={{ width: `${Math.max(2, Math.min(100, state.pct))}%` }}
        />
      </div>

      {collapsed ? null : (
        <>
          <ol className="grid grid-cols-3 gap-px bg-[var(--border-subtle)]">
            {STAGES.map((stage, index) => {
              const isActive = running && index === activeIndex;
              const isDone = state.pct >= stage.pct || done;
              return (
                <li
                  key={stage.id}
                  className={cn(
                    'flex items-center gap-1 bg-[var(--bg-surface-1)] px-2 py-1.5',
                    isActive && 'bg-[var(--accent-weak)]',
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      'size-1.5 shrink-0 rounded-full',
                      isDone ? 'bg-[var(--accent)]' : isActive ? 'animate-pulse bg-[var(--accent)]' : 'bg-[var(--border-strong)]',
                    )}
                  />
                  <span
                    className={cn(
                      'truncate text-[10.5px] leading-tight',
                      isActive ? 'font-medium text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]',
                    )}
                  >
                    {copy.generation.stages[stage.id]}
                  </span>
                </li>
              );
            })}
          </ol>

          <div className="max-h-[168px] overflow-y-auto px-3 py-2">
            {state.streamText ? (
              <p className="mb-1.5 line-clamp-3 border-l-2 border-[var(--accent)]/40 pl-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
                {state.streamText.slice(-240)}
              </p>
            ) : null}
            {state.logs.length === 0 ? (
              <p className="text-[11px] text-[var(--text-tertiary)]">{copy.generation.emptyLogs}</p>
            ) : (
              <ul className="space-y-0.5">
                {state.logs.slice(-14).map((log) => (
                  <li
                    key={log.id}
                    className={cn(
                      'mono text-[10.5px] leading-snug',
                      log.level === 'warn'
                        ? 'text-[var(--warn)]'
                        : log.level === 'error'
                          ? 'text-[var(--danger)]'
                          : 'text-[var(--text-tertiary)]',
                    )}
                  >
                    {log.message}
                  </li>
                ))}
              </ul>
            )}
            {state.tools.length > 0 ? (
              <p className="mt-1.5 text-[10px] text-[var(--text-tertiary)]">
                {copy.generation.toolCalls} · {state.tools.length} 次
              </p>
            ) : null}
          </div>

          <footer className="flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] px-3 py-2">
            <span className="truncate text-[10px] text-[var(--text-tertiary)]">
              {agentMode === 'mock' ? copy.generation.mockNotice : copy.generation.realNotice}
              {running && elapsed > 0 ? ` · ${durationLabel(elapsed)}` : ''}
            </span>
            {running ? (
              <Button size="sm" variant="ghost" onClick={onCancel}>
                {copy.generation.cancel}
              </Button>
            ) : failed ? (
              <Button size="sm" variant="secondary" onClick={onRetry}>
                {copy.generation.retry}
              </Button>
            ) : done && state.reportId && onOpenReport ? (
              <Button size="sm" variant="accent-ghost" onClick={() => onOpenReport(state.reportId!)}>
                查看报告
              </Button>
            ) : null}
          </footer>

          {state.phase === 'error' && state.error ? (
            <p className="border-t border-[var(--border-subtle)] bg-[var(--warn-weak)] px-3 py-1.5 text-[10.5px] leading-snug text-[var(--warn)]">
              {state.error.code} · {state.error.message}
            </p>
          ) : null}
          {state.phase === 'error' ? (
            <p className="border-t border-[var(--border-subtle)] px-3 py-1.5 text-[10.5px] leading-snug text-[var(--text-tertiary)]">
              {copy.generation.provisionalBody}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
