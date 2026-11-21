'use client';

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { ShieldCheck } from 'lucide-react';
import { memo } from 'react';

import { cn } from '@/lib/cn';
import { canvasCopy } from '@/lib/workflow/copy.canvas';

import { IssueBadge } from './IssueBadge';
import type { FlowNodeData } from './nodeData';

type GateFlowNode = Node<FlowNodeData, 'gate'>;

/** 检查点：窄卡 + 六边形切角，语义是"汇合并复核"。 */
function GateNodeView({ data, selected }: NodeProps<GateFlowNode>) {
  const { node, planKey, errors, warnings, highlighted, readOnly } = data;
  return (
    <div
      data-testid={`wf-node-${node.id}`}
      data-kind="gate"
      className={cn(
        'wf-node wf-gate relative flex w-[170px] items-center gap-1.5 border bg-[var(--bg-surface-3)] px-3 py-1.5 text-[12px]',
        selected ? 'border-[var(--accent)] ring-2 ring-[var(--accent-ring)]/50' : 'border-[var(--border-strong)]',
        highlighted && 'wf-pulse',
        errors > 0 && 'border-[var(--danger)]/70',
      )}
    >
      <ShieldCheck aria-hidden="true" className="size-3.5 shrink-0 text-[var(--warn)]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1">
          <span className="font-mono text-[10.5px] font-semibold text-[var(--warn)]">{planKey ?? 'G'}</span>
          <span className="text-[10px] text-[var(--text-tertiary)]">{canvasCopy.node.gateWaiting}</span>
        </div>
        <div className="truncate text-[12px] font-medium text-[var(--text-primary)]">
          {node.title.trim() === '' ? canvasCopy.node.untitled : node.title}
        </div>
      </div>
      <IssueBadge errors={errors} warnings={warnings} />
      <Handle type="target" position={Position.Left} isConnectable={!readOnly} className="wf-handle" />
      <Handle type="source" position={Position.Right} isConnectable={!readOnly} className="wf-handle" />
    </div>
  );
}

export const GateNode = memo(GateNodeView);
