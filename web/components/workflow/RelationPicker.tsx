'use client';

import { ChevronDown } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { connectionVerdict, downstreamOf, planKeys, upstreamOf } from '@/lib/workflow/canvasModel';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { makeEdge } from '@/lib/workflow/ops';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { WorkflowDoc, WorkflowNode, WorkflowOp } from '@/lib/workflow/types';

type Direction = 'upstream' | 'downstream';

interface PickerProps {
  doc: WorkflowDoc;
  node: WorkflowNode;
  direction: Direction;
  disabled: boolean;
}

/** 不依赖指针的连线方式：多选下拉勾选上游 / 下游，勾选 = 加边，取消 = 删边。 */
export function RelationPicker({ doc, node, direction, disabled }: PickerProps) {
  const dispatch = useWorkflowStore((state) => state.dispatch);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const keys = useMemo(() => planKeys(doc), [doc]);
  const linked = useMemo(
    () => new Set(direction === 'upstream' ? upstreamOf(doc, node.id) : downstreamOf(doc, node.id)),
    [doc, node.id, direction],
  );
  const candidates = doc.nodes.filter((other) => other.id !== node.id && other.kind !== 'note');
  const label = direction === 'upstream' ? canvasCopy.inspector.fieldUpstream : canvasCopy.inspector.fieldDownstream;

  const toggle = (other: WorkflowNode, checked: boolean): void => {
    const [source, target] = direction === 'upstream' ? [other.id, node.id] : [node.id, other.id];
    if (checked) {
      dispatch([{ op: 'add_edge', edge: makeEdge(source, target) }]);
      return;
    }
    const edge = doc.edges.find((candidate) => candidate.source === source && candidate.target === target);
    if (edge) dispatch([{ op: 'delete_edge', id: edge.id, expectRev: edge.rev } satisfies WorkflowOp]);
  };

  const summary =
    linked.size === 0
      ? canvasCopy.inspector.none
      : [...linked].map((id) => keys[id]?.key ?? doc.nodes.find((n) => n.id === id)?.title ?? id).join('、');

  return (
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false);
      }}
    >
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        disabled={disabled}
        data-testid={`wf-picker-${direction}`}
        onClick={() => setOpen((value) => !value)}
        className="flex h-7 w-full items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] px-2 text-left text-[12px] text-[var(--text-primary)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{summary}</span>
        <ChevronDown aria-hidden="true" className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
      </button>
      {open ? (
        <ul
          role="listbox"
          aria-multiselectable="true"
          aria-label={label}
          className="absolute z-30 mt-1 max-h-[200px] w-full overflow-auto rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]"
        >
          {candidates.map((other) => (
            <CandidateRow
              key={other.id}
              doc={doc}
              node={node}
              other={other}
              direction={direction}
              checked={linked.has(other.id)}
              planKey={keys[other.id]?.key}
              onToggle={toggle}
            />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

interface RowProps {
  doc: WorkflowDoc;
  node: WorkflowNode;
  other: WorkflowNode;
  direction: Direction;
  checked: boolean;
  planKey?: string;
  onToggle: (other: WorkflowNode, checked: boolean) => void;
}

function CandidateRow({ doc, node, other, direction, checked, planKey, onToggle }: RowProps) {
  const [source, target] = direction === 'upstream' ? [other.id, node.id] : [node.id, other.id];
  const verdict = checked ? { ok: true as const } : connectionVerdict(doc, source, target);
  const reason = verdict.ok ? undefined : canvasCopy.connect[verdict.reason];
  return (
    <li role="option" aria-selected={checked} aria-disabled={!verdict.ok} title={reason}>
      <label
        className={cn(
          'flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1 text-[12px] hover:bg-[var(--bg-hover)]',
          !verdict.ok && 'cursor-not-allowed opacity-45',
        )}
      >
        <input
          type="checkbox"
          checked={checked}
          disabled={!verdict.ok}
          onChange={(event) => onToggle(other, event.target.checked)}
          className="size-3.5 accent-[var(--accent)]"
        />
        <span className="w-7 font-mono text-[10.5px] text-[var(--text-tertiary)]">{planKey ?? ''}</span>
        <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{other.title || canvasCopy.node.untitled}</span>
      </label>
    </li>
  );
}
