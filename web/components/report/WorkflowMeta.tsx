'use client';

import { Workflow } from 'lucide-react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import type { QualityWorkflow } from '@/lib/types';

export interface WorkflowMetaProps {
  workflow?: QualityWorkflow | null;
}

/** 悬浮提示：遵循度的一句人话（运营信息，不是用户的错）。 */
export function adherenceHint(workflow: QualityWorkflow): string {
  if (workflow.adherence === 'unreported') return copy.workflowMeta.adherenceUnreported;
  if (workflow.adherence === 'none') return copy.workflowMeta.adherenceNone;
  const steps = workflow.steps;
  const gates = workflow.gates;
  const total = (steps?.total ?? 0) + (gates?.total ?? 0);
  const done = (steps?.done ?? 0) + (steps?.skipped ?? 0) + (gates?.done ?? 0);
  return fill(copy.workflowMeta.adherence, { done, total });
}

/**
 * ★v16（研究流程图 §3.10.9）：报告元信息里的「按『标准五段式研究 v3』研究」。
 *
 * - 旧报告没有 `quality.workflow` ⇒ 什么都不渲染（"不回填"纪律，同 v7）；
 * - `adherence === 'none'` 或回退内置时用 `--text-tertiary` 弱提示，**不用警示色**。
 */
export function WorkflowMeta({ workflow }: WorkflowMetaProps) {
  if (!workflow) return null;
  const fallback = workflow.source === 'builtin-code' && Boolean(workflow.fallbackReason);
  const name = fallback ? copy.workflowMeta.builtinFallback : workflow.templateName;
  const label =
    !fallback && workflow.version
      ? fill(copy.workflowMeta.labelVersion, { name, version: workflow.version })
      : fill(copy.workflowMeta.label, { name });
  const quiet = fallback || workflow.adherence === 'none';
  const hint = fallback ? copy.workflowMeta.fallback : adherenceHint(workflow);
  return (
    <span
      data-testid="workflow-meta"
      data-adherence={workflow.adherence}
      title={hint}
      className={cn(
        'inline-flex max-w-[220px] items-center gap-1 truncate text-[10.5px]',
        quiet ? 'text-[var(--text-tertiary)]' : 'text-[var(--text-secondary)]',
      )}
    >
      <Workflow className="size-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </span>
  );
}
