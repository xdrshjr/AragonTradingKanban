'use client';

import { Copy, Lock, Pencil, Star, Trash2 } from 'lucide-react';
import Link from 'next/link';

import { relativeTime } from '@/lib/format';
import { copyWorkflow, fillText } from '@/lib/workflow/copy.workflow';
import type { TemplateSummary } from '@/lib/workflow/types';

import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const t = copyWorkflow.list;

export interface RowActions {
  onClone: (template: TemplateSummary) => void;
  onSetDefault: (template: TemplateSummary) => void;
  onArchive: (template: TemplateSummary) => void;
  /** 正在执行写操作的模板 id（禁用其按钮，防止重复提交）。 */
  busyId: string | null;
}

function StatusCell({ template }: { template: TemplateSummary }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      {template.isDefault ? (
        <Badge tone="accent" icon={<Star className="size-2.5" aria-hidden="true" />}>
          {t.badges.default}
        </Badge>
      ) : null}
      {template.isBuiltin ? (
        <Badge tone="neutral" icon={<Lock className="size-2.5" aria-hidden="true" />}>
          {t.badges.builtin}
        </Badge>
      ) : null}
      {template.latestVersion === null ? <Badge tone="warn">{t.badges.unpublished}</Badge> : null}
      {template.latestVersion !== null && template.dirty ? <Badge tone="info">{t.badges.dirty}</Badge> : null}
    </div>
  );
}

function Row({ template, actions }: { template: TemplateSummary; actions: RowActions }) {
  const busy = actions.busyId === template.id;
  const canSetDefault = !template.isDefault && template.latestVersion !== null;
  const canArchive = !template.isDefault && !template.isBuiltin;
  return (
    <tr className="border-b border-[var(--border-subtle)] last:border-0 hover:bg-[var(--bg-hover)]">
      <td className="px-3 py-2">
        <Link
          href={`/admin/workflows/${encodeURIComponent(template.id)}`}
          className="text-[13px] font-medium text-[var(--text-primary)] hover:text-[var(--accent)]"
        >
          {template.name}
        </Link>
        {template.description ? (
          <div className="max-w-[360px] truncate text-[11.5px] text-[var(--text-tertiary)]">{template.description}</div>
        ) : null}
      </td>
      <td className="px-3 py-2">
        <StatusCell template={template} />
      </td>
      <td className="mono px-3 py-2 text-[12px] text-[var(--text-secondary)]">
        {template.latestVersion === null ? t.noVersion : fillText(t.versionLabel, { n: template.latestVersion })}
      </td>
      <td className="num px-3 py-2 text-[12px] text-[var(--text-secondary)]">{template.stepCount}</td>
      <td className="px-3 py-2 text-[12px] text-[var(--text-tertiary)]">
        {relativeTime(template.updatedAt)}
        {template.updatedByName ? ` · ${template.updatedByName}` : ''}
      </td>
      <td className="num px-3 py-2 text-[12px] text-[var(--text-secondary)]">
        {fillText(t.runs, { n: template.runs30d })}
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center justify-end gap-1">
          <Link
            href={`/admin/workflows/${encodeURIComponent(template.id)}`}
            aria-label={`${t.actions.edit} ${template.name}`}
            className="inline-flex h-7 items-center gap-1 rounded-[var(--radius-sm)] px-2 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
          >
            <Pencil className="size-3" aria-hidden="true" />
            {t.actions.edit}
          </Link>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            aria-label={`${t.actions.clone} ${template.name}`}
            leadingIcon={<Copy className="size-3" aria-hidden="true" />}
            onClick={() => actions.onClone(template)}
          >
            {t.actions.clone}
          </Button>
          {canSetDefault ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              aria-label={`${t.actions.setDefault} ${template.name}`}
              leadingIcon={<Star className="size-3" aria-hidden="true" />}
              onClick={() => actions.onSetDefault(template)}
            >
              {t.actions.setDefault}
            </Button>
          ) : null}
          {canArchive ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              aria-label={`${t.actions.archive} ${template.name}`}
              leadingIcon={<Trash2 className="size-3" aria-hidden="true" />}
              onClick={() => actions.onArchive(template)}
            >
              {t.actions.archive}
            </Button>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

/** IDE 风格的紧凑表格：列 = 名称 / 状态 / 最新版本 / 步骤数 / 最近编辑 / 近 30 天使用 / 行操作。 */
export function WorkflowListTable({ items, actions }: { items: TemplateSummary[]; actions: RowActions }) {
  const heads = [t.columns.name, t.columns.status, t.columns.version, t.columns.steps, t.columns.updated, t.columns.usage];
  return (
    <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)]">
      <table className="w-full min-w-[760px] border-collapse text-left">
        <thead>
          <tr className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface-2)]">
            {heads.map((head) => (
              <th key={head} scope="col" className="px-3 py-1.5 text-[11.5px] font-medium text-[var(--text-tertiary)]">
                {head}
              </th>
            ))}
            <th scope="col" className="px-3 py-1.5">
              <span className="sr-only">操作</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((template) => (
            <Row key={template.id} template={template} actions={actions} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
