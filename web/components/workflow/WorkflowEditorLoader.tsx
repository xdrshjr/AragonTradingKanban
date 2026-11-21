'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useEffect } from 'react';

import { ApiError } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import { copyWorkflow } from '@/lib/workflow/copy.workflow';
import { useWorkflowStore } from '@/lib/workflow/store';

import { ErrorState } from '../admin/AdminPrimitives';
import { Skeleton } from '../ui/Skeleton';
import { WorkflowEditor } from './WorkflowEditor';

const t = copyWorkflow.editor;

/**
 * 取一次模板详情 → 装进 store → 渲染编辑器。此后 store 是唯一真相（乐观应用 + 保存队列），
 * 所以这里关掉 react-query 的自动重取，避免它把编辑中的草稿冲掉。
 */
export function WorkflowEditorLoader({ templateId }: { templateId: string }) {
  const query = useQuery({
    queryKey: ['admin', 'workflows', 'detail', templateId],
    queryFn: () => adminApi.workflows.get(templateId),
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  const load = useWorkflowStore((state) => state.load);
  const reset = useWorkflowStore((state) => state.reset);
  const ready = useWorkflowStore((state) => state.loaded && state.templateId === templateId);

  useEffect(() => {
    if (query.data) load(query.data);
  }, [query.data, load]);
  useEffect(() => reset, [reset]);

  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <div className="mx-auto max-w-[480px] p-8 text-center" role="alert">
        <p className="text-[13px] text-[var(--text-secondary)]">{t.notFound}</p>
        <Link href="/admin/workflows" className="mt-3 inline-block text-[12.5px] text-[var(--accent)]">
          {t.back}
        </Link>
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="mx-auto max-w-[560px] p-8">
        <ErrorState error={query.error} title={t.loadFailed} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  if (!ready) {
    return (
      <div role="status" aria-busy="true" aria-label={t.loading} className="space-y-3 p-6">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-[60vh] w-full" />
      </div>
    );
  }
  return <WorkflowEditor />;
}
