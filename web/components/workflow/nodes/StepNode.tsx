'use client';

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { FileText, Layers } from 'lucide-react';
import { memo } from 'react';

import { cn } from '@/lib/cn';
import { canvasCopy, ROLE_LABELS } from '@/lib/workflow/copy.canvas';

import { IssueBadge } from './IssueBadge';
import { ROLE_TONE, type FlowNodeData } from './nodeData';

type StepFlowNode = Node<FlowNodeData, 'step'>;

function StepNodeView({ data, selected }: NodeProps<StepFlowNode>) {
  const { node, planKey, errors, warnings, highlighted, readOnly } = data;
  const role = node.config.role ?? 'custom';
  const files = node.config.files?.length ?? 0;
  const sections = node.config.sections?.length ?? 0;
  const tone = ROLE_TONE[role] ?? ROLE_TONE.custom;
  return (
    <div
      data-testid={`wf-node-${node.id}`}
      data-kind="step"
      className={cn(
        'wf-node relative w-[220px] overflow-hidden rounded-[var(--radius-sm)] border bg-[var(--bg-surface-2)] text-[12px]',
        'shadow-[var(--shadow-card)]',
        selected ? 'border-[var(--accent)] ring-2 ring-[var(--accent-ring)]/50' : 'border-[var(--border-strong)]',
        highlighted && 'wf-pulse',
        errors > 0 && 'border-[var(--danger)]/70',
      )}
    >
      <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[3px]" style={{ background: tone }} />
      <div className="py-1.5 pl-3 pr-2">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[10.5px] font-semibold" style={{ color: tone }}>
            {planKey ?? '·'}
          </span>
          <span className="rounded-sm px-1 text-[10px] text-[var(--text-tertiary)]">{ROLE_LABELS[role] ?? role}</span>
          <span className="ml-auto">
            <IssueBadge errors={errors} warnings={warnings} />
          </span>
        </div>
        <div className="mt-0.5 truncate text-[12.5px] font-medium text-[var(--text-primary)]">
          {node.title.trim() === '' ? canvasCopy.node.untitled : node.title}
        </div>
        <div className="mt-1 flex items-center gap-2 text-[10.5px] text-[var(--text-tertiary)]">
          <span className="inline-flex items-center gap-0.5" title={canvasCopy.inspector.fieldFiles}>
            <FileText aria-hidden="true" className="size-3" />
            {files}
          </span>
          <span className="inline-flex items-center gap-0.5" title={canvasCopy.inspector.fieldSections}>
            <Layers aria-hidden="true" className="size-3" />
            {sections}
          </span>
        </div>
      </div>
      <Handle type="target" position={Position.Left} isConnectable={!readOnly} className="wf-handle" />
      <Handle type="source" position={Position.Right} isConnectable={!readOnly} className="wf-handle" />
    </div>
  );
}

export const StepNode = memo(StepNodeView);
