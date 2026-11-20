'use client';

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, KeyRound, Monitor } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { api } from '@/lib/api';
import { ME_QUERY_KEY, useAuthActions, useAuthUi, useCan, useMe, useMeActions } from '@/lib/auth';
import { describeAuthError } from '@/lib/authErrorText';
import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';
import { dateTime, relativeTime } from '@/lib/format';
import { bucketView, formatReset } from '@/lib/quota';
import { clearRecoveryUnsaved, readRecoveryUnsaved } from '@/lib/recoveryKey';
import type { AuthUser, QuotaBucket, QuotaLedgerItem, SessionOut } from '@/lib/types';

import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Input } from '../ui/Input';
import { Skeleton } from '../ui/Skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/Tabs';
import { notify } from '../ui/Toast';
import { ChangePasswordForm } from './ChangePasswordForm';
import { DeleteAccountDialog } from './DeleteAccountDialog';
import { QuotaChip } from './QuotaChip';
import { QuotaRing } from './QuotaRing';
import { RoleBadge } from './RoleBadge';
import { RotateRecoveryKeyDialog } from './RotateRecoveryKeyDialog';
import { UserMenu } from './UserMenu';

const t = copyAuthPages.account;

function Card({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-[12.5px] font-semibold tracking-tight text-[var(--text-primary)]">{title}</h2>
        {aside}
      </div>
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

function ErrorLine({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const view = describeAuthError(error);
  return (
    <p role="alert" className="text-[12px] text-[var(--danger)]">
      {t.loadFailed}：{view.message}
      {view.requestId ? <span className="mono ml-1 text-[10.5px] opacity-80">{fill(copyAuthPages.errors.withRequestId, { id: view.requestId })}</span> : null}
      {onRetry ? (
        <button type="button" onClick={onRetry} className="ml-2 underline underline-offset-2">
          {t.retry}
        </button>
      ) : null}
    </p>
  );
}

/* ── 概览 ────────────────────────────────────────────────────────────────── */

function ProfileCard({ user }: { user: AuthUser }) {
  const { setMe } = useMeActions();
  const [name, setName] = useState(user.displayName ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = name.trim() !== (user.displayName ?? '');

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving || !dirty) return;
    if (name.trim().length > 40) {
      setError(t.displayNameTooLong);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const me = await api.updateProfile({ displayName: name.trim() || null });
      setMe(me);
      notify.success(t.displayNameSaved);
    } catch (failure) {
      setError(describeAuthError(failure).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={t.profile} aside={<RoleBadge role={user.role} />}>
      <dl className="divide-y divide-[var(--border-subtle)]">
        <Row label={t.username}>
          <span className="mono">{user.username}</span>
        </Row>
        <Row label={t.joined}>{dateTime(user.createdAt)}</Row>
        <Row label={t.lastLogin}>{user.lastLoginAt ? dateTime(user.lastLoginAt) : '—'}</Row>
      </dl>
      <form onSubmit={save} noValidate className="mt-3 space-y-1.5">
        <label htmlFor="account-display-name" className="block text-[12px] font-medium text-[var(--text-secondary)]">
          {t.displayName}
        </label>
        <div className="flex items-center gap-2">
          <Input
            id="account-display-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t.displayNamePlaceholder}
            maxLength={48}
            autoComplete="nickname"
            className="min-w-0 flex-1"
            aria-describedby="account-display-name-error"
          />
          <Button type="submit" variant="secondary" loading={saving} disabled={!dirty}>
            {t.displayNameSave}
          </Button>
        </div>
        <div id="account-display-name-error" aria-live="polite" className="min-h-0 text-[11.5px] text-[var(--danger)]">
          {error}
        </div>
      </form>
    </Card>
  );
}

function BucketBlock({ title, bucket, tz, canRequest }: { title: string; bucket: QuotaBucket; tz: string; canRequest: boolean }) {
  const openQuota = useAuthUi((state) => state.openQuota);
  const view = bucketView(bucket);
  if (!view) return null;
  return (
    <div className="flex items-center gap-4">
      <QuotaRing bucket={bucket} size={72} />
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] font-medium text-[var(--text-primary)]">{title}</div>
        {view.unlimited ? (
          <p className="mt-0.5 text-[12px] text-[var(--text-secondary)]">{t.quotaUnlimited}</p>
        ) : (
          <dl className="mt-0.5 space-y-0.5 text-[12px] text-[var(--text-secondary)]">
            <div className="flex gap-2">
              <dt className="text-[var(--text-tertiary)]">{t.quotaDaily}</dt>
              <dd className="num">
                {view.usedToday}/{view.limit}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-[var(--text-tertiary)]">{t.quotaBonus}</dt>
              <dd className="num">{view.bonus}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-[var(--text-tertiary)]">{t.quotaReset}</dt>
              <dd>{formatReset(bucket.resetAt, tz).trim()}</dd>
            </div>
          </dl>
        )}
      </div>
      {canRequest && !view.unlimited ? (
        <Button
          variant="secondary"
          size="sm"
          disabled={Boolean(bucket.pendingRequest)}
          onClick={() => openQuota(null)}
        >
          {bucket.pendingRequest ? t.quotaRequestPending : t.quotaRequestOpen}
        </Button>
      ) : null}
    </div>
  );
}

function RequestsList() {
  const query = useQuery({ queryKey: ['me', 'quota-requests'], queryFn: () => api.myQuotaRequests(), staleTime: 15_000 });
  if (query.isPending) return <Skeleton className="h-10 w-full" />;
  if (query.isError) return <ErrorLine error={query.error} onRetry={() => void query.refetch()} />;
  const items = query.data.items;
  if (items.length === 0) return <p className="text-[12px] text-[var(--text-tertiary)]">{t.requestsEmpty}</p>;
  return (
    <ul className="divide-y divide-[var(--border-subtle)]">
      {items.slice(0, 6).map((item) => (
        <li key={item.id} className="py-2 text-[12px]">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[var(--text-primary)]">
              {t.resource[item.resource] ?? item.resource} +{item.amount}
            </span>
            <span
              className={cn(
                'text-[11px]',
                item.status === 'approved' && 'text-[var(--accent)]',
                item.status === 'rejected' && 'text-[var(--danger)]',
                item.status === 'pending' && 'text-[var(--info)]',
              )}
            >
              {t.requestStatus[item.status] ?? item.status}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[11.5px] text-[var(--text-tertiary)]" title={item.reason}>
            {item.reason}
          </p>
          {item.note ? <p className="mt-0.5 text-[11.5px] text-[var(--text-secondary)]">{item.note}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function Overview({ user }: { user: AuthUser }) {
  const me = useMe();
  const mayRequest = useCan('quota.request');
  const quota = me.quota;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <ProfileCard user={user} />
      <div className="space-y-3">
        <Card title={t.quotaTitle}>
          {quota ? (
            <div className="space-y-4">
              <BucketBlock title={t.quotaReport} bucket={quota.report} tz={quota.tz} canRequest={mayRequest} />
              <div className="h-px bg-[var(--border-subtle)]" />
              <BucketBlock title={t.quotaDeep} bucket={quota.deepAsk} tz={quota.tz} canRequest={false} />
            </div>
          ) : (
            <p className="text-[12px] text-[var(--text-tertiary)]">{t.quotaUnlimited}</p>
          )}
        </Card>
        <Card title={t.requestsTitle}>
          <RequestsList />
        </Card>
      </div>
    </div>
  );
}

/* ── 安全 ────────────────────────────────────────────────────────────────── */

function SessionsCard() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ['me', 'sessions'], queryFn: () => api.sessions(), staleTime: 10_000 });
  const [confirmOthers, setConfirmOthers] = useState(false);

  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeSession(id),
    onSuccess: () => {
      notify.success(t.sessionRevoked);
      void queryClient.invalidateQueries({ queryKey: ['me', 'sessions'] });
    },
    onError: (error) => notify.error(describeAuthError(error).message),
  });

  const revokeOthers = async () => {
    try {
      const result = await api.revokeOtherSessions();
      notify.success(result.revoked > 0 ? fill(t.revokeOthersDone, { count: result.revoked }) : t.revokeOthersNone);
      await queryClient.invalidateQueries({ queryKey: ['me', 'sessions'] });
    } catch (error) {
      notify.error(describeAuthError(error).message);
    } finally {
      setConfirmOthers(false);
    }
  };

  const items: SessionOut[] = query.data?.items ?? [];
  const hasOthers = items.some((item) => !item.current);

  return (
    <Card
      title={t.sessionsTitle}
      aside={
        <Button variant="secondary" size="sm" disabled={!hasOthers} onClick={() => setConfirmOthers(true)}>
          {t.revokeOthers}
        </Button>
      }
    >
      <p className="mb-2 text-[11.5px] text-[var(--text-tertiary)]">{t.sessionsHint}</p>
      {query.isPending ? (
        <Skeleton className="h-12 w-full" />
      ) : query.isError ? (
        <ErrorLine error={query.error} onRetry={() => void query.refetch()} />
      ) : items.length === 0 ? (
        <p className="text-[12px] text-[var(--text-tertiary)]">{t.sessionsEmpty}</p>
      ) : (
        <ul className="divide-y divide-[var(--border-subtle)]">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-3 py-2.5">
              <Monitor className="size-4 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-[12.5px] text-[var(--text-primary)]">
                  <span className="truncate">{item.userAgent || '—'}</span>
                  {item.current ? (
                    <span className="shrink-0 rounded-full bg-[var(--accent-weak)] px-1.5 py-px text-[10.5px] text-[var(--accent)]">
                      {t.sessionCurrent}
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--text-tertiary)]">
                  {t.sessionIp} <span className="mono">{item.ip ?? '—'}</span> · {t.sessionLastSeen}{' '}
                  {item.lastSeenAt ? relativeTime(item.lastSeenAt) : '—'}
                </div>
              </div>
              {!item.current ? (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={revoke.isPending && revoke.variables === item.id}
                  onClick={() => revoke.mutate(item.id)}
                >
                  {t.sessionRevoke}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={confirmOthers}
        onOpenChange={setConfirmOthers}
        title={t.revokeOthers}
        description={t.sessionsHint}
        confirmLabel={t.revokeOthers}
        tone="danger"
        onConfirm={revokeOthers}
      />
    </Card>
  );
}

/**
 * ★Y3 恢复码卡：状态 + “生成新的恢复码”（密码确认后展示一次）。
 * 站点关闭自助找回（`/auth/config.recoveryEnabled=false`）或 root（只走离线 CLI）时整卡隐藏。
 */
function RecoveryCard({ user, onRotated }: { user: AuthUser; onRotated: (setAt: string) => void }) {
  const rc = copyAuthPages.recoveryCard;
  const [open, setOpen] = useState(false);
  const [setAt, setSetAt] = useState<string | null>(null);
  const has = Boolean(user.hasRecoveryKey) || setAt !== null;
  const config = useQuery({ queryKey: ['auth-config'], queryFn: () => api.authConfig(), staleTime: 5 * 60_000, retry: 0 });
  if (user.role === 'root' || config.data?.recoveryEnabled === false) return null;

  return (
    <Card
      title={rc.title}
      aside={
        <span
          data-testid="recovery-status"
          className={cn(
            'rounded-full px-2 py-px text-[10.5px]',
            has ? 'bg-[var(--accent-weak)] text-[var(--accent)]' : 'bg-[var(--warn-weak)] text-[var(--warn)]',
          )}
        >
          {has ? (setAt ? fill(rc.statusSetAt, { at: dateTime(setAt) }) : rc.statusSet) : rc.statusUnset}
        </span>
      }
    >
      <p className="text-[12px] leading-relaxed text-[var(--text-secondary)]">{has ? rc.body : rc.unsetBody}</p>
      <div className="mt-3">
        <Button variant="secondary" size="sm" leadingIcon={<KeyRound className="size-3.5" aria-hidden="true" />} onClick={() => setOpen(true)}>
          {has ? rc.rotate : rc.generate}
        </Button>
      </div>
      <RotateRecoveryKeyDialog
        open={open}
        onOpenChange={setOpen}
        username={user.username}
        onRotated={(at) => {
          setSetAt(at || new Date().toISOString());
          onRotated(at);
        }}
      />
    </Card>
  );
}

/**
 * ★Y4 危险区：自助注销。红色描边、非主色；root（走离线 CLI）与非 user 主体不渲染。
 * 同时要求密码 + 键入用户名；成功后回落 guest 并回首页。
 */
function DangerZone({ user }: { user: AuthUser }) {
  const t = copyAuthPages.danger;
  const router = useRouter();
  const { signOut } = useAuthActions(() => router.refresh());
  const [open, setOpen] = useState(false);
  if (user.role === 'root') return null;

  return (
    <section
      data-testid="danger-zone"
      className="rounded-[var(--radius-lg)] border border-[var(--danger)]/40 bg-[var(--bg-surface-1)] p-4 md:col-span-2"
    >
      <h2 className="text-[12.5px] font-semibold tracking-tight text-[var(--danger)]">{t.title}</h2>
      <p className="mt-1.5 max-w-[62ch] text-[12px] leading-relaxed text-[var(--text-secondary)]">{t.body}</p>
      <div className="mt-3">
        <Button variant="danger" size="sm" onClick={() => setOpen(true)}>
          {t.open}
        </Button>
      </div>
      <DeleteAccountDialog
        open={open}
        onOpenChange={setOpen}
        username={user.username}
        onDeleted={() => {
          setOpen(false);
          void signOut().then(() => {
            notify.success(t.done);
            router.replace('/');
          });
        }}
      />
    </section>
  );
}

function Security({ user, onRotated }: { user: AuthUser; onRotated: (setAt: string) => void }) {
  const { refreshMe } = useMeActions();
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Card title={copyAuthPages.changePassword.title}>
        <ChangePasswordForm
          username={user.username}
          autoFocus={false}
          onDone={() => {
            notify.success(copyAuthPages.changePassword.done);
            void refreshMe();
          }}
        />
      </Card>
      <SessionsCard />
      <RecoveryCard
        user={user}
        onRotated={(at) => {
          onRotated(at);
          void refreshMe();
        }}
      />
      <DangerZone user={user} />
    </div>
  );
}

/* ── 额度流水 ────────────────────────────────────────────────────────────── */

function reasonLabel(item: QuotaLedgerItem): string {
  return t.reasons[item.reason] ?? item.reason;
}

function Ledger() {
  const query = useInfiniteQuery({
    queryKey: ['me', 'quota-ledger'],
    queryFn: ({ pageParam }) => api.quotaLedger({ limit: 50, before: pageParam ?? null }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    staleTime: 15_000,
  });

  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError) return <ErrorLine error={query.error} onRetry={() => void query.refetch()} />;
  const rows = query.data.pages.flatMap((page) => page.items);

  return (
    <Card title={t.ledgerTitle}>
      {rows.length === 0 ? (
        <p className="text-[12px] text-[var(--text-tertiary)]">{t.ledgerEmpty}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[460px] text-left text-[12px]">
            <thead>
              <tr className="border-b border-[var(--border-subtle)] text-[11px] text-[var(--text-tertiary)]">
                <th scope="col" className="py-1.5 pr-3 font-medium">{t.ledgerAt}</th>
                <th scope="col" className="py-1.5 pr-3 font-medium">{t.ledgerEvent}</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">{t.ledgerDelta}</th>
                <th scope="col" className="py-1.5 font-medium">{t.ledgerSource}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-[var(--border-subtle)] last:border-0">
                  <td className="py-1.5 pr-3 text-[var(--text-tertiary)]">{dateTime(row.at)}</td>
                  <td className="py-1.5 pr-3 text-[var(--text-primary)]">
                    {t.resource[row.resource] ?? row.resource} · {reasonLabel(row)}
                  </td>
                  <td
                    className={cn(
                      'num py-1.5 pr-3 text-right font-medium',
                      row.delta > 0 ? 'text-[var(--accent)]' : 'text-[var(--text-secondary)]',
                    )}
                  >
                    {row.delta > 0 ? `+${row.delta}` : row.delta}
                  </td>
                  <td className="py-1.5 text-[var(--text-tertiary)]">
                    {row.source === 'bonus' ? t.sourceBonus : row.source === 'daily' ? t.sourceDaily : row.source}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {query.hasNextPage ? (
        <div className="mt-3 text-center">
          <Button size="sm" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {copy.library.loadMore}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

/* ── 页面 ────────────────────────────────────────────────────────────────── */

/** `/account`：概览 / 安全 / 额度流水。数据全部来自服务端（`['me']` 与三个 `/auth|/me` 接口）。 */
export function AccountClient() {
  const me = useMe();
  const router = useRouter();
  const { signOut } = useAuthActions(() => router.refresh());
  const user = me.user;
  const [tab, setTab] = useState('overview');
  const queryClient = useQueryClient();
  // ★Y3：注册时选了“跳过（不推荐）”的提醒（本浏览器内；可关闭，生成新码后消失）。
  const [unsaved, setUnsaved] = useState(false);
  useEffect(() => {
    setUnsaved(Boolean(user && readRecoveryUnsaved(user.id)));
  }, [user]);

  // 额度页签每次打开都重取 `['me']`：别的设备上的消耗在这里应当立刻可见。
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
  }, [queryClient]);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[880px] flex-col px-4 py-5">
      <header className="mb-5 flex items-center justify-between gap-3">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-[12px] text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
        >
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          {copy.library.backHome}
        </Link>
        <div className="flex items-center gap-2">
          <QuotaChip showUnlimited />
          <UserMenu />
        </div>
      </header>

      <div className="mb-4">
        <h1 className="text-[18px] font-semibold tracking-tight text-[var(--text-primary)]">{t.title}</h1>
        <p className="mt-0.5 text-[12px] text-[var(--text-tertiary)]">{t.subtitle}</p>
      </div>

      {user?.passwordIsDefault ? (
        <div
          role="alert"
          className="mb-4 flex items-center justify-between gap-3 rounded-[var(--radius-md)] border border-[var(--danger)]/35 bg-[var(--danger-weak)] px-3 py-2 text-[12.5px] text-[var(--danger)]"
        >
          <span>{t.forcedBanner}</span>
          <button
            type="button"
            onClick={() => setTab('security')}
            className="shrink-0 font-medium underline underline-offset-2"
          >
            {t.mustChangeCta}
          </button>
        </div>
      ) : null}

      {user && unsaved ? (
        <div
          role="status"
          data-testid="recovery-unsaved-banner"
          className="mb-4 flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 rounded-[var(--radius-md)] border border-[var(--warn)]/35 bg-[var(--warn-weak)] px-3 py-2 text-[12.5px] text-[var(--warn)]"
        >
          <span>{copyAuthPages.recoveryCard.unsavedBanner}</span>
          <span className="flex shrink-0 items-center gap-3">
            <button type="button" onClick={() => setTab('security')} className="font-medium underline underline-offset-2">
              {copyAuthPages.recoveryCard.unsavedCta}
            </button>
            <button
              type="button"
              onClick={() => {
                clearRecoveryUnsaved();
                setUnsaved(false);
              }}
              className="underline underline-offset-2 opacity-80"
            >
              {copyAuthPages.recoveryCard.dismiss}
            </button>
          </span>
        </div>
      ) : null}

      {user ? (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList aria-label={t.title} className="mb-4">
            <TabsTrigger value="overview">{t.tabs.overview}</TabsTrigger>
            <TabsTrigger value="security">{t.tabs.security}</TabsTrigger>
            <TabsTrigger value="ledger">{t.tabs.ledger}</TabsTrigger>
          </TabsList>
          <TabsContent value="overview">
            <Overview user={user} />
          </TabsContent>
          <TabsContent value="security">
            <Security
              user={user}
              onRotated={() => {
                clearRecoveryUnsaved();
                setUnsaved(false);
              }}
            />
          </TabsContent>
          <TabsContent value="ledger">
            <Ledger />
          </TabsContent>
        </Tabs>
      ) : (
        <p className="text-[12.5px] text-[var(--text-secondary)]">{copy.auth.openModeBanner}</p>
      )}

      {user ? (
        <div className="mt-8 border-t border-[var(--border-subtle)] pt-4">
          <Button
            variant="ghost"
            onClick={() => void signOut().then(() => {
              notify.info(copy.auth.signedOut);
              router.replace('/');
            })}
          >
            {t.signOut}
          </Button>
        </div>
      ) : null}
    </main>
  );
}
