'use client';

import { AlertTriangle, Bot, CheckCircle2, CloudOff, Loader2, Lock, XCircle } from 'lucide-react';
import { useMemo } from 'react';

import { cn } from '@/lib/cn';
import { effectiveMaxTurns, estimateTurns } from '@/lib/workflow/budget';
import { copyWorkflow, fillText } from '@/lib/workflow/copy.workflow';
import { countByKind } from '@/lib/workflow/ops';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { SaveState } from '@/lib/workflow/syncEngine';

const t = copyWorkflow.status;

function SaveIndicator({ state }: { state: SaveState }) {
  const icons: Record<SaveState, React.ReactNode> = {
    saved: <CheckCircle2 className="size-3 text-[var(--text-tertiary)]" aria-hidden="true" />,
    saving: <Loader2 className="size-3 animate-spin text-[var(--text-tertiary)]" aria-hidden="true" />,
    offline: <CloudOff className="size-3 text-[var(--warn)]" aria-hidden="true" />,
    error: <XCircle className="size-3 text-[var(--danger)]" aria-hidden="true" />,
  };
  return (
    <span role="status" data-save-state={state} className="flex items-center gap-1">
      {icons[state]}
      {t[state]}
    </span>
  );
}

export interface EditorStatusBarProps {
  /** 窄屏下右栏是抽屉：状态栏常驻一个开关按钮。 */
  showAssistToggle?: boolean;
  onToggleAssist?: () => void;
}

/** 24px 状态栏：保存状态 · rev · 步骤/检查点 · 估算回合 · 问题计数 · 只读锁。 */
export function EditorStatusBar({ showAssistToggle = false, onToggleAssist }: EditorStatusBarProps) {
  const doc = useWorkflowStore((state) => state.doc);
  const revision = useWorkflowStore((state) => state.revision);
  const saveState = useWorkflowStore((state) => state.saveState);
  const readOnly = useWorkflowStore((state) => state.readOnly);
  const validation = useWorkflowStore((state) => state.validation);
  const setDockTab = useWorkflowStore((state) => state.setDockTab);

  const counts = useMemo(() => (doc ? countByKind(doc) : { steps: 0, gates: 0 }), [doc]);
  const estimate = useMemo(() => (doc ? { est: estimateTurns(doc), max: effectiveMaxTurns(doc) } : null), [doc]);
  const tight = estimate !== null && estimate.est > estimate.max * 0.8;

  return (
    <footer
      aria-label="编辑器状态栏"
      className="flex h-6 shrink-0 items-center gap-3 border-t border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-3 text-[11px] text-[var(--text-tertiary)]"
    >
      <SaveIndicator state={saveState} />
      <span className="mono">{fillText(t.rev, { n: revision })}</span>
      <span>{fillText(t.steps, { n: counts.steps })}</span>
      <span>{fillText(t.gates, { n: counts.gates })}</span>
      {estimate ? (
        <span className={cn(tight && 'text-[var(--warn)]')} title="启发式估算，不是承诺">
          {fillText(t.budget, { est: estimate.est, max: estimate.max })}
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => setDockTab('problems')}
        className="flex items-center gap-2 rounded-[var(--radius-sm)] px-1 outline-none hover:text-[var(--text-primary)] focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)]"
        aria-label={`${t.problems}：${fillText(t.errors, { n: validation?.errorCount ?? 0 })}，${fillText(t.warnings, { n: validation?.warningCount ?? 0 })}`}
      >
        <span className={cn('flex items-center gap-0.5', (validation?.errorCount ?? 0) > 0 && 'text-[var(--danger)]')}>
          <XCircle className="size-3" aria-hidden="true" />
          {validation?.errorCount ?? 0}
        </span>
        <span className={cn('flex items-center gap-0.5', (validation?.warningCount ?? 0) > 0 && 'text-[var(--warn)]')}>
          <AlertTriangle className="size-3" aria-hidden="true" />
          {validation?.warningCount ?? 0}
        </span>
      </button>
      <span className="ml-auto flex items-center gap-3">
        {readOnly ? (
          <span className="flex items-center gap-1">
            <Lock className="size-3" aria-hidden="true" />
            {t.readonly}
          </span>
        ) : null}
        {showAssistToggle ? (
          <button
            type="button"
            onClick={onToggleAssist}
            title="⌘J"
            className="flex items-center gap-1 rounded-[var(--radius-sm)] px-1 outline-none hover:text-[var(--text-primary)] focus-visible:ring-1 focus-visible:ring-[var(--accent-ring)]"
          >
            <Bot className="size-3" aria-hidden="true" />
            {copyWorkflow.editor.openAssist}
          </button>
        ) : null}
      </span>
    </footer>
  );
}
