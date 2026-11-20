'use client';

import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { copyAuthPages } from '@/lib/copy.authPages';
import { DEFAULT_PASSWORD_POLICY, evaluatePassword, type PasswordPolicy } from '@/lib/passwordPolicy';

import { Button } from '../ui/Button';
import { Field, fieldDescribedBy } from './Field';
import { PasswordField } from './PasswordField';
import { PasswordStrength } from './PasswordStrength';

export interface ChangePasswordFormProps {
  username?: string;
  policy?: PasswordPolicy;
  /** 修改成功（`POST /auth/password` → 204）。调用方负责刷新 `['me']` 与跳转。 */
  onDone: () => void;
  autoFocus?: boolean;
}

type FocusTarget = 'current' | 'next' | 'confirm';

interface FieldErrors {
  current?: string;
  next?: string;
  confirm?: string;
}

/**
 * 改密表单：账号页的“安全”标签与强制改密页共用。
 *
 * 成功后服务端会吊销**除当前会话外**的全部会话、清 `must_change_password` / `password_is_default`；
 * 这里不做任何本地推断，只通知调用方去重取 `['me']`。
 */
export function ChangePasswordForm({ username = '', policy = DEFAULT_PASSWORD_POLICY, onDone, autoFocus = true }: ChangePasswordFormProps) {
  const text = copyAuthPages.changePassword;
  const baseId = useId();
  const ids = { current: `${baseId}-current`, next: `${baseId}-next`, confirm: `${baseId}-confirm` };

  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [banner, setBanner] = useState<AuthErrorView | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [serverRules, setServerRules] = useState<string[]>([]);
  const [focusRequest, setFocusRequest] = useState<{ target: FocusTarget; n: number } | null>(null);
  const submittingRef = useRef(false);
  const refs = {
    current: useRef<HTMLInputElement>(null),
    next: useRef<HTMLInputElement>(null),
    confirm: useRef<HTMLInputElement>(null),
  };

  const evaluation = useMemo(
    () => evaluatePassword(next, username, policy, serverRules),
    [next, policy, serverRules, username],
  );

  useEffect(() => {
    if (autoFocus) refs.current.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFocus]);

  useEffect(() => {
    if (!focusRequest) return;
    const element = refs[focusRequest.target].current;
    element?.focus();
    element?.select?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest]);

  const requestFocus = (target: FocusTarget) => setFocusRequest((previous) => ({ target, n: (previous?.n ?? 0) + 1 }));

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) return;

    const errors: FieldErrors = {};
    if (!current) errors.current = text.needCurrent;
    if (!next) errors.next = copyAuthPages.login.needPassword;
    else if (next === current) errors.next = text.sameAsOld;
    else if (!evaluation.ok) errors.next = copyAuthPages.password.unknownRule;
    if (confirm !== next) errors.confirm = text.mismatch;
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setBanner(null);
      requestFocus(errors.current ? 'current' : errors.next ? 'next' : 'confirm');
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setBanner(null);
    setFieldErrors({});
    try {
      await api.changePassword({ currentPassword: current, newPassword: next });
      setCurrent('');
      setNext('');
      setConfirm('');
      onDone();
    } catch (error) {
      const view = describeAuthError(error);
      if (view.code === 'invalid_credentials') {
        setFieldErrors({ current: view.message });
        requestFocus('current');
      } else if (view.code === 'weak_password') {
        setServerRules(view.rules);
        setFieldErrors({ next: view.message });
        requestFocus('next');
      } else {
        setBanner(view);
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const bannerId = `${baseId}-banner`;

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-describedby={bannerId} data-testid="change-password-form">
      {/* 密码管理器据此关联“这是哪个账号的密码”。不可见，不参与交互。 */}
      {username ? (
        <input type="text" name="username" value={username} autoComplete="username" readOnly hidden aria-hidden="true" tabIndex={-1} />
      ) : null}

      <Field id={ids.current} label={text.current} error={fieldErrors.current}>
        <PasswordField
          ref={refs.current}
          id={ids.current}
          name="current-password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          autoComplete="current-password"
          invalid={Boolean(fieldErrors.current)}
          aria-describedby={fieldDescribedBy(ids.current, { error: Boolean(fieldErrors.current) })}
        />
      </Field>

      <Field id={ids.next} label={text.next} error={fieldErrors.next}>
        <PasswordField
          ref={refs.next}
          id={ids.next}
          name="new-password"
          value={next}
          onChange={(event) => {
            setNext(event.target.value);
            if (serverRules.length > 0) setServerRules([]);
          }}
          autoComplete="new-password"
          invalid={Boolean(fieldErrors.next)}
          aria-describedby={`${ids.next}-strength ${fieldDescribedBy(ids.next, { error: Boolean(fieldErrors.next) }) ?? ''}`.trim()}
        />
        <PasswordStrength id={`${ids.next}-strength`} evaluation={evaluation} policy={policy} />
      </Field>

      <Field id={ids.confirm} label={text.confirm} error={fieldErrors.confirm}>
        <PasswordField
          ref={refs.confirm}
          id={ids.confirm}
          name="confirm-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          autoComplete="new-password"
          invalid={Boolean(fieldErrors.confirm)}
          aria-describedby={fieldDescribedBy(ids.confirm, { error: Boolean(fieldErrors.confirm) })}
        />
      </Field>

      <div id={bannerId} aria-live="polite">
        {banner ? (
          <p className="rounded-[var(--radius-sm)] border border-[var(--danger)]/30 bg-[var(--danger-weak)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--danger)]">
            {banner.message}
            {banner.hint ? <span className="block text-[11.5px] opacity-90">{banner.hint}</span> : null}
          </p>
        ) : null}
      </div>

      <Button type="submit" variant="primary" block loading={submitting}>
        {submitting ? text.submitting : text.submit}
      </Button>
    </form>
  );
}
