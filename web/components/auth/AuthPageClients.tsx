'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback } from 'react';

import { useAuthActions, useMeActions } from '@/lib/auth';
import { copy } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';
import type { PasswordPolicy } from '@/lib/passwordPolicy';
import type { AuthConfig, Me } from '@/lib/types';

import { Button } from '../ui/Button';
import { notify } from '../ui/Toast';
import { ChangePasswordForm } from './ChangePasswordForm';
import { LoginForm } from './LoginForm';
import { RecoverForm } from './RecoverForm';
import { RegisterForm } from './RegisterForm';

const FORCED_URL = '/account/password?forced=1';

/** 登录 / 注册成功后的去向：必须改密 → 强制改密页；否则 `next`（已被服务端页面 `safeNext` 过滤）。 */
function useAfterSignIn(next: string) {
  const router = useRouter();
  const { completeSignIn } = useAuthActions(() => router.refresh());
  return useCallback(
    (me: Me) => {
      completeSignIn(me);
      if (me.user?.mustChangePassword) {
        router.replace(`${FORCED_URL}&next=${encodeURIComponent(next)}`);
        return;
      }
      if (me.user) notify.success(`${copy.auth.signedIn}：${me.user.displayName?.trim() || me.user.username}`);
      router.replace(next);
    },
    [completeSignIn, next, router],
  );
}

export function LoginPageClient({
  next,
  recoveryEnabled = true,
  recovered = false,
}: {
  next: string;
  recoveryEnabled?: boolean;
  recovered?: boolean;
}) {
  const onSuccess = useAfterSignIn(next);
  return (
    <LoginForm
      onSuccess={onSuccess}
      registerHref={`/register?next=${encodeURIComponent(next)}`}
      recoveryHref={recoveryEnabled ? '/recover' : undefined}
      notice={recovered ? copyAuthPages.login.recovered : undefined}
    />
  );
}

export function RegisterPageClient({ config, next }: { config: AuthConfig; next: string }) {
  const onSuccess = useAfterSignIn(next);
  return <RegisterForm config={config} onSuccess={onSuccess} loginHref={`/login?next=${encodeURIComponent(next)}`} />;
}

/** ★Y3：找回页。成功并保存新恢复码后跳登录页（带“密码已重置”提示）。 */
export function RecoverPageClient({ policy, disabled }: { policy: PasswordPolicy; disabled: boolean }) {
  const router = useRouter();
  return <RecoverForm policy={policy} disabled={disabled} onDone={() => router.replace('/login?recovered=1')} />;
}

/** 强制改密页（也用于主动改密的独立入口）。成功后重取 `['me']` 并回到 `next`。 */
export function ForcedPasswordClient({
  username,
  next,
  forced,
}: {
  username: string;
  next: string;
  forced: boolean;
}) {
  const router = useRouter();
  const { refreshMe } = useMeActions();
  const { signOut } = useAuthActions(() => router.refresh());
  const text = copyAuthPages.changePassword;

  return (
    <div className="space-y-5">
      <ChangePasswordForm
        username={username}
        onDone={() => {
          notify.success(text.done);
          void refreshMe().then(() => {
            router.replace(next);
            router.refresh();
          });
        }}
      />
      <div className="flex items-center justify-between border-t border-[var(--border-subtle)] pt-4 text-[12px]">
        {forced ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              void signOut().then(() => {
                notify.info(copy.auth.signedOut);
                router.replace('/login');
              })
            }
          >
            {text.forcedSignOut}
          </Button>
        ) : (
          <Link href="/account" className="text-[var(--text-secondary)] underline-offset-2 hover:underline">
            {text.back}
          </Link>
        )}
      </div>
    </div>
  );
}
