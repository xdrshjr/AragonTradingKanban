'use client';

import { ChevronRight, ListTree, StickyNote } from 'lucide-react';
import { useMemo } from 'react';

import { cn } from '@/lib/cn';
import { issuesByEntity, planKeys } from '@/lib/workflow/canvasModel';
import { canvasCopy, ROLE_LABELS } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { WorkflowDoc, WorkflowNode } from '@/lib/workflow/types';

import { ToolWindow, ToolWindowButton } from './ToolWindow';

interface Phase {
  level: number;
  nodes: { node: WorkflowNode; key: string }[];
}

/** 把 step/gate 按阶段分组（与编译器同一规则：层级 → 文档序）。 */
export function groupPhases(doc: WorkflowDoc): { phases: Phase[]; stray: WorkflowNode[]; notes: WorkflowNode[] } {
  const keys = planKeys(doc);
  const byLevel = new Map<number, Phase>();
  for (const node of doc.nodes) {
    const info = keys[node.id];
    if (!info) continue;
    const phase = byLevel.get(info.level) ?? { level: info.level, nodes: [] };
    phase.nodes.push({ node, key: info.key });
    byLevel.set(info.level, phase);
  }
  const reachable = reachableFromStart(doc);
  const phases = [...byLevel.values()]
    .map((phase) => ({ ...phase, nodes: phase.nodes.filter((entry) => reachable.has(entry.node.id)) }))
    .filter((phase) => phase.nodes.length > 0)
    .sort((a, b) => a.level - b.level);
  const stray = doc.nodes.filter((node) => keys[node.id] && !reachable.has(node.id));
  return { phases, stray, notes: doc.nodes.filter((node) => node.kind === 'note') };
}

function reachableFromStart(doc: WorkflowDoc): Set<string> {
  const seen = new Set<string>();
  const stack = doc.nodes.filter((node) => node.kind === 'start').map((node) => node.id);
  while (stack.length > 0) {
    const id = stack.pop();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    for (const edge of doc.edges) if (edge.source === id) stack.push(edge.target);
  }
  return seen;
}

interface RowProps {
  node: WorkflowNode;
  planKey: string | null;
  selected: boolean;
  errors: number;
  onPick: (id: string) => void;
}

function Row({ node, planKey, selected, errors, onPick }: RowProps) {
  return (
    <li>
      <button
        type="button"
        aria-current={selected || undefined}
        data-testid={`wf-structure-${node.id}`}
        onClick={() => onPick(node.id)}
        className={cn(
          'relative flex h-6 w-full items-center gap-1.5 rounded-sm pl-5 pr-2 text-left text-[12px] outline-none',
          'hover:bg-[var(--bg-hover)] focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)]',
          selected && 'bg-[var(--bg-active)]',
        )}
      >
        {selected ? <span aria-hidden="true" className="absolute inset-y-0 left-0 w-0.5 bg-[var(--accent)]" /> : null}
        {node.kind === 'note' ? (
          <StickyNote aria-hidden="true" className="size-3 text-[var(--warn)]" />
        ) : (
          <span className="w-6 shrink-0 font-mono text-[10.5px] text-[var(--text-tertiary)]">{planKey}</span>
        )}
        <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{node.title || canvasCopy.node.untitled}</span>
        {node.kind === 'step' && node.config.role ? (
          <span className="shrink-0 text-[10px] text-[var(--text-tertiary)]">{ROLE_LABELS[node.config.role]}</span>
        ) : null}
        {errors > 0 ? (
          <span className="shrink-0 text-[10px] text-[var(--danger)]" title={canvasCopy.node.errors(errors)}>
            ⛔{errors}
          </span>
        ) : null}
      </button>
    </li>
  );
}

function PhaseGroup({ phase, selected, issues, onPick }: { phase: Phase; selected: Set<string>; issues: Record<string, { errors: number }>; onPick: (id: string) => void }) {
  return (
    <li>
      <details open>
        <summary className="flex h-6 cursor-pointer list-none items-center gap-1 px-2 text-[11.5px] font-medium text-[var(--text-secondary)] [&::-webkit-details-marker]:hidden">
          {canvasCopy.structure.phase(phase.level)}
        </summary>
        <ul>
          {phase.nodes.map((entry) => (
            <Row key={entry.node.id} node={entry.node} planKey={entry.key} selected={selected.has(entry.node.id)} errors={issues[entry.node.id]?.errors ?? 0} onPick={onPick} />
          ))}
        </ul>
      </details>
    </li>
  );
}

/** 结构树（Structure 工具窗）：阶段 → 步骤/检查点，点击在画布中选中。`icon` 模式只剩 28px 图标条。 */
export function StructurePane({ mode = 'docked', onOpen }: { mode?: 'docked' | 'icon'; onOpen?: () => void }) {
  const doc = useWorkflowStore((state) => state.doc);
  const validation = useWorkflowStore((state) => state.validation);
  const selection = useWorkflowStore((state) => state.selection);
  const setSelection = useWorkflowStore((state) => state.setSelection);
  const issues = useMemo(() => issuesByEntity(validation), [validation]);
  const groups = useMemo(() => (doc ? groupPhases(doc) : null), [doc]);
  if (!doc || !groups) return null;

  if (mode === 'icon') {
    return (
      <div className="flex h-full w-7 flex-col items-center border-r border-[var(--border-subtle)] bg-[var(--bg-surface-2)] pt-1">
        <ToolWindowButton label={canvasCopy.structure.expand} onClick={() => onOpen?.()}>
          <ListTree className="size-3.5" />
        </ToolWindowButton>
      </div>
    );
  }

  const selected = new Set(selection.nodes);
  const pick = (id: string): void => setSelection({ nodes: [id], edges: [] });
  const empty = groups.phases.length === 0 && groups.stray.length === 0;
  return (
    <ToolWindow title={canvasCopy.structure.title} icon={<ListTree className="size-3.5" />} label={canvasCopy.structure.title}>
      {empty ? <p className="px-3 py-3 text-[12px] text-[var(--text-tertiary)]">{canvasCopy.structure.empty}</p> : null}
      <ul className="py-1">
        {groups.phases.map((phase) => (
          <PhaseGroup key={phase.level} phase={phase} selected={selected} issues={issues} onPick={pick} />
        ))}
        {groups.stray.length > 0 ? (
          <li>
            <div className="flex h-6 items-center gap-1 px-2 text-[11.5px] font-medium text-[var(--warn)]">
              <ChevronRight aria-hidden="true" className="size-3" />
              {canvasCopy.structure.unreachable}
            </div>
            <ul>
              {groups.stray.map((node) => (
                <Row key={node.id} node={node} planKey={planKeys(doc)[node.id]?.key ?? null} selected={selected.has(node.id)} errors={issues[node.id]?.errors ?? 0} onPick={pick} />
              ))}
            </ul>
          </li>
        ) : null}
        {groups.notes.length > 0 ? (
          <li>
            <div className="flex h-6 items-center px-2 text-[11.5px] font-medium text-[var(--text-secondary)]">{canvasCopy.structure.notes}</div>
            <ul>
              {groups.notes.map((node) => (
                <Row key={node.id} node={node} planKey={null} selected={selected.has(node.id)} errors={0} onPick={pick} />
              ))}
            </ul>
          </li>
        ) : null}
      </ul>
    </ToolWindow>
  );
}
