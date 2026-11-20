'use client';

import { LogIn } from 'lucide-react';

import { isGuest, useAuthUi, useMe, type AuthIntent } from '@/lib/auth';
import { copy } from '@/lib/copy';
import { copyAuthPages } from '@/lib/copy.authPages';

import { Button } from '../ui/Button';

export interface RequireAuthProps {
  children: React.ReactNode;
  intent?: AuthIntent;
  /** 游客看到的内容；缺省是一张“请先登录”的小卡片（带打开登录框的按钮）。 */
  fallback?: React.ReactNode;
}

/**
 * 登录门：已登录（或 `open` 模式）渲染子树，游客渲染 `fallback`。
 * 只是体验层——子树里的数据请求照样由服务端按会话授权。
 */
export function RequireAuth({ children, intent, fallback }: RequireAuthProps) {
  const me = useMe();
  const openAuth = useAuthUi((state) => state.openAuth);
  if (!isGuest(me)) return <>{children}</>;
  if (fallback !== undefined) return <>{fallback}</>;
  return (
    <div
      role="region"
      aria-label={copyAuthPages.errors.unauthorized.message}
      className="mx-auto mt-16 max-w-[360px] rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] p-6 text-center"
    >
      <p className="text-[13px] font-medium text-[var(--text-primary)]">{copyAuthPages.errors.unauthorized.message}</p>
      <p className="mt-1 text-[12px] text-[var(--text-secondary)]">{copy.auth.guestBrowse}</p>
      <Button
        variant="primary"
        className="mt-4"
        leadingIcon={<LogIn className="size-3.5" aria-hidden="true" />}
        onClick={() => openAuth('login', intent ?? { type: 'generic' })}
      >
        {copy.auth.signIn}
      </Button>
    </div>
  );
}
