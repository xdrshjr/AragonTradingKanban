'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';
import { dateTime } from '@/lib/format';
import type { InviteOut } from '@/lib/types';

import { Field, fieldDescribedBy } from '../auth/Field';
import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { NativeSelect } from '../ui/Select';
import { StatusDot, type StatusKind } from '../ui/StatusDot';
import { notify } from '../ui/Toast';
import { AdminPageHeader, CopyButton, EmptyState, ErrorState, SecretBox, TableSkeleton } from './AdminPrimitives';
import { adminKeys } from './keys';
import { notifyFailure } from './UserActionDialogs';

const t = copyAdmin.invites;

export type InviteState = 'active' | 'exhausted' | 'expired' | 'revoked';

/** 邀请码当前状态：以服务端的 `status` 为准，缺省时按字段推出（仅用于展示）。 */
export function inviteState(invite: InviteOut, now: number = Date.now()): InviteState {
  // 服务端已推好（撤销 > 过期 > 用尽 > 可用）；只有它缺席时才在客户端按字段推。
  if (invite.status) return invite.status;
  if (invite.disabledAt) return 'revoked';
  if (invite.usedCount >= invite.maxUses) return 'exhausted';
  if (invite.expiresAt && new Date(invite.expiresAt).getTime() <= now) return 'expired';
  return 'active';
}

const STATE_DOT: Record<InviteState, StatusKind> = {
  active: 'active',
  exhausted: 'disabled',
  expired: 'disabled',
  revoked: 'danger',
};

/* ── 创建对话框 ────────────────────────────────────────────────────────────── */

type ExpiryChoice = '7' | '30' | '90' | 'permanent' | 'custom';

function CreateInviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const [maxUses, setMaxUses] = useState('1');
  const [expiry, setExpiry] = useState<ExpiryChoice>('30');
  const [customDays, setCustomDays] = useState('14');
  const [bonus, setBonus] = useState('0');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<{ maxUses?: string; days?: string; bonus?: string; form?: string }>({});
  const [created, setCreated] = useState<InviteOut | null>(null);
  const maxUsesRef = useRef<HTMLInputElement>(null);
  const daysRef = useRef<HTMLInputElement>(null);
  const bonusRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setMaxUses('1');
      setExpiry('30');
      setCustomDays('14');
      setBonus('0');
      setNote('');
      setErrors({});
      setCreated(null);
    }
  }, [open]);

  const create = useMutation({
    mutationFn: (body: Parameters<typeof adminApi.adminCreateInvite>[0]) => adminApi.adminCreateInvite(body),
    onSuccess: (invite) => {
      setCreated(invite);
      void queryClient.invalidateQueries({ queryKey: adminKeys.invites });
    },
    onError: (error) => notifyFailure(error),
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (create.isPending) return;
    const f = t.form;
    const uses = Number(maxUses);
    const bonusN = Number(bonus === '' ? 0 : bonus);
    const days = expiry === 'permanent' ? null : expiry === 'custom' ? Number(customDays) : Number(expiry);
    const next: typeof errors = {};
    if (!Number.isInteger(uses) || uses < 1 || uses > 100) next.maxUses = f.invalidMaxUses;
    if (days !== null && (!Number.isInteger(days) || days < 1 || days > 90)) next.days = f.invalidDays;
    if (!Number.isInteger(bonusN) || bonusN < 0 || bonusN > 20) next.bonus = f.invalidBonus;
    setErrors(next);
    if (next.maxUses) return maxUsesRef.current?.focus();
    if (next.days) return daysRef.current?.focus();
    if (next.bonus) return bonusRef.current?.focus();
    create.mutate({
      maxUses: uses,
      expiresInDays: days,
      bonusCredits: bonusN,
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  };

  const f = t.form;
  return (
    <Dialog open={open} onOpenChange={(next) => (create.isPending ? undefined : onOpenChange(next))}>
      <DialogContent title={f.title} hideClose className="w-[min(92vw,460px)] p-5">
        {created ? (
          <div>
            <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{f.doneTitle}</h2>
            <p className="mt-1.5 text-[12.5px] text-[var(--text-secondary)]">{f.doneBody}</p>
            <div className="mt-3">
              <SecretBox value={created.code} label={t.columns.code} />
            </div>
            <div className="mt-5 flex justify-end">
              <Button variant="primary" onClick={() => onOpenChange(false)}>
                {f.doneClose}
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} noValidate>
            <h2 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">{f.title}</h2>
            <div className="mt-4 space-y-3">
              <Field id="invite-max-uses" label={f.maxUses} error={errors.maxUses}>
                <Input
                  id="invite-max-uses"
                  ref={maxUsesRef}
                  type="number"
                  inputMode="numeric"
                  value={maxUses}
                  onChange={(event) => setMaxUses(event.target.value)}
                  invalid={Boolean(errors.maxUses)}
                  aria-describedby={fieldDescribedBy('invite-max-uses', { error: Boolean(errors.maxUses) })}
                  className="w-28"
                />
              </Field>
              <Field id="invite-expiry" label={f.expiry}>
                <NativeSelect
                  id="invite-expiry"
                  value={expiry}
                  onChange={(event) => setExpiry(event.target.value as ExpiryChoice)}
                  className="w-full"
                >
                  <option value="7">{fill(f.expiryDays, { n: 7 })}</option>
                  <option value="30">{fill(f.expiryDays, { n: 30 })}</option>
                  <option value="90">{fill(f.expiryDays, { n: 90 })}</option>
                  <option value="custom">{f.expiryCustom}</option>
                  <option value="permanent">{f.expiryPermanent}</option>
                </NativeSelect>
              </Field>
              {expiry === 'custom' ? (
                <Field id="invite-days" label={f.expiryCustomLabel} error={errors.days}>
                  <Input
                    id="invite-days"
                    ref={daysRef}
                    type="number"
                    inputMode="numeric"
                    value={customDays}
                    onChange={(event) => setCustomDays(event.target.value)}
                    invalid={Boolean(errors.days)}
                    aria-describedby={fieldDescribedBy('invite-days', { error: Boolean(errors.days) })}
                    className="w-28"
                  />
                </Field>
              ) : null}
              <Field id="invite-bonus" label={f.bonus} error={errors.bonus}>
                <Input
                  id="invite-bonus"
                  ref={bonusRef}
                  type="number"
                  inputMode="numeric"
                  value={bonus}
                  onChange={(event) => setBonus(event.target.value)}
                  invalid={Boolean(errors.bonus)}
                  aria-describedby={fieldDescribedBy('invite-bonus', { error: Boolean(errors.bonus) })}
                  className="w-28"
                />
              </Field>
              <Field id="invite-note" label={f.note}>
                <Input
                  id="invite-note"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  maxLength={80}
                  autoComplete="off"
                />
              </Field>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={create.isPending}>
                {copyAdmin.common.cancel}
              </Button>
              <Button type="submit" variant="primary" loading={create.isPending}>
                {f.submit}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ── 面板 ──────────────────────────────────────────────────────────────────── */

/** 邀请码：创建 / 复制 / 撤销（撤销不影响已注册用户）。 */
export function InvitesPanel() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<InviteOut | null>(null);
  const query = useQuery({ queryKey: adminKeys.invites, queryFn: () => adminApi.adminInvites() });
  const items = query.data?.items ?? [];

  const revoke = async (invite: InviteOut) => {
    try {
      await adminApi.adminRevokeInvite(invite.id);
      notify.success(fill(t.revoked, { code: invite.code }));
      void queryClient.invalidateQueries({ queryKey: adminKeys.invites });
      setRevoking(null);
    } catch (error) {
      notifyFailure(error);
    }
  };

  return (
    <div>
      <AdminPageHeader
        title={t.title}
        subtitle={t.subtitle}
        actions={
          <Button variant="primary" leadingIcon={<Plus className="size-3.5" aria-hidden="true" />} onClick={() => setCreating(true)}>
            {t.create}
          </Button>
        }
      />

      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)]">
        {query.isError ? (
          <div className="p-4">
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          </div>
        ) : query.isPending ? (
          <TableSkeleton rows={4} />
        ) : items.length === 0 ? (
          <EmptyState
            title={t.empty}
            hint={t.emptyHint}
            action={
              <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
                {t.create}
              </Button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-[12.5px]">
              <caption className="sr-only">{t.title}</caption>
              <thead>
                <tr className="border-b border-[var(--border-subtle)] text-[11px] text-[var(--text-tertiary)]">
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.code}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.usage}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.bonus}</th>
                  <th scope="col" className="hidden px-3 py-2 font-medium min-[900px]:table-cell">{t.columns.expires}</th>
                  <th scope="col" className="px-3 py-2 font-medium">{t.columns.status}</th>
                  <th scope="col" className="hidden px-3 py-2 font-medium min-[900px]:table-cell">{t.columns.note}</th>
                  <th scope="col" className="px-3 py-2"><span className="sr-only">{t.columns.actions}</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((invite) => {
                  const state = inviteState(invite);
                  return (
                    <tr key={invite.id} className="border-b border-[var(--border-subtle)] last:border-0">
                      <td className="px-3 py-2">
                        <span className="mono select-all text-[13px] font-medium tracking-wide text-[var(--text-primary)]">
                          {invite.code}
                        </span>
                      </td>
                      <td className="num px-3 py-2 text-[var(--text-secondary)]">
                        {invite.usedCount} / {invite.maxUses}
                      </td>
                      <td className="num px-3 py-2 text-[var(--text-secondary)]">
                        {invite.bonusCredits > 0 ? `+${invite.bonusCredits}` : copyAdmin.common.emDash}
                      </td>
                      <td className="hidden px-3 py-2 text-[var(--text-tertiary)] min-[900px]:table-cell">
                        {invite.expiresAt ? dateTime(invite.expiresAt) : t.permanent}
                      </td>
                      <td className="px-3 py-2">
                        <StatusDot status={STATE_DOT[state]} label={t.status[state]} />
                      </td>
                      <td className="hidden max-w-[180px] truncate px-3 py-2 text-[var(--text-tertiary)] min-[900px]:table-cell">
                        {invite.note || copyAdmin.common.emDash}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1.5">
                          <CopyButton value={invite.code} label={fill(t.copyCode, { code: invite.code })} />
                          {state !== 'revoked' ? (
                            <Button
                              size="sm"
                              variant="danger"
                              aria-label={fill(t.revokeTitle, { code: invite.code })}
                              onClick={() => setRevoking(invite)}
                            >
                              {t.revoke}
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <CreateInviteDialog open={creating} onOpenChange={setCreating} />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(next) => (next ? undefined : setRevoking(null))}
        title={revoking ? fill(t.revokeTitle, { code: revoking.code }) : t.revoke}
        description={t.revokeBody}
        confirmLabel={t.revokeConfirm}
        onConfirm={() => (revoking ? revoke(revoking) : undefined)}
      />
    </div>
  );
}
