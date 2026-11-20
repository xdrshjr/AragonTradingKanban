'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { fill } from '@/lib/copy';
import { copyAdmin } from '@/lib/copy.admin';

import { Field, fieldDescribedBy } from '../auth/Field';
import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { notify } from '../ui/Toast';
import { notifyFailure } from './UserActionDialogs';
import { adminKeys } from './keys';

const t = copyAdmin.users.bulk;

/** 与后端 `quota_service.GRANT_MIN/MAX` 同值（§25.3.5b）。 */
export const BULK_GRANT_MIN = -50;
export const BULK_GRANT_MAX = 50;
/** 一次最多 100 个目标（`BulkQuotaRequest.userIds`）。 */
export const BULK_MAX_TARGETS = 100;

export interface BulkQuotaDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userIds: string[];
  /** 成功后（全成）；调用方清除选择。 */
  onDone: () => void;
}

/**
 * 批量赠送 / 收回额度（§25.3.5b）。**全成全败**由服务端两遍制保证；这里只做体验层：
 * 范围 −50..50 且非 0、单飞（提交即 `disabled`）、结果里把“被夹成 0 的收回”（`skipped`）说清楚。
 */
export function BulkQuotaDialog({ open, onOpenChange, userIds, onDone }: BulkQuotaDialogProps) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState('5');
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const busyRef = useRef(false);
  const amountRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setAmount('5');
      setNote('');
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  const parsed = Number(amount);
  const valid =
    amount.trim() !== '' && Number.isInteger(parsed) && parsed !== 0 && parsed >= BULK_GRANT_MIN && parsed <= BULK_GRANT_MAX;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busyRef.current) return;
    if (!valid) {
      setError(t.invalid);
      amountRef.current?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await adminApi.adminBulkQuota({
        userIds,
        grant: parsed,
        ...(note.trim() ? { note: note.trim() } : {}),
      });
      notify.success(
        result.skipped > 0
          ? fill(t.doneSkipped, { applied: result.applied, skipped: result.skipped })
          : fill(t.done, { applied: result.applied }),
      );
      void queryClient.invalidateQueries({ queryKey: adminKeys.usersRoot });
      void queryClient.invalidateQueries({ queryKey: adminKeys.overview });
      onDone();
      onOpenChange(false);
    } catch (failure) {
      notifyFailure(failure);
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (submitting ? undefined : onOpenChange(next))}>
      <DialogContent
        title={t.title}
        description={fill(t.body, { n: userIds.length })}
        hideClose
        className="w-[min(92vw,440px)] p-5"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          amountRef.current?.focus();
          amountRef.current?.select();
        }}
      >
        <h2 aria-hidden="true" className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">
          {t.title}
        </h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{fill(t.body, { n: userIds.length })}</p>
        <form onSubmit={submit} noValidate aria-busy={submitting || undefined} className="mt-4 space-y-3" data-testid="bulk-quota-form">
          <Field id="bulk-amount" label={t.amount} hint={t.amountHint} error={error}>
            <Input
              ref={amountRef}
              id="bulk-amount"
              type="number"
              inputMode="numeric"
              min={BULK_GRANT_MIN}
              max={BULK_GRANT_MAX}
              step={1}
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              invalid={Boolean(error)}
              aria-describedby={fieldDescribedBy('bulk-amount', { hint: true, error: Boolean(error) })}
              className="num"
            />
          </Field>
          <Field id="bulk-note" label={t.note}>
            <Input
              id="bulk-note"
              value={note}
              maxLength={120}
              onChange={(event) => setNote(event.target.value)}
              placeholder={t.notePlaceholder}
              autoComplete="off"
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
              {copyAdmin.common.cancel}
            </Button>
            <Button
              type="submit"
              variant={valid && parsed < 0 ? 'danger' : 'primary'}
              loading={submitting}
              disabled={submitting || !valid}
            >
              {valid && parsed < 0 ? t.submitRevoke : t.submit}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
