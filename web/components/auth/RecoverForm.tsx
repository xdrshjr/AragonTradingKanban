'use client';

import Link from 'next/link';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { copyAuthPages } from '@/lib/copy.authPages';
import { DEFAULT_PASSWORD_POLICY, evaluatePassword, type PasswordPolicy } from '@/lib/passwordPolicy';
import { isCompleteRecoveryKey, normalizeRecoveryKey } from '@/lib/recoveryKey';
import type { CaptchaChallenge } from '@/lib/types';

import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { CaptchaField } from './CaptchaField';
import { Field, fieldDescribedBy } from './Field';
import { PasswordField } from './PasswordField';
import { PasswordStrength } from './PasswordStrength';
import { RecoveryKeyDialog } from './RecoveryKeyDialog';
import { RecoveryKeyInput } from './RecoveryKeyInput';
import { useCaptcha } from './useCaptcha';

export interface RecoverFormProps {
  policy?: PasswordPolicy;
  /** 站点关闭了自助找回：渲染“联系管理员”面板而不是表单。 */
  disabled?: boolean;
  /** 成功并确认保存新恢复码之后（页面负责跳 `/login?recovered=1`）。 */
  onDone: () => void;
  loginHref?: string;
}

type FocusTarget = 'username' | 'key' | 'password' | 'confirm' | 'captcha';

interface FieldErrors {
  username?: string;
  key?: string;
  password?: string;
  confirm?: string;
  captcha?: string;
}

function readChallenge(error: unknown): CaptchaChallenge | null {
  if (!(error instanceof ApiError)) return null;
  const candidate = error.detail.challenge as Partial<CaptchaChallenge> | undefined;
  return candidate && typeof candidate.challengeId === 'string' && typeof candidate.image === 'string'
    ? (candidate as CaptchaChallenge)
    : null;
}

/**
 * 找回密码表单（spec §25.3.3）：用户名 + 恢复码 + 新密码 + 验证码（恒必填）。
 *
 * * **单飞**：提交即 `disabled` + `aria-busy`，处理函数自带同步守卫——双击不会发出第二个请求
 *   （服务端 CAS 是第二道，见 RV-Y3 / AC-250）。
 * * **统一失败话术**：`recovery_invalid` 不区分“用户不存在 / 码错 / 未设置 / 已停用”；永远附“联系管理员”出口。
 * * 验证码在服务端一次性消耗：**任何**服务端拒绝之后都换一张新的并聚焦到下一个该动的字段。
 * * 弱密码在码校验**之前**被拒，且不消耗恢复码（AC-221），所以 `weak_password` 只点亮规则清单。
 */
