'use client';

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { Flag, Play } from 'lucide-react';
import { memo } from 'react';

import { cn } from '@/lib/cn';
import { canvasCopy } from '@/lib/workflow/copy.canvas';

import { IssueBadge } from './IssueBadge';
import type { FlowNodeData } from './nodeData';

type AnchorFlowNode = Node<FlowNodeData, 'start' | 'end'>;

/** 起止锚点：胶囊形，不可删除；start 只有出口，end 只有入口。 */
function AnchorNodeView({ data, selected }: NodeProps<AnchorFlowNode>) {
  const { node, errors, warnings, readOnly } = data;
  const isStart = node.kind === 'start';
  const Icon = isStart ? Play : Flag;
  const fallback = isStart ? canvasCopy.node.start : canvasCopy.node.end;
  return (
    <div
      data-testid={`wf-node-${node.id}`}
      data-kind={node.kind}
      className={cn(
        'wf-node inline-flex items-center gap-1.5 rounded-full border bg-[var(--bg-surface-3)] px-3 py-1 text-[12px] font-medium',
        selected ? 'border-[var(--accent)] ring-2 ring-[var(--accent-ring)]/50' : 'border-[var(--border-strong)]',
        'text-[var(--text-primary)]',
      )}
    >
      <Icon aria-hidden="true" className="size-3 text-[var(--accent)]" />
      <span>{node.title.trim() === '' ? fallback : node.title}</span>
      <IssueBadge errors={errors} warnings={warnings} />
      {isStart ? (
        <Handle type="source" position={Position.Right} isConnectable={!readOnly} className="wf-handle" />
      ) : (
        <Handle type="target" position={Position.Left} isConnectable={!readOnly} className="wf-handle" />
      )}
    </div>
  );
}

export const AnchorNode = memo(AnchorNodeView);
