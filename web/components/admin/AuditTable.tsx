'use client';

import { useInfiniteQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { useCan } from '@/lib/auth';
import { copy, fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { dateTime } from '@/lib/format';
import type { AuditQuery, AuditRow } from '@/lib/types';

import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { NativeSelect } from '../ui/Select';
import { AdminPageHeader, EmptyState, ErrorState, TableSkeleton } from './AdminPrimitives';
import { AUDIT_ACTIONS, auditActionLabel } from './auditLabels';
import { ExportLink } from './ExportLink';
import { adminKeys } from './keys';

const t = copyAdmin.audit;

interface DraftFilters {
  action: string;
  actor: string;
  target: string;
  from: string;
  to: string;
}

const EMPTY: DraftFilters = { action: '', actor: '', target: '', from: '', to: '' };

/** `YYYY-MM-DD`（本地日）→ 当天 00:00 / 23:59:59.999 的 ISO 时刻。接口只约定“时间范围”，这里发完整 ISO（见 F-17）。 */
function dayStart(day: string): string | undefined {
  if (!day) return undefined;
  const value = new Date(`${day}T00:00:00`);
  return Number.isNaN(value.getTime()) ? undefined : value.toISOString();
}

function dayEnd(day: string): string | undefined {
  if (!day) return undefined;
  const value = new Date(`${day}T23:59:59.999`);
  return Number.isNaN(value.getTime()) ? undefined : value.toISOString();
}

function toQuery(filters: DraftFilters): Omit<AuditQuery, 'before' | 'limit'> {
  return {
    action: filters.action || undefined,
    actor: filters.actor.trim() || undefined,
    target: filters.target.trim() || undefined,
    from: dayStart(filters.from),
    to: dayEnd(filters.to),
  };
}

/** 详情只展示服务端白名单后的字段，JSON 原样缩进，默认折叠。 */
function DetailCell({ row }: { row: AuditRow }) {
  const detail = row.detail;
  if (!detail || Object.keys(detail).length === 0) {
    return <span className="text-[var(--text-tertiary)]">{copyAdmin.common.emDash}</span>;
  }
  return (
    <details className="group">
      <summary
        aria-label={fill(t.detailLabel, { action: auditActionLabel(row.action) })}
        className="cursor-pointer select-none text-[11.5px] text-[var(--text-secondary)] underline-offset-2 hover:text-[var(--text-primary)] hover:underline"
      >
        {t.detailShow}
      </summary>
      <pre className="mono mt-1.5 max-w-[320px] overflow-x-auto whitespace-pre-wrap break-all rounded-[var(--radius-sm)] bg-[var(--bg-surface-2)] p-2 text-[11px] leading-relaxed text-[var(--text-secondary)]">
        {JSON.stringify(detail, null, 2)}
      </pre>
    </details>
  );
}

/** 审计日志：只读、筛选（动作 / 操作者 / 对象 / 时间范围）+ 游标分页。 */
export function AuditTable({ initialAction }: { initialAction?: string }) {
  const canExport = useCan('admin.audit.read');
  // ★Y5(c)：概览“安全事件”点进来时带 `?action=…`；只认词表里的动作（未知值静默忽略）。
  const start: DraftFilters =
    initialAction && AUDIT_ACTIONS.includes(initialAction) ? { ...EMPTY, action: initialAction } : EMPTY;
  const [draft, setDraft] = useState<DraftFilters>(start);
  const [applied, setApplied] = useState<DraftFilters>(start);
  const filters = useMemo(() => toQuery(applied), [applied]);

  const query = useInfiniteQuery({
    queryKey: adminKeys.audit(filters),
    queryFn: ({ pageParam }) => adminApi.adminAudit({ ...filters, before: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });
  const rows = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const filtered = Object.values(applied).some((value) => value !== '');

  const set = (key: keyof DraftFilters) => (value: string) => setDraft((current) => ({ ...current, [key]: value }));
  const apply = (event: React.FormEvent) => {
    event.preventDefault();
    setApplied(draft);
  };
  const reset = () => {
    setDraft(EMPTY);
    setApplied(EMPTY);
  };

  const label = 'mb-1 block text-[11.5px] text-[var(--text-tertiary)]';

  return (
    <div>
      <AdminPageHeader
        title={t.title}
        subtitle={t.subtitle}
        actions={
          canExport ? (
            <ExportLink
              href={adminApi.adminAuditExportUrl(filters)}
              label={t.export}
              hint={t.exportHint}
              started={t.exportStarted}
            />
          ) : null
        }
      />

      <form
        onSubmit={apply}
        aria-label={t.filters.label}
        className="mb-4 grid grid-cols-2 items-end gap-3 min-[900px]:grid-cols-[1.4fr_1fr_1fr_1fr_1fr_auto]"
      >
        <div>
          <label htmlFor="audit-action" className={label}>
            {t.filters.action}
          </label>
          <NativeSelect
            id="audit-action"
            value={draft.action}
            onChange={(event) => set('action')(event.target.value)}
            className="w-full"
          >
            <option value="">{t.filters.actionAll}</option>
            {AUDIT_ACTIONS.map((action) => (
              <option key={action} value={action}>
                {auditActionLabel(action)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div>
          <label htmlFor="audit-actor" className={label}>
            {t.filters.actor}
          </label>
          <Input
            id="audit-actor"
            value={draft.actor}
            onChange={(event) => set('actor')(event.target.value)}
            placeholder={t.filters.actorPlaceholder}
            autoComplete="off"
            spellCheck={false}
            className="font-mono"
          />
        </div>
        <div>
          <label htmlFor="audit-target" className={label}>
            {t.filters.target}
          </label>
          <Input
            id="audit-target"
            value={draft.target}
            onChange={(event) => set('target')(event.target.value)}
            placeholder={t.filters.targetPlaceholder}
            autoComplete="off"
            spellCheck={false}
            className="font-mono"
          />
        </div>
        <div>
          <label htmlFor="audit-from" className={label}>
            {t.filters.from}
          </label>
          <Input id="audit-from" type="date" value={draft.from} max={draft.to || undefined} onChange={(event) => set('from')(event.target.value)} />
        </div>
        <div>
          <label htmlFor="audit-to" className={label}>
            {t.filters.to}
          </label>
          <Input id="audit-to" type="date" value={draft.to} min={draft.from || undefined} onChange={(event) => set('to')(event.target.value)} />
        </div>
        <div className="col-span-2 flex items-center gap-2 min-[900px]:col-span-1">
          <Button type="submit" variant="secondary" className="h-9">
            {t.filters.apply}
          </Button>
          {filtered || Object.values(draft).some(Boolean) ? (
            <Button variant="ghost" className="h-9" onClick={reset}>
              {t.filters.reset}
            </Button>
          ) : null}
        </div>
      </form>

      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)]">
        {query.isError ? (
          <div className="p-4">
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          </div>
        ) : query.isPending ? (
          <TableSkeleton rows={8} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={t.empty}
            hint={t.emptyHint}
            action={
              filtered ? (
                <Button size="sm" variant="secondary" onClick={reset}>
                  {t.filters.reset}
                </Button>
              ) : null
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-[12.5px]">
              <caption className="sr-only">{t.title}</caption>
              <thead>
                <tr className="border-b border-[var(--border-subtle)] text-[11px] text-[var(--text-tertiary)]">
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.at}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.actor}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.action}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.target}</th>
                  <th scope="col" className="hidden px-3 py-2 font-medium min-[900px]:table-cell">{t.columns.ip}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.detail}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-[var(--border-subtle)] align-top last:border-0">
                    <td className="whitespace-nowrap px-3 py-2 text-[var(--text-tertiary)]">
                      <time dateTime={row.at}>{dateTime(row.at)}</time>
                    </td>
                    <td className="px-3 py-2">
                      <span className="mono text-[var(--text-secondary)]">{row.actorName ?? t.system}</span>
                      {row.actorRole ? (
                        <span className="ml-1.5 text-[10.5px] text-[var(--text-tertiary)]">
                          {(copy.auth.roles as Record<string, string>)[row.actorRole] ?? row.actorRole}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 text-[var(--text-primary)]">
                      {auditActionLabel(row.action)}
                      <div className="mono text-[10.5px] text-[var(--text-tertiary)]">{row.action}</div>
                    </td>
                    <td className="mono px-3 py-2 text-[var(--text-secondary)]">
                      {row.targetName ?? copyAdmin.common.emDash}
                    </td>
                    <td className="mono hidden px-3 py-2 text-[var(--text-tertiary)] min-[900px]:table-cell">
                      {row.ip ?? row.ipPrefix ?? copyAdmin.common.emDash}
                    </td>
                    <td className="px-3 py-2">
                      <DetailCell row={row} />
                      {row.requestId ? (
                        <div className="mono mt-1 text-[10px] text-[var(--text-tertiary)]">{row.requestId}</div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {query.hasNextPage ? (
        <div className="mt-3 text-center">
          <Button size="sm" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {copyAdmin.common.loadMore}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
