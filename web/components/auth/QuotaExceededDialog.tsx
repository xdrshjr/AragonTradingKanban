'use client';

import Link from 'next/link';
import { useEffect, useId, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { useAuthUi, useCan, useMe, useMeActions } from '@/lib/auth';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { fill } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';
import { bucketView, formatReset, resetCountdown } from '@/lib/quota';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Segmented } from '../ui/Tabs';
import { notify } from '../ui/Toast';
import { QuotaRing } from './QuotaRing';

const AMOUNTS = [
  { value: '1', label: '1 次' },
  { value: '3', label: '3 次' },
  { value: '5', label: '5 次' },
] as const;

const REASON_MAX = 200;

/**
 * 额度用完对话框（spec §24.3.15c）——**不是一个红色 toast**。
 *
 * 说清四件事：用了多少、什么时候重置、手上还有没有赠送额度、能不能申请（以及申请的状态）。
 * 全站名额已满（`scope='site'`）时**不显示申请表单**：批准也无济于事（§24.3.8）。
 * 打开来源两处：额度芯片归零后的点击，与 `429 quota_exceeded` 的服务端兜底（并发 / 多标签页导致预检过时）。
 */
export function QuotaExceededDialog() {
  const text = copyAuthPages.quotaDialog;
  const open = useAuthUi((state) => state.quotaOpen);
  const detail = useAuthUi((state) => state.quotaDetail);
  const closeQuota = useAuthUi((state) => state.closeQuota);
  const me = useMe();
  const { refreshMe } = useMeActions();
  const mayRequest = useCan('quota.request');
  const reasonId = useId();

  const resource = detail?.resource ?? 'report';
  const bucket = resource === 'deep_ask' ? me.quota?.deepAsk : me.quota?.report;
  const view = bucketView(bucket);
  const site = detail?.scope === 'site';
  const resetAt = detail?.resetAt ?? bucket?.resetAt ?? null;
  const tz = me.quota?.tz;

  const used = detail ? detail.usedToday : (view?.usedToday ?? 0);
  const limit = detail ? detail.limit : (view?.unlimited ? null : (view?.limit ?? null));
  const bonus = detail ? detail.bonus : (view?.bonus ?? 0);
  const pending = Boolean(bucket?.pendingRequest);
  const canRequest = !site && mayRequest && (detail?.canRequest ?? true);

  const [reason, setReason] = useState('');
  const [amount, setAmount] = useState<'1' | '3' | '5'>('3');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<AuthErrorView | null>(null);
  const [needReason, setNeedReason] = useState(false);

  useEffect(() => {
    if (!open) {
      setSubmitted(false);
      setError(null);
      setNeedReason(false);
    }
  }, [open]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    if (!reason.trim()) {
      setNeedReason(true);
      document.getElementById(reasonId)?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await api.createQuotaRequest({ resource, amount: Number(amount), reason: reason.trim() });
      setSubmitted(true);
      setReason('');
      notify.success(text.submitted);
      await refreshMe();
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'quota_request_pending') {
        // 已有待处理申请（多标签页）：刷新 `me`，对话框随之切到"处理中"。
        setSubmitted(true);
        await refreshMe();
      } else {
        setError(describeAuthError(failure));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const title = site ? text.titleSite : text.title;
  const reset = formatReset(resetAt, tz);
  const countdown = resetCountdown(resetAt);

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : closeQuota())}>
      <DialogContent title={title} className="w-[min(92vw,440px)] p-6" data-testid="quota-dialog">
        <div className="flex items-start gap-4 pr-6">
          {bucket && view && !site ? <QuotaRing bucket={bucket} size={64} /> : null}
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">{title}</h2>
            <ul className="mt-1.5 space-y-0.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
              {!site ? (
                <li>
                  {limit === null ? fill(text.usageUnknown, { used }) : fill(text.usage, { used, limit })}
                  {bonus > 0 ? ` · ${fill(text.bonusLeft, { bonus })}` : ''}
                </li>
              ) : null}
              {resetAt ? (
                <li>{site ? fill(text.resetSite, { reset: reset.trim() }) : fill(text.reset, { countdown, reset: reset.trim() })}</li>
              ) : null}
            </ul>
          </div>
        </div>

        {site ? (
          <p className="mt-4 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{text.siteBody}</p>
        ) : canRequest ? (
          pending || submitted ? (
            <p
              role="status"
              className="mt-4 rounded-[var(--radius-sm)] border border-[var(--info)]/30 bg-[var(--info-weak)] px-3 py-2 text-[12.5px] leading-relaxed text-[var(--info)]"
            >
              {text.pending}
            </p>
          ) : (
            <form onSubmit={submit} noValidate className="mt-4 space-y-3">
              <div>
                <h3 className="text-[12.5px] font-medium text-[var(--text-primary)]">{text.requestTitle}</h3>
                <p className="mt-0.5 text-[11.5px] text-[var(--text-tertiary)]">{text.requestBody}</p>
              </div>
              <div>
                <label htmlFor={reasonId} className="mb-1.5 block text-[12px] font-medium text-[var(--text-secondary)]">
                  {text.reasonLabel}
                </label>
                <textarea
                  id={reasonId}
                  data-input-field=""
                  value={reason}
                  onChange={(event) => {
                    setReason(event.target.value.slice(0, REASON_MAX));
                    if (needReason) setNeedReason(false);
                  }}
                  rows={3}
                  maxLength={REASON_MAX}
                  placeholder={text.reasonPlaceholder}
                  aria-invalid={needReason || undefined}
                  aria-describedby={`${reasonId}-meta`}
                  className="w-full resize-none rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] px-2.5 py-2 text-[13px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-tertiary)] focus:border-[var(--accent)]/60 focus:ring-2 focus:ring-[var(--accent-ring)]/40"
                />
                <div id={`${reasonId}-meta`} className="mt-1 flex items-start justify-between gap-2 text-[11px]">
                  <span aria-live="polite" className="text-[var(--danger)]">
                    {needReason ? text.needReason : ''}
                  </span>
                  <span className="num shrink-0 text-[var(--text-tertiary)]">
                    {fill(text.reasonCount, { count: reason.length })}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[12px] text-[var(--text-secondary)]">{text.amountLabel}</span>
                <Segmented
                  options={AMOUNTS}
                  value={amount}
                  onChange={(value) => setAmount(value as '1' | '3' | '5')}
                  ariaLabel={text.amountLabel}
                />
              </div>
              <div aria-live="polite">
                {error ? (
                  <p className="rounded-[var(--radius-sm)] border border-[var(--danger)]/30 bg-[var(--danger-weak)] px-2.5 py-2 text-[12px] text-[var(--danger)]">
                    {error.message}
                    {error.hint ? <span className="block text-[11.5px] opacity-90">{error.hint}</span> : null}
                  </p>
                ) : null}
              </div>
              <Button type="submit" variant="primary" block loading={submitting}>
                {submitting ? text.submitting : text.submit}
              </Button>
            </form>
          )
        ) : null}

        <div className="mt-5 flex items-center justify-between gap-2">
          <Link
            href="/account"
            onClick={closeQuota}
            className="text-[12px] text-[var(--text-secondary)] underline-offset-2 hover:text-[var(--text-primary)] hover:underline"
          >
            {text.viewAccount}
          </Link>
          <Button variant="ghost" onClick={closeQuota}>
            {site || !canRequest ? text.browse : text.close}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
