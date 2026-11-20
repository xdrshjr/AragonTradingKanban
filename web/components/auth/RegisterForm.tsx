'use client';

import { ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { describeAuthError, type AuthErrorView } from '@/lib/authErrorText';
import { fill } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';
import { DEFAULT_PASSWORD_POLICY, evaluatePassword } from '@/lib/passwordPolicy';
import { markRecoveryUnsaved } from '@/lib/recoveryKey';
import type { AuthConfig, CaptchaChallenge, Me } from '@/lib/types';

import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { CaptchaField } from './CaptchaField';
import { ACCENT_TEXT, Field, fieldDescribedBy } from './Field';
import { PasswordField } from './PasswordField';
import { PasswordStrength } from './PasswordStrength';
import { RecoveryKeyDialog } from './RecoveryKeyDialog';
import { useCaptcha } from './useCaptcha';

export interface RegisterFormProps {
  /** `GET /auth/config`：规则（用户名 / 密码 / 注册模式）的唯一来源。 */
  config: AuthConfig;
  onSuccess: (me: Me) => void;
  onSwitchToLogin?: () => void;
  loginHref?: string;
}

/** 取不到 `/auth/config` 时的兜底（与服务端默认一致）。注册模式按 `open` 处理，服务端仍是最终裁决。 */
export const FALLBACK_AUTH_CONFIG: AuthConfig = {
  authMode: 'enforced',
  registrationMode: 'open',
  registrationOpen: true,
  passwordPolicy: DEFAULT_PASSWORD_POLICY,
  usernamePolicy: { min: 3, max: 20, pattern: '^[A-Za-z0-9_]+$' },
  captchaKind: 'captcha',
  forceRootPasswordChange: true,
  recoveryEnabled: true,
};

type FocusTarget = 'username' | 'password' | 'confirm' | 'captcha' | 'invite' | 'risk';

interface FieldErrors {
  username?: string;
  password?: string;
  confirm?: string;
  captcha?: string;
  invite?: string;
  risk?: string;
}

function readChallenge(error: unknown): CaptchaChallenge | null {
  if (!(error instanceof ApiError)) return null;
  const candidate = error.detail.challenge as Partial<CaptchaChallenge> | undefined;
  return candidate && typeof candidate.challengeId === 'string' && typeof candidate.image === 'string'
    ? (candidate as CaptchaChallenge)
    : null;
}

/** 注册表单（spec §24.3.6 / §24.3.15b）。规则从 `config` 来；强度条与清单实时点亮。 */
export function RegisterForm({ config, onSuccess, onSwitchToLogin, loginHref = '/login' }: RegisterFormProps) {
  const text = copyAuthPages.register;
  const baseId = useId();
  const ids = {
    username: `${baseId}-username`,
    password: `${baseId}-password`,
    confirm: `${baseId}-confirm`,
    captcha: `${baseId}-captcha`,
    invite: `${baseId}-invite`,
    risk: `${baseId}-risk`,
  };

  const closed = config.registrationMode === 'closed';
  // 验证码不可用（服务端 `registrationOpen=false` 而模式不是 closed）：注册入口只剩邀请码（免验证码）。
  const [runtimeCaptchaDown, setRuntimeCaptchaDown] = useState(false);
  const captchaDown = (!closed && !config.registrationOpen) || runtimeCaptchaDown;
  const inviteRequired = config.registrationMode === 'invite' || captchaDown;

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [captchaAnswer, setCaptchaAnswer] = useState('');
  const [inviteOpen, setInviteOpen] = useState(inviteRequired);
  const [inviteCode, setInviteCode] = useState('');
  const [risk, setRisk] = useState(false);
  const [honeypot, setHoneypot] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [banner, setBanner] = useState<AuthErrorView | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [serverRules, setServerRules] = useState<string[]>([]);
  // ★Y3：注册成功后先展示恢复码（明文只存在于这个 state 与用户的剪贴板；不进 ['me'] 缓存）。
  const [issued, setIssued] = useState<{ me: Me; key: string } | null>(null);
  const [focusRequest, setFocusRequest] = useState<{ target: FocusTarget; n: number } | null>(null);
  const submittingRef = useRef(false);

  const refs = {
    username: useRef<HTMLInputElement>(null),
    password: useRef<HTMLInputElement>(null),
    confirm: useRef<HTMLInputElement>(null),
    captcha: useRef<HTMLInputElement>(null),
    invite: useRef<HTMLInputElement>(null),
    risk: useRef<HTMLInputElement>(null),
  };

  const usingInvite = inviteCode.trim().length > 0;
  const needsCaptcha = !closed && !inviteRequired && !usingInvite;
  const captcha = useCaptcha('register', needsCaptcha);

  const policy = config.passwordPolicy ?? DEFAULT_PASSWORD_POLICY;
  const evaluation = useMemo(
    () => evaluatePassword(password, username, policy, serverRules),
    [password, policy, serverRules, username],
  );

  const requestFocus = (target: FocusTarget) => setFocusRequest((previous) => ({ target, n: (previous?.n ?? 0) + 1 }));

  useEffect(() => {
    if (!closed) refs.username.current?.focus();
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

  const usernamePattern = useMemo(() => {
    try {
      return new RegExp(config.usernamePolicy.pattern);
    } catch {
      return /^[A-Za-z0-9_]+$/;
    }
  }, [config.usernamePolicy.pattern]);

  const usernameBad = (value: string): boolean =>
    value.length < config.usernamePolicy.min || value.length > config.usernamePolicy.max || !usernamePattern.test(value);

  const usernameRuleText = fill(text.usernameRule, { min: config.usernamePolicy.min, max: config.usernamePolicy.max });
  const usernameBadText = fill(text.usernameBad, { min: config.usernamePolicy.min, max: config.usernamePolicy.max });

  const handleFailure = (error: unknown) => {
    const view = describeAuthError(error);
    const attached = readChallenge(error);
    setBanner(null);
    switch (view.code) {
      case 'username_taken':
      case 'invalid_username':
        setFieldErrors({ username: view.message });
        requestFocus('username');
        return;
      case 'weak_password':
        setServerRules(view.rules);
        setFieldErrors({ password: view.message });
        requestFocus('password');
        return;
      case 'captcha_invalid':
      case 'captcha_required':
        if (attached) captcha.adopt(attached);
        else void captcha.refresh();
        setCaptchaAnswer('');
        setFieldErrors({ captcha: view.message });
        requestFocus('captcha');
        return;
      case 'invite_invalid':
      case 'invite_required':
        setInviteOpen(true);
        setFieldErrors({ invite: view.message });
        requestFocus('invite');
        return;
      case 'captcha_unavailable':
        setRuntimeCaptchaDown(true);
        setInviteOpen(true);
        setFieldErrors({});
        setBanner(view);
        return;
      default:
        setFieldErrors({});
        setBanner(view);
    }
  };

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current || closed) return;

    const errors: FieldErrors = {};
    const name = username.trim();
    if (usernameBad(name)) errors.username = usernameBadText;
    if (!password) errors.password = copyAuthPages.login.needPassword;
    else if (!evaluation.ok) errors.password = copyAuthPages.password.unknownRule;
    if (confirm !== password) errors.confirm = text.confirmMismatch;
    if (needsCaptcha && !captchaAnswer.trim()) errors.captcha = copyAuthPages.login.needCaptcha;
    if (inviteRequired && !usingInvite) errors.invite = copyAuthPages.errors.invite_required.message;
    if (!risk) errors.risk = text.needRisk;
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setBanner(null);
      const order: FocusTarget[] = ['username', 'password', 'confirm', 'captcha', 'invite', 'risk'];
      requestFocus(order.find((key) => errors[key]) ?? 'username');
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setBanner(null);
    setFieldErrors({});
    try {
      const result = await api.register({
        username: name,
        password,
        website: honeypot,
        ...(usingInvite
          ? { inviteCode: inviteCode.trim() }
          : captcha.challenge
            ? {
                captchaId: captcha.challenge.challengeId,
                captchaAnswer: captchaAnswer.trim(),
                ...(captcha.issuedAt ? { issuedAt: captcha.issuedAt } : {}),
              }
            : {}),
      });
      // 把一次性明文从 `Me` 里剥离：下游 `completeSignIn(me)` 会把它写进 React Query 缓存。
      const { recoveryKey, ...me } = result;
      if (recoveryKey) {
        setIssued({ me, key: recoveryKey });
        return;
      }
      onSuccess(me);
    } catch (error) {
      handleFailure(error);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  if (closed) {
    return (
      <div className="space-y-3 text-center" data-testid="register-closed">
        <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">{text.closedTitle}</h2>
        <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{text.closedBody}</p>
        <p className="text-[12px] text-[var(--text-tertiary)]">
          {text.hasAccount}{' '}
          {onSwitchToLogin ? (
            <button type="button" onClick={onSwitchToLogin} className="font-medium text-[var(--accent)] underline-offset-2 hover:underline" style={ACCENT_TEXT}>
              {text.toLogin}
            </button>
          ) : (
            <Link href={loginHref} className="font-medium text-[var(--accent)] underline-offset-2 hover:underline">
              {text.toLogin}
            </Link>
          )}
        </p>
      </div>
    );
  }

  const bannerId = `${baseId}-banner`;

  const finish = (skipped: boolean) => {
    if (!issued) return;
    if (skipped && issued.me.user) markRecoveryUnsaved(issued.me.user.id);
    const { me } = issued;
    setIssued(null);
    onSuccess(me);
  };

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-describedby={bannerId} data-testid="register-form">
      <RecoveryKeyDialog
        open={issued !== null}
        recoveryKey={issued?.key ?? ''}
        username={issued?.me.user?.username ?? ''}
        onContinue={() => finish(false)}
        onSkip={() => finish(true)}
      />
      {captchaDown ? (
        <div
          role="note"
          className="rounded-[var(--radius-sm)] border border-[var(--warn)]/30 bg-[var(--warn-weak)] px-2.5 py-2 text-[12px] leading-relaxed text-[var(--warn)]"
        >
          <strong className="font-medium">{text.unavailableTitle}</strong>
          <span className="block">{text.unavailableBody}</span>
        </div>
      ) : null}

      <Field
        id={ids.username}
        label={text.username}
        hint={usernameRuleText}
        error={fieldErrors.username}
      >
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
          maxLength={config.usernamePolicy.max + 8}
          invalid={Boolean(fieldErrors.username)}
          aria-describedby={fieldDescribedBy(ids.username, { hint: true, error: Boolean(fieldErrors.username) })}
        />
      </Field>

      <Field id={ids.password} label={text.password} error={fieldErrors.password}>
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

      {needsCaptcha ? (
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
      ) : null}

      <div>
        {inviteRequired ? null : (
          <button
            type="button"
            onClick={() => setInviteOpen((value) => !value)}
            aria-expanded={inviteOpen}
            aria-controls={`${ids.invite}-region`}
            className="flex items-center gap-1 text-[12px] text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
          >
            <ChevronRight className={`size-3 transition-transform ${inviteOpen ? 'rotate-90' : ''}`} aria-hidden="true" />
            {text.inviteToggle}
          </button>
        )}
        <div id={`${ids.invite}-region`} hidden={!inviteOpen && !inviteRequired} className={inviteRequired ? '' : 'mt-2'}>
          <Field
            id={ids.invite}
            label={text.inviteLabel}
            hint={inviteRequired ? text.inviteRequiredHint : text.inviteSkipsCaptcha}
            error={fieldErrors.invite}
          >
            <Input
              ref={refs.invite}
              id={ids.invite}
              name="invite"
              value={inviteCode}
              onChange={(event) => setInviteCode(event.target.value.toUpperCase())}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              placeholder={text.invitePlaceholder}
              invalid={Boolean(fieldErrors.invite)}
              aria-describedby={fieldDescribedBy(ids.invite, { hint: true, error: Boolean(fieldErrors.invite) })}
              className="font-mono tracking-[0.12em]"
            />
          </Field>
        </div>
      </div>

      {/* 蜜罐：人类看不见也 Tab 不到；机器人会填。非空 = 服务端按"验证码错误"同样的通用错误拒绝。 */}
      <div aria-hidden="true" style={{ position: 'absolute', left: '-10000px', width: 1, height: 1, overflow: 'hidden' }}>
        <input
          type="text"
          name="website"
          tabIndex={-1}
          autoComplete="off"
          value={honeypot}
          onChange={(event) => setHoneypot(event.target.value)}
        />
      </div>

      <div>
        <label htmlFor={ids.risk} className="flex items-start gap-2 text-[12px] leading-relaxed text-[var(--text-secondary)]">
          <input
            ref={refs.risk}
            id={ids.risk}
            type="checkbox"
            checked={risk}
            onChange={(event) => setRisk(event.target.checked)}
            aria-describedby={fieldDescribedBy(ids.risk, { error: Boolean(fieldErrors.risk) })}
            className="mt-0.5 size-3.5 shrink-0 accent-[var(--accent)]"
          />
          <span>{text.risk}</span>
        </label>
        <div id={`${ids.risk}-error`} aria-live="polite" className="mt-1 text-[11.5px] text-[var(--danger)]">
          {fieldErrors.risk ?? null}
        </div>
      </div>

      <div id={bannerId} aria-live="polite" data-testid="register-error">
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
        {text.hasAccount}{' '}
        {onSwitchToLogin ? (
          <button type="button" onClick={onSwitchToLogin} className="font-medium text-[var(--accent)] underline-offset-2 hover:underline" style={ACCENT_TEXT}>
            {text.toLogin}
          </button>
        ) : (
          <Link href={loginHref} className="font-medium text-[var(--accent)] underline-offset-2 hover:underline">
            {text.toLogin}
          </Link>
        )}
      </p>
    </form>
  );
}
