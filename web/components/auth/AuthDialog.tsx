'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useCallback } from 'react';

import { api } from '@/lib/api';
import { useAuthActions, useAuthUi, type AuthTab } from '@/lib/auth';
import { copyAuthPages } from '@/lib/copy.authPages';
import { copy } from '@/lib/copy';
import type { Me } from '@/lib/types';

import { Dialog, DialogContent } from '../ui/Dialog';
import { Skeleton } from '../ui/Skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/Tabs';
import { notify } from '../ui/Toast';
import { LoginForm } from './LoginForm';
import { FALLBACK_AUTH_CONFIG, RegisterForm } from './RegisterForm';

/**
 * 登录 / 注册弹窗（spec §24.3.15c）。**懒加载**：只在第一次被打开时由 `Providers` 挂载。
 *
 * 关键纪律：游客点“登录后生成”弹出它，成功后**回到原页面原位置并聚焦原按钮，不自动发起生成**——
 * 花钱的动作必须由人再点一次。所以这里只做三件事：写 `['me']`、关弹窗、把焦点交还。
 */
export function AuthDialog() {
  const router = useRouter();
  const open = useAuthUi((state) => state.authOpen);
  const tab = useAuthUi((state) => state.authTab);
  const intent = useAuthUi((state) => state.intent);
  const closeAuth = useAuthUi((state) => state.closeAuth);
  const setAuthTab = useAuthUi((state) => state.setAuthTab);
  const { completeSignIn } = useAuthActions(() => router.refresh());

  const config = useQuery({
    queryKey: ['auth-config'],
    queryFn: () => api.authConfig(),
    enabled: open,
    staleTime: 5 * 60_000,
    retry: 0,
  });

  const onSuccess = useCallback(
    (me: Me) => {
      completeSignIn(me);
      closeAuth();
      if (me.user?.mustChangePassword) {
        router.push('/account/password?forced=1');
        return;
      }
      if (me.user) notify.success(`${copy.auth.signedIn}：${me.user.displayName?.trim() || me.user.username}`);
    },
    [closeAuth, completeSignIn, router],
  );

  const text = copyAuthPages.dialog;
  const title = intent?.type === 'generate' ? text.titleGenerate : text.title;

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : closeAuth())}>
      <DialogContent
        title={title}
        description={text.description}
        className="w-[min(92vw,420px)] max-h-[92dvh] overflow-y-auto p-6"
        onCloseAutoFocus={(event) => {
          // 登录成功后：焦点交给“原来想点的那个控件”（比如生成按钮），而不是随 Radix 回到已重排的触发器。
          const focusId = intent?.focusId;
          const target = focusId ? document.getElementById(focusId) : null;
          if (target) {
            event.preventDefault();
            target.focus();
          }
        }}
      >
        <header className="mb-4 pr-6">
          <h2 aria-hidden="true" className="text-[16px] font-semibold tracking-tight text-[var(--text-primary)]">
            {title}
          </h2>
          <p className="mt-1 text-[12px] leading-relaxed text-[var(--text-secondary)]">{text.description}</p>
        </header>

        <Tabs value={tab} onValueChange={(value) => setAuthTab(value as AuthTab)}>
          <TabsList aria-label={text.tabs} className="mb-4">
            <TabsTrigger value="login">{copy.auth.signIn}</TabsTrigger>
            <TabsTrigger value="register">{copy.auth.signUp}</TabsTrigger>
          </TabsList>
          <TabsContent value="login">
            <LoginForm
              onSuccess={onSuccess}
              onSwitchToRegister={() => setAuthTab('register')}
              recoveryHref={config.data?.recoveryEnabled === false ? undefined : '/recover'}
              onRecoveryClick={closeAuth}
            />
          </TabsContent>
          <TabsContent value="register">
            {config.isPending ? (
              <div className="space-y-3" aria-busy="true">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
              </div>
            ) : (
              <RegisterForm
                config={config.data ?? FALLBACK_AUTH_CONFIG}
                onSuccess={onSuccess}
                onSwitchToLogin={() => setAuthTab('login')}
              />
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
