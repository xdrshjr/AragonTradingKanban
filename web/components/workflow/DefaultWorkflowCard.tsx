'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';

import { adminApi } from '@/lib/api.admin';
import { copyWorkflow, fillText } from '@/lib/workflow/copy.workflow';

import { AdminCard } from '../admin/AdminPrimitives';
import { adminKeys } from '../admin/keys';

const t = copyWorkflow.settingsCard;

/** `/admin/settings` 里的只读卡片：当前默认研究流程是什么，以及去哪管理。 */
export function DefaultWorkflowCard() {
  const query = useQuery({
    queryKey: adminKeys.workflowDefault,
    queryFn: () => adminApi.workflows.defaultSummary(),
    retry: false,
  });
  const summary = query.data;

  let body: string;
  if (query.isError) body = t.unavailable;
  else if (!summary) body = t.loading;
  else if (!summary.enabled) body = t.disabled;
  else {
    const version = summary.version === null ? t.noVersion : fillText(t.version, { n: summary.version });
    body = fillText(t.current, { name: summary.name, version });
  }

  return (
    <AdminCard title={t.title} headingId="settings-workflow">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]" data-testid="default-workflow-text">
          {body}
        </p>
        <Link
          href="/admin/workflows"
          className="inline-flex h-7 items-center rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] px-2.5 text-[12px] font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-surface-3)]"
        >
          {t.manage}
        </Link>
      </div>
    </AdminCard>
  );
}