export function RecoverForm({ policy = DEFAULT_PASSWORD_POLICY, disabled = false, onDone, loginHref = '/login' }: RecoverFormProps) {
  const text = copyAuthPages.recover;
  const baseId = useId();
  const ids = {
    username: `${baseId}-username`,
    key: `${baseId}-key`,
    password: `${baseId}-password`,
    confirm: `${baseId}-confirm`,
    captcha: `${baseId}-captcha`,
  };

  const [serverDisabled, setServerDisabled] = useState(false);
  const [username, setUsername] = useState('');
  const [recoveryKey, setRecoveryKey] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [captchaAnswer, setCaptchaAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [banner, setBanner] = useState<AuthErrorView | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [serverRules, setServerRules] = useState<string[]>([]);
  const [issued, setIssued] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ target: FocusTarget; n: number } | null>(null);
  const submittingRef = useRef(false);

  const refs = {
    username: useRef<HTMLInputElement>(null),
    key: useRef<HTMLInputElement>(null),
    password: useRef<HTMLInputElement>(null),
    confirm: useRef<HTMLInputElement>(null),
    captcha: useRef<HTMLInputElement>(null),
  };

  const off = disabled || serverDisabled;
  const captcha = useCaptcha('recover', !off);
  const evaluation = useMemo(
    () => evaluatePassword(password, username, policy, serverRules),
    [password, policy, serverRules, username],
  );

  const requestFocus = (target: FocusTarget) => setFocusRequest((previous) => ({ target, n: (previous?.n ?? 0) + 1 }));

  useEffect(() => {
    if (!off) refs.username.current?.focus();
    // 只在挂载时聚焦一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!focusRequest) return;
    const element = refs[focusRequest.target].current;
    element?.focus();
    if (focusRequest.target === 'captcha' || focusRequest.target === 'password') element?.select?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusRequest]);

  const freshCaptcha = (error: unknown) => {
    const attached = readChallenge(error);
    if (attached) captcha.adopt(attached);
    else void captcha.refresh();
    setCaptchaAnswer('');
  };

  const handleFailure = (error: unknown) => {
    const view = describeAuthError(error);
    setBanner(null);
    switch (view.code) {
      case 'recovery_disabled':
        setServerDisabled(true);
        return;
      case 'weak_password':
        // 弱密码在码校验之前被拒：恢复码仍然有效，只换验证码（它已被消耗）。
        setServerRules(view.rules);
        setFieldErrors({ password: view.message });
        freshCaptcha(error);
        requestFocus('password');
        return;
      case 'captcha_invalid':
      case 'captcha_required':
        freshCaptcha(error);
        setFieldErrors({ captcha: view.message });
        requestFocus('captcha');
        return;
      case 'recovery_invalid':
        // 一条统一话术：不指向任何一个字段，焦点回到用户名由用户重新核对。
        freshCaptcha(error);
        setFieldErrors({});
        setBanner(view);
        requestFocus('username');
        return;
      default:
        if (captcha.challenge) freshCaptcha(error);
        setFieldErrors({});
        setBanner(view);
    }
  };

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current || off) return;

    const errors: FieldErrors = {};
    if (!username.trim()) errors.username = text.needUsername;
    if (!recoveryKey) errors.key = text.needKey;
    else if (!isCompleteRecoveryKey(recoveryKey)) errors.key = text.keyIncomplete;
    if (!password) errors.password = text.needPassword;
    else if (!evaluation.ok) errors.password = copyAuthPages.password.unknownRule;
    if (!errors.password && confirm !== password) errors.confirm = text.confirmMismatch;
    if (!captchaAnswer.trim()) errors.captcha = text.needCaptcha;
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setBanner(null);
      const order: FocusTarget[] = ['username', 'key', 'password', 'confirm', 'captcha'];
      requestFocus(order.find((key) => errors[key]) ?? 'username');
      return;
    }
    if (!captcha.challenge) {
      setFieldErrors({ captcha: text.needCaptcha });
      requestFocus('captcha');
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setBanner(null);
    setFieldErrors({});
    try {
      const result = await api.recover({
        username: username.trim(),
        recoveryKey: normalizeRecoveryKey(recoveryKey),
        newPassword: password,
        captchaId: captcha.challenge.challengeId,
        captchaAnswer: captchaAnswer.trim(),
      });
      // 成功：旧码已作废，展示**新**码；不自动登录。
      setPassword('');
      setConfirm('');
      setIssued(result.recoveryKey);
    } catch (error) {
      handleFailure(error);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  if (off) {
    return (
      <div className="space-y-3 text-center" data-testid="recover-disabled">
        <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">{text.disabledTitle}</h2>
        <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{text.disabledBody}</p>
        <Link href={loginHref} className="inline-block text-[12px] font-medium text-[var(--accent)] underline-offset-2 hover:underline">
          {text.backToLogin}
        </Link>
      </div>
    );
  }

  const bannerId = `${baseId}-banner`;

  return (
    <>
      <RecoveryKeyDialog
        open={issued !== null}
        recoveryKey={issued ?? ''}
        username={username.trim()}
        variant="rotated"
        onContinue={() => {
          setIssued(null);
          onDone();
        }}
      />
      <form
        onSubmit={onSubmit}
        noValidate
        className="space-y-4"
        aria-describedby={bannerId}
        aria-busy={submitting || undefined}
        data-testid="recover-form"
      >
        <Field id={ids.username} label={text.username} error={fieldErrors.username}>
          <Input
            ref={refs.username}
            id={ids.username}
            name="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            invalid={Boolean(fieldErrors.username)}
            aria-describedby={fieldDescribedBy(ids.username, { error: Boolean(fieldErrors.username) })}
          />
        </Field>

        <Field id={ids.key} label={text.recoveryKey} hint={text.recoveryKeyHint} error={fieldErrors.key}>
          <RecoveryKeyInput
            ref={refs.key}
            id={ids.key}
            name="recovery-key"
            value={recoveryKey}
            onValueChange={setRecoveryKey}
            invalid={Boolean(fieldErrors.key)}
            aria-describedby={fieldDescribedBy(ids.key, { hint: true, error: Boolean(fieldErrors.key) })}
          />
        </Field>

        <Field id={ids.password} label={text.newPassword} error={fieldErrors.password}>
          <PasswordField
            ref={refs.password}
            id={ids.password}
            name="new-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              if (serverRules.length > 0) setServerRules([]);
            }}
            autoComplete="new-password"
            invalid={Boolean(fieldErrors.password)}
            aria-describedby={`${ids.password}-strength ${fieldDescribedBy(ids.password, { error: Boolean(fieldErrors.password) }) ?? ''}`.trim()}
          />
          <PasswordStrength id={`${ids.password}-strength`} evaluation={evaluation} policy={policy} />
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

        <CaptchaField
          ref={refs.captcha}
          id={ids.captcha}
          challenge={captcha.challenge}
          loading={captcha.loading}
          failure={captcha.failure}
          value={captchaAnswer}
          onChange={setCaptchaAnswer}
          onRefresh={() => {
            setCaptchaAnswer('');
            void captcha.refresh();
          }}
          error={fieldErrors.captcha}
        />

        <div id={bannerId} aria-live="polite" data-testid="recover-error">
          {banner ? (
            <p className="rounded-[var(--radius-sm)] border border-[var(--danger)]/30 bg-[var(--danger-weak)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--danger)]">
              {banner.message}
              {banner.hint ? <span className="block text-[11.5px] opacity-90">{banner.hint}</span> : null}
              {banner.code === 'recovery_invalid' ? (
                <span className="mt-1 block text-[11.5px] opacity-90">{text.stillStuck}</span>
              ) : null}
            </p>
          ) : null}
        </div>

        <Button type="submit" variant="primary" size="lg" block loading={submitting} disabled={submitting}>
          {submitting ? text.submitting : text.submit}
        </Button>

        <p className="text-center text-[12px] text-[var(--text-tertiary)]">
          <Link href={loginHref} className="font-medium text-[var(--accent)] underline-offset-2 hover:underline">
            {text.backToLogin}
          </Link>
        </p>
      </form>
    </>
  );
}
