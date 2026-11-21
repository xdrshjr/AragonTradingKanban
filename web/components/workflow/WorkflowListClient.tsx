'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { copyWorkflow, fillText } from '@/lib/workflow/copy.workflow';
import type { CreateFrom, TemplateSummary } from '@/lib/workflow/types';

import { AdminPageHeader, EmptyState, ErrorState, TableSkeleton } from '../admin/AdminPrimitives';
import { adminKeys } from '../admin/keys';
import { notifyFailure } from '../admin/UserActionDialogs';
import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { notify } from '../ui/Toast';
import { CreateWorkflowDialog } from './CreateWorkflowDialog';
import { WorkflowListTable } from './WorkflowListTable';

const t = copyWorkflow.list;

interface CreateRequest {
  initialFrom?: CreateFrom;
  initialName?: string;
}

/** `/admin/workflows`：模板列表、总开关、新建 / 复制 / 设默认 / 归档。 */
export function WorkflowListClient() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: adminKeys.workflows, queryFn: () => adminApi.workflows.list() });
  const [creating, setCreating] = useState<CreateRequest | null>(null);
  const [defaulting, setDefaulting] = useState<TemplateSummary | null>(null);
  const [archiving, setArchiving] = useState<TemplateSummary | null>(null);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: adminKeys.workflows });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => adminApi.workflows.setEnabled(enabled),
    onSuccess: () => {
      notify.success(t.enabledSaved);
      invalidate();
    },
    onError: notifyFailure,
  });

  const create = useMutation({
    mutationFn: (body: { name: string; description: string; from: CreateFrom }) => adminApi.workflows.create(body),
    onSuccess: (detail) => {
      notify.success(fillText(t.cloned, { name: detail.meta.name }));
      invalidate();
      router.push(`/admin/workflows/${encodeURIComponent(detail.meta.id)}`);
    },
    onError: notifyFailure,
  });

  const makeDefault = useMutation({
    mutationFn: (template: TemplateSummary) => adminApi.workflows.setDefault(template.id),
    onSuccess: (_data, template) => {
      notify.success(fillText(t.defaultSaved, { name: template.name }));
      void queryClient.invalidateQueries({ queryKey: adminKeys.workflowDefault });
      invalidate();
    },
    onError: notifyFailure,
  });

  const archive = useMutation({
    mutationFn: (template: TemplateSummary) => adminApi.workflows.archive(template.id),
    onSuccess: (_data, template) => {
      notify.success(fillText(t.archived, { name: template.name }));
      invalidate();
    },
    onError: notifyFailure,
  });

  const data = query.data;
  const items = data?.items ?? [];
  const busyId = makeDefault.isPending ? (makeDefault.variables?.id ?? null) : archive.isPending ? (archive.variables?.id ?? null) : null;

  return (
    <div>
      <AdminPageHeader
        title={t.title}
        subtitle={t.subtitle}
        actions={
          <Button
            variant="primary"
            leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
            onClick={() => setCreating({})}
          >
            {t.create}
          </Button>
        }
      />

      {data ? (
        <label
          htmlFor="workflow-enabled-switch"
          className="mb-4 flex cursor-pointer items-center gap-3 rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3.5 py-2.5 text-[12.5px]"
        >
          <input
            id="workflow-enabled-switch"
            type="checkbox"
            role="switch"
            checked={data.enabled}
            disabled={toggle.isPending}
            onChange={(event) => toggle.mutate(event.target.checked)}
            className="size-4 accent-[var(--accent)]"
          />
          <span className="font-medium text-[var(--text-primary)]">{t.enabledLabel}</span>
          <span className="text-[11.5px] text-[var(--text-tertiary)]">{data.enabled ? t.enabledOn : t.enabledOff}</span>
        </label>
      ) : null}

      {query.isError ? (
        <ErrorState error={query.error} title={t.loadFailed} onRetry={() => void query.refetch()} />
      ) : !data ? (
        <TableSkeleton />
      ) : items.length === 0 ? (
        <EmptyState
          title={t.empty}
          hint={t.emptyHint}
          action={
            <Button variant="primary" onClick={() => setCreating({ initialFrom: 'builtin' })}>
              {t.create}
            </Button>
          }
        />
      ) : (
        <WorkflowListTable
          items={items}
          actions={{
            busyId,
            onClone: (template) =>
              setCreating({ initialFrom: { templateId: template.id }, initialName: `${template.name} 副本`.slice(0, 60) }),
            onSetDefault: setDefaulting,
            onArchive: setArchiving,
          }}
        />
      )}

      <CreateWorkflowDialog
        open={creating !== null}
        onOpenChange={(open) => !open && setCreating(null)}
        templates={items}
        initialFrom={creating?.initialFrom}
        initialName={creating?.initialName}
        loading={create.isPending}
        onSubmit={(body) => create.mutate(body)}
      />

      <ConfirmDialog
        open={defaulting !== null}
        onOpenChange={(open) => !open && setDefaulting(null)}
        title={fillText(t.setDefaultTitle, { name: defaulting?.name ?? '' })}
        description={t.setDefaultBody}
        confirmLabel={t.setDefaultConfirm}
        tone="primary"
        onConfirm={async () => {
          if (defaulting) await makeDefault.mutateAsync(defaulting).catch(() => undefined);
          setDefaulting(null);
        }}
      />

      <ConfirmDialog
        open={archiving !== null}
        onOpenChange={(open) => !open && setArchiving(null)}
        title={fillText(t.archiveTitle, { name: archiving?.name ?? '' })}
        description={t.archiveBody}
        confirmLabel={t.archiveConfirm}
        onConfirm={async () => {
          if (archiving) await archive.mutateAsync(archiving).catch(() => undefined);
          setArchiving(null);
        }}
      />
    </div>
  );
}
