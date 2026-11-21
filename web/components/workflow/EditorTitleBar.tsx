'use client';

import { ChevronDown, ChevronLeft, Copy, FileSearch, Lock, Send } from 'lucide-react';
import Link from 'next/link';

import { copyWorkflow, fillText } from '@/lib/workflow/copy.workflow';
import { useWorkflowStore } from '@/lib/workflow/store';

import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const t = copyWorkflow.editor;

export interface EditorTitleBarProps {
  onPublish: () => void;
  /** 内置 / 归档模板：提供后显示【复制为可编辑副本】。 */
  onClone?: () => void;
}

/** 编辑器标题栏：‹ 研究流程 / 名称 [v3 已发布 · 草稿有未发布修改]   [版本▾] [预览提示词] [发布…]。 */
export function EditorTitleBar({ onPublish, onClone }: EditorTitleBarProps) {
  const meta = useWorkflowStore((state) => state.meta);
  const dirty = useWorkflowStore((state) => state.dirty);
  const readOnly = useWorkflowStore((state) => state.lockedByTemplate);
  const setDockTab = useWorkflowStore((state) => state.setDockTab);
  const errorCount = useWorkflowStore((state) => state.validation?.errorCount ?? 0);

  const latest = meta?.latestVersion ?? null;
  return (
    <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3">
      <Link
        href="/admin/workflows"
        className="flex items-center gap-0.5 text-[12px] text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
      >
        <ChevronLeft className="size-3.5" aria-hidden="true" />
        {t.back}
      </Link>
      <span aria-hidden="true" className="text-[var(--text-tertiary)]">
        /
      </span>
      <h1 className="min-w-0 truncate text-[13px] font-semibold tracking-tight" data-testid="workflow-title">
        {meta?.name ?? ''}
      </h1>
      {readOnly ? <Lock className="size-3 shrink-0 text-[var(--text-tertiary)]" aria-label={t.builtinReadonly} /> : null}
      <div className="flex min-w-0 items-center gap-1.5">
        {latest !== null ? <Badge tone="accent">{fillText(copyWorkflow.list.versionLabel, { n: latest })}</Badge> : <Badge tone="warn">{t.neverPublished}</Badge>}
        {latest !== null && dirty ? <Badge tone="info">{t.draftDirty}</Badge> : null}
        {meta?.isDefault ? <Badge tone="neutral">{copyWorkflow.list.badges.default}</Badge> : null}
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        {onClone ? (
          <Button size="sm" variant="accent-ghost" leadingIcon={<Copy className="size-3" aria-hidden="true" />} onClick={onClone}>
            {t.cloneToEdit}
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          trailingIcon={<ChevronDown className="size-3" aria-hidden="true" />}
          onClick={() => setDockTab('versions')}
        >
          {t.versions}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          leadingIcon={<FileSearch className="size-3" aria-hidden="true" />}
          onClick={() => setDockTab('plan')}
        >
          {t.previewPrompt}
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={readOnly}
          title={errorCount > 0 ? `${fillText(copyWorkflow.status.errors, { n: errorCount })}，无法发布` : '⌘⇧P'}
          leadingIcon={<Send className="size-3" aria-hidden="true" />}
          onClick={onPublish}
        >
          {t.publish}
        </Button>
      </div>
    </div>
  );
}
