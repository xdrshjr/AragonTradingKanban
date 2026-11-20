'use client';

import { useEffect, useId, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { copyAuthPages } from '@/lib/copy.authPages';
import { clearRecoveryUnsaved } from '@/lib/recoveryKey';

import { Button } from '../ui/Button';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Field, fieldDescribedBy } from './Field';
import { PasswordField } from './PasswordField';
import { RecoveryKeyDialog } from './RecoveryKeyDialog';

export interface RotateRecoveryKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  username: string;
  /** 新码已展示且用户点了“继续”；`setAt` 来自服务端。 */
  onRotated: (setAt: string) => void;
}

/**
 * 生成 / 轮换恢复码（登录态，需当前密码；spec §25.3.3 路径 2）。
 *
 * **单飞**（RV-Y17）：这个请求没有“旧码”可做比较-交换，只能靠前端单飞 + 服务端 3 次/分钟限速，
 * 避免“屏幕上是先返回的那把、库里是后写的那把”。提交即 `disabled`（`aria-busy`），同步守卫挡住连点。
 * 成功后密码框关闭、新码弹窗接棒（明文只存在于本组件的 state）。
 */
export function RotateRecoveryKeyDialog({ open, onOpenChange, username, onRotated }: RotateRecoveryKeyDialogProps) {
  const t = copyAuthPages.recoveryCard;
  const baseId = useId();
  const passwordId = `${baseId}-password`;
  const [password, setPassword] = useState('');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<AuthErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [issued, setIssued] = useState<{ key: string; setAt: string } | null>(null);
  const busyRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setPassword('');
      setFieldError(null);
      setBanner(null);
      setSubmitting(false);
    }
  }, [open]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busyRef.current) return;
    if (!password) {
      setFieldError(t.needPassword);
      inputRef.current?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    setFieldError(null);
    setBanner(null);
    try {
      const result = await api.rotateRecoveryKey(password);
      setPassword('');
      setIssued({ key: result.recoveryKey, setAt: result.setAt });
    } catch (error) {
      const view = describeAuthError(error);
      if (view.code === 'invalid_credentials') {
        setFieldError(view.message);
        setPassword('');
        inputRef.current?.focus();
      } else {
        setBanner(view);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <>
      <Dialog open={open && issued === null} onOpenChange={(next) => (submitting ? undefined : onOpenChange(next))}>
        <DialogContent
          title={t.confirmTitle}
          description={t.confirmBody}
          className="w-[min(92vw,420px)] p-5"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            inputRef.current?.focus();
          }}
        >
          <h2 aria-hidden="true" className="pr-6 text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">
            {t.confirmTitle}
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{t.confirmBody}</p>
          <form onSubmit={submit} noValidate aria-busy={submitting || undefined} className="mt-4 space-y-3" data-testid="rotate-recovery-form">
            <Field id={passwordId} label={t.password} error={fieldError}>
              <PasswordField
                ref={inputRef}
                id={passwordId}
                name="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                invalid={Boolean(fieldError)}
                aria-describedby={fieldDescribedBy(passwordId, { error: Boolean(fieldError) })}
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
              <Button type="submit" variant="primary" loading={submitting} disabled={submitting}>
                {t.submit}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <RecoveryKeyDialog
        open={issued !== null}
        recoveryKey={issued?.key ?? ''}
        username={username}
        variant="rotated"
        onContinue={() => {
          const at = issued?.setAt ?? '';
          setIssued(null);
          clearRecoveryUnsaved();
          onOpenChange(false);
          onRotated(at);
        }}
      />
    </>
  );
}
