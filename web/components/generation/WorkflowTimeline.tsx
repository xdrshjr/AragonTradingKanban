'use client';

import { Check, Loader2, Minus } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';

import type { WorkflowItemView, WorkflowProgressView } from './useGenerationStream';

/**
 * ★v16（研究流程图 §3.10.9）：生成遮罩里的**研究流程时间线**——小而克制。
 *
 * 进度来自 Agent 输出的进度标记，那只是"观测"：没有标记到达时每一项保持"待定"，
 * 并且**永远不显示失败**（软约束，漏标不是用户的错）。状态靠图标 + 文字区分（不只靠颜色，AC-W35）。
 */
export interface WorkflowTimelineProps {
  workflow: WorkflowProgressView;
}

const STATE_LABEL: Record<WorkflowItemView['state'], string> = {
  pending: copy.generation.workflow.pending,
  running: copy.generation.workflow.running,
  done: copy.generation.workflow.done,
  skip: copy.generation.workflow.skipped,
};

function StateIcon({ state }: { state: WorkflowItemView['state'] }) {
  if (state === 'done') return <Check className="size-3 text-[var(--accent)]" aria-hidden="true" />;
  if (state === 'skip') return <Minus className="size-3 text-[var(--text-tertiary)]" aria-hidden="true" />;
  if (state === 'running') {
    return <Loader2 className="size-3 animate-spin text-[var(--accent)] motion-reduce:animate-none" aria-hidden="true" />;
  }
  return <span aria-hidden="true" className="size-1.5 rounded-full border border-[var(--border-strong)]" />;
}

function TimelineRow({ item }: { item: WorkflowItemView }) {
  const isGate = item.kind === 'gate';
  return (
    <li
      data-testid={`wf-item-${item.key}`}
      data-state={item.state}
      className={cn(
        'flex items-center gap-1.5 py-0.5 text-[10.5px] leading-tight',
        isGate && 'mt-0.5 border-t border-dashed border-[var(--border-subtle)] pt-1',
        item.state === 'running' ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]',
      )}
    >
      <span className="flex size-3.5 shrink-0 items-center justify-center">
        <StateIcon state={item.state} />
      </span>
      <span className="mono shrink-0 text-[var(--text-tertiary)]">{item.key}</span>
      <span
        className={cn('min-w-0 flex-1 truncate', item.state === 'running' && 'font-medium')}
        title={item.note ?? item.title}
      >
        {isGate ? `${copy.generation.workflow.gate}：` : ''}
        {item.title}
      </span>
      <span className="shrink-0 text-[10px] text-[var(--text-tertiary)]">{STATE_LABEL[item.state]}</span>
    </li>
  );
}

export function WorkflowTimeline({ workflow }: WorkflowTimelineProps) {
  if (workflow.items.length === 0) return null;
  const title = workflow.fallback
    ? copy.generation.workflow.fallbackTitle
    : `${workflow.templateName}${workflow.version ? ` v${workflow.version}` : ''}`;
  return (
    <section
      data-testid="workflow-timeline"
      aria-label={copy.generation.workflow.listLabel}
      className="border-t border-[var(--border-subtle)] px-3 py-1.5"
    >
      <header className="mb-0.5 flex items-center justify-between gap-2 text-[10.5px]">
        <span className="min-w-0 truncate font-medium text-[var(--text-secondary)]">
          {copy.generation.workflow.title} · {title}
        </span>
        <span className="mono shrink-0 text-[var(--text-tertiary)]">
          {fill(copy.generation.workflow.progress, { done: workflow.doneCount, total: workflow.total })}
        </span>
      </header>
      <ol className="max-h-[132px] overflow-y-auto">
        {workflow.items.map((item) => (
          <TimelineRow key={item.key} item={item} />
        ))}
      </ol>
    </section>
  );
}
