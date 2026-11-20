'use client';

import { useEffect, useId, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { fill } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Input } from '../ui/Input';
import { Field, fieldDescribedBy } from './Field';
import { PasswordField } from './PasswordField';

export interface DeleteAccountDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  username: string;
  /** 注销成功（服务端已清 Cookie）；调用方负责回落 guest 并跳首页。 */
  onDeleted: () => void;
}

/**
 * 自助注销确认（spec §25.3.4）：**同时**输入当前密码与键入用户名，“确认注销”才可用。
 * 后端对 `confirm` 另有校验（`422 validation_error{field:'confirm'}`），这里只是体验层；
 * 提交即 `disabled`，同步守卫防双击。root / 游客 / service 的入口由调用方不渲染。
 */
export function DeleteAccountDialog({ open, onOpenChange, username, onDeleted }: DeleteAccountDialogProps) {
  const t = copyAuthPages.danger;
  const baseId = useId();
  const ids = { password: `${baseId}-password`, confirm: `${baseId}-confirm` };
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [banner, setBanner] = useState<AuthErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const busyRef = useRef(false);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setPassword('');
      setConfirm('');
      setPasswordError(null);
      setConfirmError(null);
      setBanner(null);
      setSubmitting(false);
    }
  }, [open]);

  const ready = password.length > 0 && confirm === username;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busyRef.current || !ready) return;
    busyRef.current = true;
    setSubmitting(true);
    setPasswordError(null);
    setConfirmError(null);
    setBanner(null);
    try {
      await api.deleteAccount({ password, confirm });
      onDeleted();
    } catch (error) {
      const view = describeAuthError(error);
      if (view.code === 'invalid_credentials') {
        setPasswordError(view.message);
        setPassword('');
        passwordRef.current?.focus();
      } else {
        setBanner(view);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (submitting ? undefined : onOpenChange(next))}>
      <DialogContent
        title={t.dialogTitle}
        description={fill(t.dialogBody, { name: username })}
        hideClose
        className="w-[min(92vw,440px)] p-5"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          passwordRef.current?.focus();
        }}
      >
        <h2 aria-hidden="true" className="text-[14px] font-semibold tracking-tight text-[var(--danger)]">
          {t.dialogTitle}
        </h2>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{fill(t.dialogBody, { name: username })}</p>
        <form onSubmit={submit} noValidate aria-busy={submitting || undefined} className="mt-4 space-y-3" data-testid="delete-account-form">
          <Field id={ids.password} label={t.password} error={passwordError}>
            <PasswordField
              ref={passwordRef}
              id={ids.password}
              name="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              invalid={Boolean(passwordError)}
              aria-describedby={fieldDescribedBy(ids.password, { error: Boolean(passwordError) })}
            />
          </Field>
          <Field id={ids.confirm} label={t.confirm} hint={t.confirmHint} error={confirmError}>
            <Input
              ref={confirmRef}
              id={ids.confirm}
              name="confirm-username"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              placeholder={username}
              invalid={Boolean(confirmError)}
              aria-describedby={fieldDescribedBy(ids.confirm, { hint: true, error: Boolean(confirmError) })}
              className="font-mono"
            />
          </Field>
          <div aria-live="polite">
            {banner ? (
              <p className="rounded-[var(--radius-sm)] border border-[var(--danger)]/30 bg-[var(--danger-weak)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--danger)]">
                {banner.message}
                {banner.hint ? <span className="block text-[11.5px] opacity-90">{banner.hint}</span> : null}
              </p>
            ) : null}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
              {t.cancel}
            </Button>
            <Button type="submit" variant="danger" loading={submitting} disabled={!ready || submitting}>
              {t.submit}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
