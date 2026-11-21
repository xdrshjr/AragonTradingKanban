'use client';

import { AlertTriangle, ChevronDown, ChevronUp, XOctagon } from 'lucide-react';
import dynamic from 'next/dynamic';

import { cn } from '@/lib/cn';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { DockTab } from '@/lib/workflow/types';

import { PlanPreview } from './PlanPreview';
import { ProblemsPanel } from './ProblemsPanel';
import { UsagePanel } from './UsagePanel';

// 版本页签带只读画布与确认对话框，按需加载。
const VersionsPanel = dynamic(() => import('./VersionsPanel').then((module) => module.VersionsPanel), { ssr: false });

const TABS: { id: DockTab; label: string }[] = [
  { id: 'problems', label: canvasCopy.dock.problems },
  { id: 'plan', label: canvasCopy.dock.plan },
  { id: 'versions', label: canvasCopy.dock.versions },
  { id: 'usage', label: canvasCopy.dock.usage },
];

function CountBadges({ errors, warnings }: { errors: number; warnings: number }) {
  return (
    <span className="ml-1 inline-flex items-center gap-1.5 text-[10.5px]">
      {errors > 0 ? (
        <span className="inline-flex items-center gap-0.5 text-[var(--danger)]" title={canvasCopy.node.errors(errors)}>
          <XOctagon aria-hidden="true" className="size-3" />
          {errors}
          <span className="sr-only">{canvasCopy.node.errors(errors)}</span>
        </span>
      ) : null}
      {warnings > 0 ? (
        <span className="inline-flex items-center gap-0.5 text-[var(--warn)]" title={canvasCopy.node.warnings(warnings)}>
          <AlertTriangle aria-hidden="true" className="size-3" />
          {warnings}
          <span className="sr-only">{canvasCopy.node.warnings(warnings)}</span>
        </span>
      ) : null}
    </span>
  );
}

function TabBody({ tab }: { tab: DockTab }) {
  if (tab === 'problems') return <ProblemsPanel />;
  if (tab === 'plan') return <PlanPreview />;
  if (tab === 'versions') return <VersionsPanel />;
  return <UsagePanel />;
}

/**
 * 底部工具窗：问题 / 预览 / 版本 / 使用。**默认收起为 28px 标题条**（问题计数徽标常显），
 * 点击标题条或页签展开（状态栏的计数同样调用 `setDockTab`，它会展开工具窗）。
 */
export function BottomDock() {
  const panes = useWorkflowStore((state) => state.panes);
  const validation = useWorkflowStore((state) => state.validation);
  const togglePane = useWorkflowStore((state) => state.togglePane);
  const setDockTab = useWorkflowStore((state) => state.setDockTab);
  const expanded = panes.dock;
  const Chevron = expanded ? ChevronDown : ChevronUp;

  return (
    <section aria-label={canvasCopy.dock.ariaLabel} className="flex h-full min-h-0 flex-col bg-[var(--bg-surface-1)]" data-expanded={expanded}>
      <header className="flex h-7 shrink-0 items-center border-b border-[var(--border-subtle)] bg-[var(--bg-surface-2)] pl-1 pr-1">
        <div role="tablist" aria-label={canvasCopy.dock.ariaLabel} className="flex min-w-0 flex-1 items-stretch gap-0.5">
          {TABS.map((tab) => {
            const active = expanded && panes.dockTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={`wf-dock-tab-${tab.id}`}
                aria-selected={active}
                aria-controls="wf-dock-panel"
                data-testid={`wf-dock-tab-${tab.id}`}
                onClick={() => setDockTab(tab.id)}
                className={cn(
                  'flex items-center rounded-t-[var(--radius-sm)] px-2.5 text-[12px] outline-none transition-colors focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)]',
                  active
                    ? 'border-b-2 border-[var(--accent)] font-medium text-[var(--text-primary)]'
                    : 'border-b-2 border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
                )}
              >
                {tab.label}
                {tab.id === 'problems' && validation ? (
                  <CountBadges errors={validation.errorCount} warnings={validation.warningCount} />
                ) : null}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={expanded ? canvasCopy.dock.collapse : canvasCopy.dock.expand}
          title={expanded ? canvasCopy.dock.collapse : canvasCopy.dock.expand}
          data-testid="wf-dock-toggle"
          onClick={() => togglePane('dock')}
          className="inline-flex size-5 items-center justify-center rounded-[var(--radius-sm)] text-[var(--text-tertiary)] outline-none hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)]"
        >
          <Chevron className="size-3.5" aria-hidden="true" />
        </button>
      </header>
      {expanded ? (
        <div id="wf-dock-panel" role="tabpanel" aria-labelledby={`wf-dock-tab-${panes.dockTab}`} className="min-h-0 flex-1 overflow-auto">
          <TabBody tab={panes.dockTab} />
        </div>
      ) : null}
    </section>
  );
}
