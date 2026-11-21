'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';

import { adminApi } from '@/lib/api.admin';
import { copyWorkflow } from '@/lib/workflow/copy.workflow';
import {
  BP_DRAWER,
  BP_READONLY,
  SPLIT_DEFAULT,
  SPLIT_MAX,
  SPLIT_MIN,
  computeSplit,
  dragSplit,
  loadSplit,
  saveSplit,
  toCssVariables,
} from '@/lib/workflow/layout';
import { useWorkflowStore } from '@/lib/workflow/store';
import { useWorkflowSync } from '@/lib/workflow/useWorkflowSync';

import { notifyFailure } from '../admin/UserActionDialogs';
import { Sheet, SheetContent } from '../ui/Sheet';
import { Splitter } from '../ui/Splitter';
import { notify } from '../ui/Toast';
import AssistPanel from './AssistPanel';
import { EditorLeftRegion } from './EditorLeftRegion';
import { EditorStatusBar } from './EditorStatusBar';
import { EditorTitleBar } from './EditorTitleBar';
import { PublishDialog } from './PublishDialog';
import { useContainerSize } from './useContainerSize';
import { useEditorShortcuts } from './useEditorShortcuts';

const t = copyWorkflow.editor;

/** 把 store 里的一次性通知转成 toast（store 不直接依赖 UI 组件）。 */
function useNoticeToasts(): void {
  const notice = useWorkflowStore((state) => state.notice);
  useEffect(() => {
    if (!notice) return;
    if (notice.kind === 'error') notify.error(notice.text);
    else if (notice.kind === 'success') notify.success(notice.text);
    else notify.info(notice.text);
  }, [notice]);
}

/** 窄屏只读 + 有错误时自动展开一次问题面板（用户亲手收起过则尊重其偏好）。 */
function useShellEffects(width: number): void {
  const setNarrow = useWorkflowStore((state) => state.setNarrow);
  const errorCount = useWorkflowStore((state) => state.validation?.errorCount ?? 0);
  const dockPref = useWorkflowStore((state) => state.panePrefs.dock);
  const setDockTab = useWorkflowStore((state) => state.setDockTab);
  const autoOpened = useRef(false);

  useEffect(() => setNarrow(width < BP_READONLY), [width, setNarrow]);
  useEffect(() => {
    if (errorCount > 0 && dockPref === undefined && !autoOpened.current) {
      autoOpened.current = true;
      setDockTab('problems');
    }
  }, [errorCount, dockPref, setDockTab]);
}

function useSplitState(containerPx: number) {
  const [pct, setPct] = useState(SPLIT_DEFAULT);
  useEffect(() => setPct(loadSplit()), []);
  const apply = useCallback((next: number) => {
    setPct(next);
    saveSplit(next);
  }, []);
  return {
    pct,
    widths: computeSplit(containerPx, pct),
    onDelta: (delta: number) => apply(dragSplit(containerPx, pct, delta)),
    onReset: () => apply(SPLIT_DEFAULT),
  };
}

/** 内置模板的"复制为可编辑副本"：以当前模板为起点创建并跳转。 */
function useCloneToEdit(): () => void {
  const router = useRouter();
  const meta = useWorkflowStore((state) => state.meta);
  return useCallback(() => {
    if (!meta) return;
    adminApi.workflows
      .create({ name: `${meta.name} 副本`.slice(0, 60), from: { templateId: meta.id } })
      .then((detail) => router.push(`/admin/workflows/${encodeURIComponent(detail.meta.id)}`))
      .catch(notifyFailure);
  }, [meta, router]);
}

/**
 * 编辑器外壳（spec §3.10.2）：标题栏 · 左 70% 画布工作台 | 分隔条 | 右 30% AI 助手 · 状态栏。
 *
 * * 比例真源 `lib/workflow/layout.ts`：结果写成 CSS 变量 `--wf-left-w / --wf-right-w`；
 * * < 1100px 右栏收为抽屉（`⌘J`），< 768px 只读查看。
 */
export function WorkflowEditor() {
  const ref = useRef<HTMLDivElement>(null);
  const { width } = useContainerSize(ref);
  const split = useSplitState(width);
  const [assistOpen, setAssistOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const narrow = useWorkflowStore((state) => state.narrow);
  const announce = useWorkflowStore((state) => state.announce);
  const locked = useWorkflowStore((state) => state.lockedByTemplate);
  const cloneToEdit = useCloneToEdit();
  const drawer = width < BP_DRAWER;

  useWorkflowSync();
  useNoticeToasts();
  useShellEffects(width);
  useEditorShortcuts({
    onTogglePanel: useCallback(() => setAssistOpen((open) => !open), []),
    onPublish: useCallback(() => setPublishOpen(true), []),
  });

  return (
    <div
      ref={ref}
      data-testid="workflow-editor"
      style={toCssVariables(split.widths) as CSSProperties}
      className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)] text-[var(--text-primary)]"
    >
      <EditorTitleBar onPublish={() => setPublishOpen(true)} onClone={locked ? cloneToEdit : undefined} />
      {narrow && !locked ? (
        <div role="status" className="shrink-0 bg-[var(--bg-surface-2)] px-3 py-1 text-[12px] text-[var(--text-secondary)]">
          {t.mobileReadonly}
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1">
        <EditorLeftRegion
          leftPx={drawer ? width : split.widths.leftPx}
          className={drawer ? 'w-full' : 'w-[var(--wf-left-w)] shrink-0'}
        />
        {drawer ? null : (
          <>
            <Splitter
              value={split.widths.leftPct}
              min={SPLIT_MIN}
              max={SPLIT_MAX}
              onDelta={split.onDelta}
              onReset={split.onReset}
              label={t.split}
            />
            <aside
              aria-label={t.rightRegion}
              data-testid="wf-right"
              className="min-h-0 min-w-0 flex-1 border-l border-[var(--border-subtle)] bg-[var(--bg-surface-1)]"
            >
              <AssistPanel />
            </aside>
          </>
        )}
      </div>
      <EditorStatusBar showAssistToggle={drawer} onToggleAssist={() => setAssistOpen((open) => !open)} />
      {drawer ? (
        <Sheet open={assistOpen} onOpenChange={setAssistOpen}>
          <SheetContent title={t.rightRegion} className="min-[900px]:w-[400px]">
            <AssistPanel />
          </SheetContent>
        </Sheet>
      ) : null}
      <PublishDialog open={publishOpen} onOpenChange={setPublishOpen} />
      <div aria-live="polite" role="status" className="sr-only" data-testid="wf-announce">
        {announce}
      </div>
    </div>
  );
}
