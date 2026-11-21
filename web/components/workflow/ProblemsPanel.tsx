'use client';

import { AlertTriangle, CheckCircle2, XOctagon } from 'lucide-react';
import { useCallback } from 'react';

import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { describeIssue } from '@/lib/workflow/copy.workflow';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { ValidationIssue, ValidationReport, WorkflowDoc } from '@/lib/workflow/types';

interface ProblemsViewProps {
  report: ValidationReport | null;
  doc: WorkflowDoc | null;
  onLocate: (issue: ValidationIssue) => void;
}

/** 纯展示：错误在前、警告在后；点击一条 = 定位并选中对应节点（整张图的问题只展示、不可定位）。 */
export function ProblemsView({ report, doc, onLocate }: ProblemsViewProps) {
  const titleOf = (id: string | null): string => doc?.nodes.find((node) => node.id === id)?.title ?? '';
  if (!report) return <p className="px-3 py-3 text-[12px] text-[var(--text-tertiary)]">…</p>;
  if (report.issues.length === 0) {
    return (
      <p className="flex items-center gap-1.5 px-3 py-3 text-[12px] text-[var(--text-secondary)]">
        <CheckCircle2 aria-hidden="true" className="size-3.5 text-[var(--accent)]" />
        {report.blank ? canvasCopy.problems.blank : canvasCopy.problems.none}
      </p>
    );
  }
  const ordered = [...report.issues].sort((a, b) => Number(a.severity === 'warning') - Number(b.severity === 'warning'));
  return (
    <ul aria-label={canvasCopy.dock.problems} className="py-1">
      {ordered.map((issue, index) => {
        const isError = issue.severity === 'error';
        const Icon = isError ? XOctagon : AlertTriangle;
        const locatable = issue.entityKind === 'node' && issue.entityId !== null;
        const text = describeIssue(issue, titleOf);
        return (
          <li key={`${issue.code}-${issue.entityId ?? 'graph'}-${index}`}>
            <button
              type="button"
              disabled={!locatable}
              data-testid="wf-problem"
              data-severity={issue.severity}
              onClick={() => onLocate(issue)}
              className="flex w-full items-start gap-2 px-3 py-1 text-left text-[12px] outline-none hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-hover)] focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)] disabled:cursor-default disabled:hover:bg-transparent"
            >
              <Icon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" style={{ color: isError ? 'var(--danger)' : 'var(--warn)' }} />
              <span className="sr-only">{isError ? canvasCopy.problems.severityError : canvasCopy.problems.severityWarning}：</span>
              <span className="min-w-0 flex-1 text-[var(--text-primary)]">{text}</span>
              <span className="shrink-0 font-mono text-[10.5px] text-[var(--text-tertiary)]">
                {locatable ? canvasCopy.problems.locate : issue.entityKind === 'graph' ? canvasCopy.problems.graphLevel : ''}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** 连 store 的问题面板。 */
export function ProblemsPanel({ onLocated }: { onLocated?: (id: string) => void }) {
  const report = useWorkflowStore((state) => state.validation);
  const doc = useWorkflowStore((state) => state.doc);
  const setSelection = useWorkflowStore((state) => state.setSelection);
  const setHighlight = useWorkflowStore((state) => state.setHighlight);
  const locate = useCallback(
    (issue: ValidationIssue) => {
      if (issue.entityKind !== 'node' || !issue.entityId) return;
      setSelection({ nodes: [issue.entityId], edges: [] });
      setHighlight([issue.entityId], 1200);
      onLocated?.(issue.entityId);
    },
    [setSelection, setHighlight, onLocated],
  );
  return <ProblemsView report={report} doc={doc} onLocate={locate} />;
}
