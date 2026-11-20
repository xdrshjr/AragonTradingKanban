'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { useMe } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { dateTime, relativeTime } from '@/lib/format';
import type { AdminUser, AdminUserDetail, QuotaBucket, QuotaResource, SessionOut } from '@/lib/types';

import { RoleBadge } from '../auth/RoleBadge';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { NativeSelect } from '../ui/Select';
import { Sheet, SheetContent } from '../ui/Sheet';
import { Skeleton } from '../ui/Skeleton';
import { StatusDot } from '../ui/StatusDot';
import { notify } from '../ui/Toast';
import { Avatar, ErrorState, userStatusKind, userStatusLabel } from './AdminPrimitives';
import { adminKeys } from './keys';
import { notifyFailure } from './UserActionDialogs';
import { userActionsFor, type UserAction } from './userActions';

const t = copyAdmin.drawer;

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="border-t border-[var(--border-subtle)] pt-4">
      <h3 id={id} className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-[12.5px]">
      <dt className="shrink-0 text-[var(--text-tertiary)]">{label}</dt>
      <dd className="min-w-0 truncate text-right text-[var(--text-primary)]">{children}</dd>
    </div>
  );
}

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-[12px] font-medium text-[var(--text-secondary)]">
      {children}
    </label>
  );
}

const RESOURCE_OPTIONS: { value: QuotaResource; label: string }[] = [
  { value: 'report', label: copyAdmin.quota.resource.report },
  { value: 'deep_ask', label: copyAdmin.quota.resource.deep_ask },
];

function bucketOf(detail: AdminUserDetail | undefined, resource: QuotaResource): QuotaBucket | null {
  const status = detail?.quotaStatus;
  if (!status) return null;
  return resource === 'report' ? status.report : status.deepAsk;
}

/* ── 额度 ──────────────────────────────────────────────────────────────────── */

type LimitMode = 'follow' | 'unlimited' | 'custom';

