'use client';

import { type Node, type NodeProps } from '@xyflow/react';
import { StickyNote } from 'lucide-react';
import { memo } from 'react';

import { cn } from '@/lib/cn';

import type { FlowNodeData } from './nodeData';

type NoteFlowNode = Node<FlowNodeData, 'note'>;

/** 注释：便签感的弱色卡，无手柄（不参与连线，也不进编译）。 */
function NoteNodeView({ data, selected }: NodeProps<NoteFlowNode>) {
  const { node, highlighted } = data;
  const body = node.config.body ?? '';
  return (
    <div
      data-testid={`wf-node-${node.id}`}
      data-kind="note"
      className={cn(
        'wf-node w-[190px] rounded-[var(--radius-sm)] border border-dashed bg-[var(--warn-weak)] px-2.5 py-1.5 text-[11.5px]',
        selected ? 'border-[var(--accent)] ring-2 ring-[var(--accent-ring)]/50' : 'border-[var(--warn)]/40',
        highlighted && 'wf-pulse',
      )}
    >
      <div className="flex items-center gap-1 text-[var(--warn)]">
        <StickyNote aria-hidden="true" className="size-3" />
        <span className="truncate font-medium">{node.title}</span>
      </div>
      {body ? <p className="mt-0.5 line-clamp-3 whitespace-pre-wrap text-[var(--text-secondary)]">{body}</p> : null}
    </div>
  );
}

export const NoteNode = memo(NoteNodeView);
