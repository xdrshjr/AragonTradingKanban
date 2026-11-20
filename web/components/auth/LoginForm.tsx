'use client';

import Link from 'next/link';
import { useEffect, useId, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { copyAuthPages } from '@/lib/copy.authPages';
import type { CaptchaChallenge, Me } from '@/lib/types';

import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { CaptchaField } from './CaptchaField';
import { ACCENT_TEXT, Field, fieldDescribedBy } from './Field';
import { PasswordField } from './PasswordField';
import { useCaptcha } from './useCaptcha';

export interface LoginFormProps {
  /** 登录成功（响应体就是 `Me`）。调用方决定接下来去哪（页面 → `next`，弹窗 → 原地关闭）。 */
  onSuccess: (me: Me) => void;
  /** 弹窗里切到“注册”标签；缺省则渲染指向 `/register` 的链接。 */
  onSwitchToRegister?: () => void;
  registerHref?: string;
  /** ★Y3：给出则在密码框旁渲染"忘记密码？"（关闭自助找回时调用方不传）。 */
  recoveryHref?: string;
  /** 弹窗里点击“忘记密码？”时先关弹窗。 */
  onRecoveryClick?: () => void;
  /** 成功提示条（如"密码已重置"）。 */
  notice?: string;
  autoFocus?: boolean;
}

type FocusTarget = 'username' | 'password' | 'captcha';

interface FieldErrors {
  username?: string;
  password?: string;
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
 * 登录表单（spec §24.3.7 / §24.3.15b）。
 *
 * * **自适应验证码**：第一次不带验证码；收到 `422 captcha_required`（或 `invalid_credentials` 的
 *   `detail.captchaRequired`）才渲染验证码输入，响应里已附带新图。服务端不暴露"该用户是否处于失败态"。
 * * **统一话术**：不区分"用户不存在 / 密码错 / 已停用"——文案来自 `describeAuthError`。
 * * **焦点**：提交失败后焦点移到第一个非法字段；验证码错 → 自动换图、清空输入、焦点回到验证码框。
 * * **防双击**：提交中按钮 `loading`（同时 `disabled`），且处理函数自带 `submitting` 守卫。
 */
export function LoginForm({
  onSuccess,
  onSwitchToRegister,
  registerHref = '/register',
  recoveryHref,
  onRecoveryClick,
  notice,
  autoFocus = true,
}: LoginFormProps) {
  const text = copyAuthPages.login;
  const baseId = useId();
  const ids = { username: `${baseId}-username`, password: `${baseId}-password`, captcha: `${baseId}-captcha`, remember: `${baseId}-remember` };

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [captchaAnswer, setCaptchaAnswer] = useState('');
  const [captchaRequired, setCaptchaRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [banner, setBanner] = useState<AuthErrorView | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [focusRequest, setFocusRequest] = useState<{ target: FocusTarget; n: number } | null>(null);

  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const captchaRef = useRef<HTMLInputElement>(null);
  const submittingRef = useRef(false);
  const captcha = useCaptcha('login', captchaRequired);

  const requestFocus = (target: FocusTarget) => setFocusRequest((previous) => ({ target, n: (previous?.n ?? 0) + 1 }));

  useEffect(() => {
    if (autoFocus) usernameRef.current?.focus();
  }, [autoFocus]);

  // 焦点请求在提交后的渲染**之后**才落地（验证码输入框此刻才出现在 DOM 里）。
  useEffect(() => {
    if (!focusRequest) return;
    const element = { username: usernameRef, password: passwordRef, captcha: captchaRef }[focusRequest.target].current;
    element?.focus();
    if (focusRequest.target !== 'username') element?.select?.();
  }, [focusRequest]);

  const handleFailure = (error: unknown) => {
    const view = describeAuthError(error);
    const detail = error instanceof ApiError ? error.detail : {};
    const attached = readChallenge(error);

    if (view.code === 'captcha_required' || view.code === 'captcha_invalid') {
      setCaptchaRequired(true);
      // 服务端已附带新图就直接用；否则（旧图失效且没附新图）主动换一张。
      if (attached) captcha.adopt(attached);
      else void captcha.refresh();
      setCaptchaAnswer('');
      setFieldErrors({ captcha: view.message });
      setBanner(null);
      requestFocus('captcha');
      return;
    }

    if (view.code === 'invalid_credentials') {
      if (detail.captchaRequired === true) {
        // 验证码一次性：已经显示着验证码时再失败，旧图作废，必须换新（响应没附带就主动取）。
        const alreadyShown = captchaRequired;
        setCaptchaRequired(true);
        if (attached) captcha.adopt(attached);
        else if (alreadyShown) void captcha.refresh();
        setCaptchaAnswer('');
      }
      setPassword('');
      setFieldErrors({});
      setBanner(view);
      requestFocus('password');
      return;
    }

    // 锁定 / 限速 / 停用 / 网络等：没有"错在哪个字段"，焦点回到用户名，原因在提示区。
    setFieldErrors({});
    setBanner(view);
    requestFocus('username');
  };

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) return;

    const errors: FieldErrors = {};
    if (!username.trim()) errors.username = text.needUsername;
    else if (!password) errors.password = text.needPassword;
    if (captchaRequired && !captchaAnswer.trim()) errors.captcha = text.needCaptcha;
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setBanner(null);
      requestFocus(errors.username ? 'username' : errors.password ? 'password' : 'captcha');
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setBanner(null);
    setFieldErrors({});
    try {
      const me = await api.login({
        username: username.trim(),
        password,
        remember,
        ...(captchaRequired && captcha.challenge
          ? { captchaId: captcha.challenge.challengeId, captchaAnswer: captchaAnswer.trim() }
          : {}),
      });
      onSuccess(me);
    } catch (error) {
      handleFailure(error);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const bannerId = `${baseId}-banner`;

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-describedby={bannerId} data-testid="login-form">
      {notice ? (
        <p
          role="status"
          data-testid="login-notice"
          className="rounded-[var(--radius-sm)] border border-[var(--accent)]/30 bg-[var(--accent-weak)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--accent)]"
        >
          {notice}
        </p>
      ) : null}

      <Field id={ids.username} label={text.username} error={fieldErrors.username}>
        <Input
          ref={usernameRef}
          id={ids.username}
          name="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          placeholder={text.usernamePlaceholder}
          invalid={Boolean(fieldErrors.username)}
          aria-describedby={fieldDescribedBy(ids.username, { error: Boolean(fieldErrors.username) })}
        />
      </Field>

      <Field
        id={ids.password}
        label={text.password}
        error={fieldErrors.password}
        aside={
          recoveryHref ? (
            <Link
              href={recoveryHref}
              onClick={onRecoveryClick}
              className="text-[11.5px] text-[var(--text-tertiary)] underline-offset-2 hover:text-[var(--text-secondary)] hover:underline"
            >
              {text.forgot}
            </Link>
          ) : null
        }
      >
        <PasswordField
          ref={passwordRef}
          id={ids.password}
          name="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          invalid={Boolean(fieldErrors.password)}
          aria-describedby={fieldDescribedBy(ids.password, { error: Boolean(fieldErrors.password) })}
        />
      </Field>

      {captchaRequired ? (
        <CaptchaField
          ref={captchaRef}
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
          hint={text.captchaHint}
        />
      ) : null}

      <label htmlFor={ids.remember} className="flex items-start gap-2 text-[12px] text-[var(--text-secondary)]">
        <input
          id={ids.remember}
          type="checkbox"
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
          className="mt-0.5 size-3.5 accent-[var(--accent)]"
        />
        <span>
          {text.remember}
          <span className="block text-[11px] text-[var(--text-tertiary)]">{text.rememberHint}</span>
        </span>
      </label>

      <div id={bannerId} aria-live="polite" data-testid="login-error">
        {banner ? (
          <p className="rounded-[var(--radius-sm)] border border-[var(--danger)]/30 bg-[var(--danger-weak)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--danger)]">
            {banner.message}
            {banner.hint ? <span className="block text-[11.5px] opacity-90">{banner.hint}</span> : null}
          </p>
        ) : null}
      </div>

      <Button type="submit" variant="primary" size="lg" block loading={submitting}>
        {submitting ? text.submitting : text.submit}
      </Button>

      <p className="text-center text-[12px] text-[var(--text-tertiary)]">
        {text.noAccount}{' '}
        {onSwitchToRegister ? (
          <button
            type="button"
            onClick={onSwitchToRegister}
            className="font-medium text-[var(--accent)] underline-offset-2 hover:underline"
            style={ACCENT_TEXT}
          >
            {text.toRegister}
          </button>
        ) : (
          <Link href={registerHref} className="font-medium text-[var(--accent)] underline-offset-2 hover:underline">
            {text.toRegister}
          </Link>
        )}
      </p>
    </form>
  );
}
