'use client';

import dynamic from 'next/dynamic';
import { useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copyWorkflow } from '@/lib/workflow/copy.workflow';
import {
  DOCK_BAR_H,
  DOCK_H,
  INSPECTOR_W,
  STRUCTURE_W,
  computeInner,
  type InnerLayout,
} from '@/lib/workflow/layout';
import { useWorkflowStore } from '@/lib/workflow/store';

import { BottomDock } from './BottomDock';
import { Inspector } from './Inspector';
import { StructurePane } from './StructurePane';
import { useContainerSize } from './useContainerSize';

const WorkflowCanvas = dynamic(() => import('./WorkflowCanvas'), {
  ssr: false,
  loading: () => (
    <div role="status" className="flex h-full items-center justify-center text-[12px] text-[var(--text-tertiary)]">
      {copyWorkflow.editor.canvasLoading}
    </div>
  ),
});

function useInnerLayout(leftPx: number, heightPx: number): InnerLayout {
  const panes = useWorkflowStore((state) => state.panes);
  const prefs = useWorkflowStore((state) => state.panePrefs);
  return computeInner({
    leftPx,
    heightPx,
    prefs: { structure: prefs.structure, inspector: prefs.inspector, dock: panes.dock },
  });
}

function OverlayPanel({ side, width, onEscape, children }: { side: 'left' | 'right'; width: number; onEscape: () => void; children: React.ReactNode }) {
  return (
    <div
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onEscape();
        }
      }}
      style={{ width }}
      className={cn(
        'absolute inset-y-0 z-20 border-[var(--border-strong)] bg-[var(--bg-surface-1)] shadow-[var(--shadow-pop)]',
        side === 'left' ? 'left-0 border-r' : 'right-0 border-l',
      )}
    >
      {children}
    </div>
  );
}

export interface EditorLeftRegionProps {
  /** 左栏宽度（px）；抽屉模式下等于容器宽。 */
  leftPx: number;
  className?: string;
}

/** 左 70%：画布工作台。三栏并排与让位规则见 `computeInner`（spec §3.10.2，RW-5）。 */
export function EditorLeftRegion({ leftPx, className }: EditorLeftRegionProps) {
  const ref = useRef<HTMLDivElement>(null);
  const { height } = useContainerSize(ref);
  const inner = useInnerLayout(leftPx, height);
  const [structureOpen, setStructureOpen] = useState(false);
  const hasSelection = useWorkflowStore((state) => state.selection.nodes.length + state.selection.edges.length > 0);
  const setSelection = useWorkflowStore((state) => state.setSelection);
  const dockBar = inner.dock === 'collapsed';

  return (
    <div
      ref={ref}
      role="region"
      aria-label={copyWorkflow.editor.leftRegion}
      data-testid="wf-left"
      data-structure={inner.structure}
      data-inspector={inner.inspector}
      data-dock={inner.dock}
      className={cn('relative flex min-h-0 min-w-0 flex-col', className)}
    >
      <div className="relative flex min-h-0 flex-1">
        {inner.structure === 'docked' ? (
          <div className="shrink-0 border-r border-[var(--border-subtle)]" style={{ width: STRUCTURE_W }}>
            <StructurePane />
          </div>
        ) : (
          <StructurePane mode="icon" onOpen={() => setStructureOpen((open) => !open)} />
        )}
        <div className="relative min-w-0 flex-1" data-testid="wf-canvas-region">
          <WorkflowCanvas />
          {inner.structure === 'icon' && structureOpen ? (
            <OverlayPanel side="left" width={STRUCTURE_W} onEscape={() => setStructureOpen(false)}>
              <StructurePane />
            </OverlayPanel>
          ) : null}
          {inner.inspector === 'overlay' && hasSelection ? (
            <OverlayPanel side="right" width={INSPECTOR_W} onEscape={() => setSelection({ nodes: [], edges: [] })}>
              <Inspector />
            </OverlayPanel>
          ) : null}
        </div>
        {inner.inspector === 'docked' ? (
          <div className="shrink-0 border-l border-[var(--border-subtle)]" style={{ width: INSPECTOR_W }}>
            <Inspector />
          </div>
        ) : null}
      </div>
      <div
        data-testid="wf-dock"
        className={cn(
          'shrink-0 border-t border-[var(--border-subtle)]',
          inner.dock === 'overlay' && 'absolute inset-x-0 bottom-0 z-20 shadow-[var(--shadow-pop)]',
        )}
        style={{ height: dockBar ? DOCK_BAR_H : DOCK_H }}
      >
        <BottomDock />
      </div>
    </div>
  );
}