function QuotaSection({ user, detail, canAdjust }: { user: AdminUser; detail?: AdminUserDetail; canAdjust: boolean }) {
  const queryClient = useQueryClient();
  const [resource, setResource] = useState<QuotaResource>('report');
  const bucket = bucketOf(detail, resource);
  const lite = user.quota?.report;

  const [grant, setGrant] = useState('');
  const [grantNote, setGrantNote] = useState('');
  const [grantError, setGrantError] = useState<string | null>(null);

  const [mode, setMode] = useState<LimitMode>('custom');
  const [custom, setCustom] = useState('');
  const [limitError, setLimitError] = useState<string | null>(null);

  // 取到真实额度后，把“日额度覆盖”表单对齐到当前生效值（不限 / 具体次数）。
  const effectiveLimit = bucket ? bucket.limit : lite ? lite.limit : undefined;
  useEffect(() => {
    if (effectiveLimit === undefined) return;
    if (effectiveLimit === null) {
      setMode('unlimited');
    } else {
      setMode('custom');
      setCustom(String(effectiveLimit));
    }
  }, [effectiveLimit, resource, user.id]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: adminKeys.user(user.id) });
    void queryClient.invalidateQueries({ queryKey: adminKeys.usersRoot });
  };

  const grantMutation = useMutation({
    mutationFn: (amount: number) =>
      adminApi.adminAdjustQuota(user.id, { resource, grant: amount, ...(grantNote.trim() ? { note: grantNote.trim() } : {}) }),
    onSuccess: () => {
      notify.success(t.grantDone);
      setGrant('');
      setGrantNote('');
      refresh();
    },
    onError: notifyFailure,
  });

  const limitMutation = useMutation({
    mutationFn: (dailyLimit: number | null) => adminApi.adminAdjustQuota(user.id, { resource, dailyLimit }),
    onSuccess: () => {
      notify.success(t.limitDone);
      refresh();
    },
    onError: notifyFailure,
  });

  const submitGrant = (event: React.FormEvent) => {
    event.preventDefault();
    const n = Number(grant);
    if (!Number.isInteger(n) || n === 0 || n < -50 || n > 50) {
      setGrantError(t.grantInvalid);
      return;
    }
    setGrantError(null);
    grantMutation.mutate(n);
  };

  const submitLimit = (event: React.FormEvent) => {
    event.preventDefault();
    if (mode === 'follow') return limitMutation.mutate(null);
    if (mode === 'unlimited') return limitMutation.mutate(-1);
    const n = Number(custom);
    if (custom.trim() === '' || !Number.isInteger(n) || n < 0 || n > 1000) {
      setLimitError(t.limitInvalid);
      return;
    }
    setLimitError(null);
    limitMutation.mutate(n);
  };

  const unlimited = bucket ? bucket.limit === null : false;
  const used = bucket?.usedToday ?? lite?.usedToday ?? 0;
  const bonus = bucket?.bonus ?? lite?.bonus ?? 0;
  const limit = bucket ? bucket.limit : lite ? lite.limit : null;

  return (
    <Section id="drawer-quota" title={t.quota}>
      <div className="mb-3 flex items-center gap-2">
        <label htmlFor="drawer-resource" className="sr-only">
          {copyAdmin.quota.title}
        </label>
        <NativeSelect
          id="drawer-resource"
          selectSize="sm"
          value={resource}
          onChange={(event) => setResource(event.target.value as QuotaResource)}
        >
          {RESOURCE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      </div>

      <dl className="mb-4 grid grid-cols-3 gap-2" data-testid="drawer-quota-summary">
        {[
          [t.quotaToday, `${used}${unlimited || limit === null ? '' : ` / ${limit}`}`],
          [t.quotaLimit, limit === null ? copyAdmin.common.unlimited : String(limit)],
          [t.quotaBonus, String(bonus)],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-3 py-2"
          >
            <dt className="text-[11px] text-[var(--text-tertiary)]">{label}</dt>
            <dd className="num mt-0.5 text-[15px] font-semibold text-[var(--text-primary)]">{value}</dd>
          </div>
        ))}
      </dl>

      {canAdjust ? (
        <div className="space-y-5">
          <form onSubmit={submitGrant} noValidate aria-label={t.grantTitle} className="space-y-2">
            <FieldLabel htmlFor="drawer-grant">{t.grantLabel}</FieldLabel>
            <div className="flex items-start gap-2">
              <Input
                id="drawer-grant"
                type="number"
                inputMode="numeric"
                value={grant}
                onChange={(event) => setGrant(event.target.value)}
                invalid={Boolean(grantError)}
                aria-describedby="drawer-grant-error"
                className="w-28"
                placeholder="+5"
              />
              <Input
                aria-label={t.grantNote}
                value={grantNote}
                onChange={(event) => setGrantNote(event.target.value)}
                placeholder={t.grantNote}
                maxLength={80}
                className="min-w-0 flex-1"
              />
              <Button type="submit" variant="secondary" loading={grantMutation.isPending}>
                {t.grantSubmit}
              </Button>
            </div>
            <div id="drawer-grant-error" aria-live="polite" className="min-h-0 text-[11.5px] text-[var(--danger)]">
              {grantError}
            </div>
          </form>

          <form onSubmit={submitLimit} noValidate aria-label={t.limitTitle} className="space-y-2">
            <fieldset>
              <legend className="mb-1.5 text-[12px] font-medium text-[var(--text-secondary)]">{t.limitTitle}</legend>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-[var(--text-secondary)]">
                {(
                  [
                    ['follow', t.limitFollow],
                    ['unlimited', t.limitUnlimited],
                    ['custom', t.limitCustom],
                  ] as const
                ).map(([value, label]) => (
                  <label key={value} className="inline-flex cursor-pointer items-center gap-1.5">
                    <input
                      type="radio"
                      name="drawer-limit-mode"
                      value={value}
                      checked={mode === value}
                      onChange={() => setMode(value)}
                      className="accent-[var(--accent)]"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            {mode === 'custom' ? (
              <div>
                <FieldLabel htmlFor="drawer-limit-custom">{t.limitCustomLabel}</FieldLabel>
                <Input
                  id="drawer-limit-custom"
                  type="number"
                  inputMode="numeric"
                  value={custom}
                  onChange={(event) => setCustom(event.target.value)}
                  invalid={Boolean(limitError)}
                  aria-describedby="drawer-limit-error"
                  className="w-28"
                />
              </div>
            ) : null}
            <div id="drawer-limit-error" aria-live="polite" className="min-h-0 text-[11.5px] text-[var(--danger)]">
              {limitError}
            </div>
            <Button type="submit" variant="secondary" loading={limitMutation.isPending}>
              {t.limitSubmit}
            </Button>
          </form>
        </div>
      ) : null}
    </Section>
  );
}

/* ── 会话 ──────────────────────────────────────────────────────────────────── */

function SessionsSection({
  user,
  sessions,
  canRevoke,
  onRevokeAll,
}: {
  user: AdminUser;
  sessions: SessionOut[];
  canRevoke: boolean;
  onRevokeAll: () => void;
}) {
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: (sessionId: string) => adminApi.adminRevokeSession(user.id, sessionId),
    onSuccess: () => {
      notify.success(t.sessionRevoked);
      void queryClient.invalidateQueries({ queryKey: adminKeys.user(user.id) });
    },
    onError: notifyFailure,
  });

  return (
    <Section id="drawer-sessions" title={t.sessions}>
      {sessions.length === 0 ? (
        <p className="text-[12px] text-[var(--text-tertiary)]">{t.sessionsEmpty}</p>
      ) : (
        <ul className="divide-y divide-[var(--border-subtle)] rounded-[var(--radius-md)] border border-[var(--border-subtle)]">
          {sessions.map((session) => {
            const ua = session.userAgent ?? copyAdmin.common.emDash;
            return (
              <li key={session.id} className="flex items-center gap-3 px-3 py-2 text-[12px]">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[var(--text-primary)]">{ua}</div>
                  <div className="mt-0.5 truncate text-[11px] text-[var(--text-tertiary)]">
                    {session.ip ? <span className="mono">{fill(t.sessionIp, { ip: session.ip })}</span> : null}
                    {session.ip ? ' · ' : ''}
                    {fill(t.sessionLastSeen, { when: relativeTime(session.lastSeenAt ?? session.createdAt) })}
                  </div>
                </div>
                {canRevoke ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label={fill(t.sessionRevokeLabel, { ua })}
                    loading={revoke.isPending && revoke.variables === session.id}
                    onClick={() => revoke.mutate(session.id)}
                  >
                    {t.sessionRevoke}
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {canRevoke && sessions.length > 1 ? (
        <div className="mt-2">
          <Button size="sm" variant="ghost" onClick={onRevokeAll}>
            {t.sessionsRevokeAll}
          </Button>
        </div>
      ) : null}
    </Section>
  );
}

/* ── 危险区 ────────────────────────────────────────────────────────────────── */

function DangerZone({
  user,
  onAction,
}: {
  user: AdminUser;
  onAction: (action: UserAction, user: AdminUser) => void;
}) {
  const me = useMe();
  const set = userActionsFor(me, user);
  const has = (action: UserAction) => set.actions.has(action);
  const items: { action: UserAction; label: string; tone: 'danger' | 'secondary' }[] = [
    { action: 'unlock', label: copyAdmin.users.actions.unlock, tone: 'secondary' },
    { action: 'disable', label: copyAdmin.users.actions.disable, tone: 'danger' },
    { action: 'enable', label: copyAdmin.users.actions.enable, tone: 'secondary' },
    { action: 'resetPassword', label: copyAdmin.users.actions.resetPassword, tone: 'danger' },
    { action: 'promote', label: copyAdmin.users.actions.promote, tone: 'secondary' },
    { action: 'demote', label: copyAdmin.users.actions.demote, tone: 'danger' },
    { action: 'delete', label: copyAdmin.users.actions.delete, tone: 'danger' },
  ];
  const visible = items.filter((item) => has(item.action));

  const note =
    set.reason === 'self' ? t.dangerSelf : set.reason === 'deleted' ? t.dangerDeleted : set.reason === 'protected' ? t.dangerNone : null;

  return (
    <Section id="drawer-danger" title={t.danger}>
      {visible.length === 0 ? (
        <p className="text-[12px] text-[var(--text-tertiary)]">{note ?? t.dangerNone}</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {visible.map((item) => (
              <Button key={item.action} size="sm" variant={item.tone} onClick={() => onAction(item.action, user)}>
                {item.label}
              </Button>
            ))}
          </div>
          {set.reason === 'self' ? <p className="mt-2 text-[11.5px] text-[var(--text-tertiary)]">{t.dangerSelf}</p> : null}
        </>
      )}
    </Section>
  );
}

/* ── 抽屉本体 ──────────────────────────────────────────────────────────────── */

export interface UserDrawerProps {
  userId: string | null;
  /** 列表里已有的摘要：详情还没回来时先用它渲染头部，抽屉不会先空一下。 */
  summary: AdminUser | null;
  onClose: () => void;
  onAction: (action: UserAction, user: AdminUser) => void;
}

export function UserDrawer({ userId, summary, onClose, onAction }: UserDrawerProps) {
  const me = useMe();
  const query = useQuery({
    queryKey: adminKeys.user(userId ?? ''),
    queryFn: () => adminApi.adminUser(userId as string),
    enabled: userId !== null,
  });
  const detail = query.data;
  const user: AdminUser | null = detail ?? summary;
  const actionSet = user ? userActionsFor(me, user) : null;
  const canQuota = Boolean(actionSet?.actions.has('adjustQuota'));
  const canRevoke = Boolean(actionSet?.actions.has('revokeSessions'));
  const name = user?.displayName?.trim() || user?.username || '';

  return (
    <Sheet open={userId !== null} onOpenChange={(next) => (next ? undefined : onClose())}>
      <SheetContent title={t.title} description={user ? user.username : t.title} closeLabel={t.close}>
        {user ? (
          <>
            <header className="flex items-center gap-3 px-5 pb-4 pt-5 pr-12">
              <Avatar name={name} className="size-10 text-[15px]" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="mono truncate text-[15px] font-semibold text-[var(--text-primary)]">{user.username}</span>
                  <RoleBadge role={user.role} />
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[11.5px] text-[var(--text-tertiary)]">
                  <StatusDot status={userStatusKind(user)} label={userStatusLabel(user)} />
                  {user.displayName ? <span className="truncate">{user.displayName}</span> : null}
                </div>
              </div>
            </header>

            <div className={cn('min-h-0 flex-1 space-y-5 overflow-y-auto px-5 pb-8')}>
              {query.isError ? (
                <ErrorState error={query.error} title={t.loadFailed} onRetry={() => void query.refetch()} />
              ) : null}

              <Section id="drawer-profile" title={t.profile}>
                <dl className="divide-y divide-[var(--border-subtle)]">
                  <Row label={t.username}>
                    <span className="mono">{user.username}</span>
                  </Row>
                  <Row label={t.displayName}>{user.displayName ?? copyAdmin.common.emDash}</Row>
                  <Row label={t.createdAt}>{dateTime(user.createdAt)}</Row>
                  <Row label={t.lastLogin}>{user.lastLoginAt ? dateTime(user.lastLoginAt) : copyAdmin.common.never}</Row>
                  {user.invitedBy ? <Row label={t.invitedBy}>{user.invitedBy}</Row> : null}
                  {detail?.failedLogins ? <Row label={t.failedLogins}>{detail.failedLogins}</Row> : null}
                  {detail?.lockedUntil ? <Row label={t.lockedUntil}>{dateTime(detail.lockedUntil)}</Row> : null}
                  {detail?.disabledReason ? <Row label={t.disabledReason}>{detail.disabledReason}</Row> : null}
                </dl>
              </Section>

              {!detail && query.isPending ? (
                <div role="status" aria-busy="true" aria-label={copyAdmin.common.loading} className="space-y-2">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-24 w-full" />
                </div>
              ) : null}

              <QuotaSection user={user} detail={detail} canAdjust={canQuota} />

              {detail ? (
                <SessionsSection
                  user={user}
                  sessions={detail.sessions ?? []}
                  canRevoke={canRevoke}
                  onRevokeAll={() => onAction('revokeSessions', user)}
                />
              ) : null}

              {detail ? (
                <Section id="drawer-runs" title={t.runs}>
                  {(detail.recentRuns ?? []).length === 0 ? (
                    <p className="text-[12px] text-[var(--text-tertiary)]">{t.runsEmpty}</p>
                  ) : (
                    <ul className="divide-y divide-[var(--border-subtle)]">
                      {(detail.recentRuns ?? []).slice(0, 20).map((run) => (
                        <li key={run.id} className="flex items-center gap-3 py-1.5 text-[12px]">
                          <span className="mono min-w-0 flex-1 truncate text-[var(--text-primary)]">
                            {run.symbol ?? copyAdmin.common.emDash}
                          </span>
                          <span className="text-[var(--text-secondary)]">
                            {(t.runStatus as Record<string, string>)[run.status] ?? run.status}
                          </span>
                          <time dateTime={run.startedAt} className="w-20 shrink-0 text-right text-[11px] text-[var(--text-tertiary)]">
                            {relativeTime(run.startedAt)}
                          </time>
                        </li>
                      ))}
                    </ul>
                  )}
                </Section>
              ) : null}

              <DangerZone user={user} onAction={onAction} />
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
