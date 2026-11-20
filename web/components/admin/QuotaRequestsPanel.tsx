'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { dateTime, relativeTime } from '@/lib/format';
import type { AdminQuotaRequest } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/Tabs';
import { Textarea } from '../ui/Textarea';
import { notify } from '../ui/Toast';
import { AdminPageHeader, EmptyState, ErrorState, TableSkeleton, UserCell } from './AdminPrimitives';
import { adminKeys } from './keys';
import { notifyFailure } from './UserActionDialogs';

const t = copyAdmin.quota;
const DEFAULT_GRANT = 3;

const UNKNOWN_USER = { id: '', username: copyAdmin.common.emDash, displayName: null };

/** 申请人（账号被清理的极端情形下后端给 null）。 */
function userOf(request: AdminQuotaRequest) {
  return request.user ?? UNKNOWN_USER;
}

function resourceLabel(resource: string): string {
  return (t.resource as Record<string, string>)[resource] ?? resource;
}

/* ── 拒绝对话框（理由必填，用户可见）────────────────────────────────────────── */

function RejectDialog({
  request,
  onClose,
  onResolved,
}: {
  request: AdminQuotaRequest | null;
  onClose: () => void;
  onResolved: () => void;
}) {
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const busyRef = useRef(false);
  const last = useRef<AdminQuotaRequest | null>(null);
  if (request) last.current = request;
  const shown = request ?? last.current;

  useEffect(() => {
    if (!request) {
      setNote('');
      setError(null);
    }
  }, [request]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!request || busyRef.current) return;
    if (note.trim() === '') {
      setError(t.rejectRequired);
      ref.current?.focus();
      return;
    }
    setError(null);
    busyRef.current = true;
    setBusy(true);
    try {
      await adminApi.adminResolveQuotaRequest(request.id, { decision: 'reject', note: note.trim() });
      notify.success(fill(t.rejected, { name: userOf(request).username }));
      onResolved();
      onClose();
    } catch (failure) {
      notifyFailure(failure);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  return (
    <Dialog open={request !== null} onOpenChange={(next) => (!next && !busy ? onClose() : undefined)}>
      <DialogContent
        title={shown ? fill(t.rejectTitle, { name: userOf(shown).username }) : t.reject}
        description={t.rejectBody}
        hideClose
        className="w-[min(92vw,440px)] p-5"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          ref.current?.focus();
        }}
      >
        <form onSubmit={(event) => void submit(event)} noValidate>
          <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">
            {shown ? fill(t.rejectTitle, { name: userOf(shown).username }) : t.reject}
          </h2>
          <p className="mt-1.5 text-[12.5px] text-[var(--text-secondary)]">{t.rejectBody}</p>
          <div className="mt-3">
            <label htmlFor="reject-note" className="mb-1.5 block text-[12px] font-medium text-[var(--text-secondary)]">
              {t.rejectNote}
            </label>
            <Textarea
              id="reject-note"
              ref={ref}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={200}
              placeholder={t.rejectNotePlaceholder}
              invalid={Boolean(error)}
              aria-describedby="reject-note-error"
              aria-required="true"
            />
            <div id="reject-note-error" aria-live="polite" className="min-h-0 text-[11.5px] text-[var(--danger)]">
              {error}
            </div>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {copyAdmin.common.cancel}
            </Button>
            <Button type="submit" variant="danger" loading={busy}>
              {t.rejectConfirm}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* ── 待处理行 ──────────────────────────────────────────────────────────────── */

function PendingRow({
  request,
  onReject,
  onResolved,
}: {
  request: AdminQuotaRequest;
  onReject: () => void;
  onResolved: () => void;
}) {
  const [grant, setGrant] = useState(String(DEFAULT_GRANT));
  const [error, setError] = useState<string | null>(null);
  const grantN = Number(grant);
  const valid = Number.isInteger(grantN) && grantN >= 1 && grantN <= 20;

  const approve = useMutation({
    mutationFn: (n: number) => adminApi.adminResolveQuotaRequest(request.id, { decision: 'approve', grant: n }),
    onSuccess: (_data, n) => {
      notify.success(fill(t.approved, { name: userOf(request).username, n }));
      onResolved();
    },
    onError: notifyFailure,
  });

  return (
    <li className="px-4 py-3.5" data-testid={`quota-request-${userOf(request).username}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <UserCell username={userOf(request).username} displayName={userOf(request).displayName} />
            <Badge tone="neutral">{resourceLabel(request.resource)}</Badge>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11.5px] text-[var(--text-tertiary)]">
            <span>{fill(t.requested, { n: request.amount })}</span>
            <span>{fill(t.runs7d, { n: request.userRuns7d })}</span>
            <time dateTime={request.createdAt} title={dateTime(request.createdAt)}>
              {fill(t.submittedAt, { when: relativeTime(request.createdAt) })}
            </time>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor={`grant-${request.id}`} className="sr-only">
            {t.grantLabel}
          </label>
          <Input
            id={`grant-${request.id}`}
            type="number"
            inputMode="numeric"
            inputSize="sm"
            value={grant}
            onChange={(event) => {
              setGrant(event.target.value);
              setError(null);
            }}
            invalid={Boolean(error)}
            className="w-16"
            aria-describedby={error ? `grant-${request.id}-error` : undefined}
          />
          <Button
            size="sm"
            variant="accent-ghost"
            loading={approve.isPending}
            aria-label={fill(t.approveLabel, { name: userOf(request).username, n: valid ? grantN : DEFAULT_GRANT })}
            onClick={() => {
              if (!valid) {
                setError(t.grantInvalid);
                return;
              }
              approve.mutate(grantN);
            }}
          >
            {fill(t.approve, { n: valid ? grantN : DEFAULT_GRANT })}
          </Button>
          <Button size="sm" variant="secondary" aria-label={fill(t.rejectLabel, { name: userOf(request).username })} onClick={onReject}>
            {t.reject}
          </Button>
        </div>
      </div>

      <blockquote className="mt-2 border-l-2 border-[var(--border-strong)] pl-3 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
        {request.reason}
      </blockquote>
      <div id={`grant-${request.id}-error`} aria-live="polite" className="min-h-0 text-right text-[11.5px] text-[var(--danger)]">
        {error}
      </div>
    </li>
  );
}

function ResolvedRow({ request }: { request: AdminQuotaRequest }) {
  const tone = request.status === 'approved' ? 'accent' : request.status === 'rejected' ? 'danger' : 'neutral';
  return (
    <li className="px-4 py-3" data-testid={`quota-resolved-${userOf(request).username}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <UserCell username={userOf(request).username} displayName={userOf(request).displayName} />
          <Badge tone="neutral">{resourceLabel(request.resource)}</Badge>
          <Badge tone={tone}>{t.status[request.status]}</Badge>
        </div>
        <span className="text-[11.5px] text-[var(--text-tertiary)]">
          {request.resolvedAt ? fill(t.resolvedAt, { when: relativeTime(request.resolvedAt) }) : null}
        </span>
      </div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{request.reason}</p>
      {request.status === 'approved' && request.granted ? (
        <p className="mt-1 text-[12px] text-[var(--text-tertiary)]">{fill(t.granted, { n: request.granted })}</p>
      ) : null}
      {request.resolutionNote ?? request.note ? (
        <p className="mt-1 text-[12px] text-[var(--text-tertiary)]">
          {t.note}：{request.resolutionNote ?? request.note}
        </p>
      ) : null}
    </li>
  );
}

function RequestList({ status }: { status: 'pending' | 'resolved' }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<AdminQuotaRequest | null>(null);
  const query = useQuery({
    queryKey: adminKeys.quotaRequests(status),
    queryFn: () => adminApi.adminQuotaRequests(status),
  });
  const items = query.data?.items ?? [];

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: adminKeys.quotaRequestsRoot });
    void queryClient.invalidateQueries({ queryKey: adminKeys.overview });
    void queryClient.invalidateQueries({ queryKey: adminKeys.usersRoot });
  };

  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)]">
      {query.isError ? (
        <div className="p-4">
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </div>
      ) : query.isPending ? (
        <TableSkeleton rows={3} />
      ) : items.length === 0 ? (
        <EmptyState
          title={status === 'pending' ? t.emptyPending : t.emptyResolved}
          hint={status === 'pending' ? t.emptyPendingHint : undefined}
        />
      ) : (
        <ul className="divide-y divide-[var(--border-subtle)]">
          {items.map((request) =>
            status === 'pending' ? (
              <PendingRow
                key={request.id}
                request={request}
                onReject={() => setRejecting(request)}
                onResolved={refresh}
              />
            ) : (
              <ResolvedRow key={request.id} request={request} />
            ),
          )}
        </ul>
      )}
      <RejectDialog request={rejecting} onClose={() => setRejecting(null)} onResolved={refresh} />
    </div>
  );
}

/** 额度申请队列：待处理（通过 +N / 拒绝并写理由）与已处理（只读）。 */
export function QuotaRequestsPanel() {
  const [tab, setTab] = useState<'pending' | 'resolved'>('pending');
  return (
    <div>
      <AdminPageHeader title={t.title} subtitle={t.subtitle} />
      <Tabs value={tab} onValueChange={(value) => setTab(value as 'pending' | 'resolved')}>
        <TabsList aria-label={t.title} className="mb-4">
          <TabsTrigger value="pending">{t.tabs.pending}</TabsTrigger>
          <TabsTrigger value="resolved">{t.tabs.resolved}</TabsTrigger>
        </TabsList>
        <TabsContent value="pending">
          <RequestList status="pending" />
        </TabsContent>
        <TabsContent value="resolved">
          <RequestList status="resolved" />
        </TabsContent>
      </Tabs>
    </div>
  );
}
