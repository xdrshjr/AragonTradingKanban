'use client';

import { Activity, CircleAlert } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { useUiStore } from '@/lib/store';

import { Badge } from '../ui/Badge';
import type { useGenerationStream } from '../generation/useGenerationStream';

export interface RunStatusBadgeProps {
  generation: ReturnType<typeof useGenerationStream>;
  agentMode: 'real' | 'mock';
  degradedReason?: string | null;
}

/**
 * Header capsule: a pulsing dot + the current stage while a run is live, and an
 * honest "示例模式" badge when the real engine is unavailable (§10.3, AC-15).
 */
export function RunStatusBadge({ generation, agentMode, degradedReason }: RunStatusBadgeProps) {
  const { state, isActive } = generation;
  const setOverlayCollapsed = useUiStore((store) => store.setOverlayCollapsed);

  if (isActive) {
    return (
      <button
        type="button"
        onClick={() => setOverlayCollapsed(false)}
        className="flex h-7 items-center gap-1.5 rounded-full border border-[var(--accent)]/35 bg-[var(--accent-weak)] px-2.5 text-[11.5px] text-[var(--accent)]"
        aria-live="polite"
      >
        <span className="relative flex size-1.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--accent)] opacity-70" />
          <span className="relative inline-flex size-1.5 rounded-full bg-[var(--accent)]" />
        </span>
        <span className="max-w-[160px] truncate">{state.stageLabel ?? copy.generation.title}</span>
        <span className="num text-[10px] opacity-80">{state.pct}%</span>
      </button>
    );
  }

  if (agentMode === 'mock') {
    const reason = degradedReason ? copy.capabilities.reason[degradedReason] ?? degradedReason : null;
    return (
      <Badge tone="warn" icon={<CircleAlert className="size-3" />} title={reason ?? copy.capabilities.mockBody}>
        {copy.capabilities.degraded}
        {reason ? ` · ${reason}` : ''}
      </Badge>
    );
  }

  return (
    <span className={cn('flex items-center gap-1 text-[10.5px] text-[var(--text-tertiary)]')}>
      <Activity className="size-3" aria-hidden="true" />
      真实生成引擎
    </span>
  );
}
