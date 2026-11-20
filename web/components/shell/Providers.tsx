'use client';

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { notify, ToastProvider } from '@/components/ui/Toast';
import { TooltipProvider } from '@/components/ui/Tooltip';
import { api } from '@/lib/api';
import { onAuthEvent } from '@/lib/authEvents';
import {
  GUEST_ME,
  ME_QUERY_KEY,
  MeContext,
  subscribeAuthBroadcast,
  useAuthUi,
  type MeContextValue,
} from '@/lib/auth';
import { copy } from '@/lib/copy';
import { purgeUserCache, registerOfflineSupport } from '@/lib/offline';
import type { Me } from '@/lib/types';

// ★X6（AC-202）：登录 / 额度两个弹窗是**一次性懒加载**的浮层——第一次被打开时才下载 chunk，
// 此后保持挂载（关闭动画与焦点归还要它活着）。静态进首屏的只有 `UserMenu` 与 `QuotaChip`。
const AuthDialog = dynamic(() => import('../auth/AuthDialog').then((module) => module.AuthDialog), {
  ssr: false,
  loading: () => null,
});
const QuotaExceededDialog = dynamic(
  () => import('../auth/QuotaExceededDialog').then((module) => module.QuotaExceededDialog),
  { ssr: false, loading: () => null },
);

function AuthDialogHosts() {
  const authEverOpened = useAuthUi((state) => state.authEverOpened);
  const quotaEverOpened = useAuthUi((state) => state.quotaEverOpened);
  return (
    <>
      {authEverOpened ? <AuthDialog /> : null}
      {quotaEverOpened ? <QuotaExceededDialog /> : null}
    </>
  );
}

/**
 * `['me']` 的唯一持有者（spec §24.3.15a）。
 *
 * * `initialMe` 来自根 `layout.tsx` 的服务端取数；它为 `null`（API 冷启动、取不到）时站点**降级为游客渲染**，
 *   客户端随后再自己取一次——不让站点因为认证接口的一次失败而白屏。
 * * 三种"身份变了"的信号都汇到这里：401 会话失效事件、`403 password_change_required`、其它标签页的
 *   `BroadcastChannel('aragon-auth')`。
 */
function MeBridge({ initialMe, children }: { initialMe: Me | null; children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const openAuth = useAuthUi((state) => state.openAuth);
  const [initialUpdatedAt] = useState(() => Date.now());

  const query = useQuery<Me>({
    queryKey: ME_QUERY_KEY,
    queryFn: () => api.me(),
    ...(initialMe ? { initialData: initialMe, initialDataUpdatedAt: initialUpdatedAt } : {}),
    staleTime: 60_000,
    retry: 0,
    refetchOnWindowFocus: false,
  });
  const me = query.data ?? GUEST_ME;

  const setMe = useCallback((next: Me) => queryClient.setQueryData(ME_QUERY_KEY, next), [queryClient]);
  const refreshMe = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
  }, [queryClient]);

  // 会话失效（过期 / 被吊销 / 被停用）：回落 guest、清 SW 的用户缓存、提示并打开登录框；
  // **不**整页刷新——那会丢掉用户正在读的报告位置（§24.3.15a）。
  useEffect(
    () =>
      onAuthEvent('unauthorized', () => {
        const current = queryClient.getQueryData<Me>(ME_QUERY_KEY);
        // 本来就是游客：这是调用方自己的"请先登录"分支，不是"登录失效"。
        if (!current?.authenticated || current.authMode === 'open') return;
        queryClient.setQueryData(ME_QUERY_KEY, GUEST_ME);
        purgeUserCache();
        notify.warn(copy.auth.sessionExpired);
        openAuth('login', { type: 'generic' });
      }),
    [openAuth, queryClient],
  );

  // 初始 / 临时密码：除改密外的接口全是 403，所以统一送去强制改密页。
  useEffect(
    () =>
      onAuthEvent('password-change-required', () => {
        if (window.location.pathname.startsWith('/account/password')) return;
        router.replace('/account/password?forced=1');
      }),
    [router],
  );

  // 另一个标签页登录 / 登出：这一页立刻跟上（否则 A 页登出后 B 页还能点"生成"，R-A16）。
  useEffect(
    () =>
      subscribeAuthBroadcast(() => {
        void queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
        void queryClient.invalidateQueries({ queryKey: ['watchlist'] });
        router.refresh();
      }),
    [queryClient, router],
  );

  const value = useMemo<MeContextValue>(() => ({ me, setMe, refreshMe }), [me, refreshMe, setMe]);

  return (
    <MeContext.Provider value={value}>
      {children}
      <AuthDialogHosts />
    </MeContext.Provider>
  );
}

/**
 * Client providers. The QueryClient is created once per browser session so
 * server rendering never shares a cache between requests (§10.2).
 *
 * ★X6：`initialMe` 由根布局在服务端取得（带 Cookie 转发），作为 `['me']` 的 `initialData`，
 * 所以首屏 HTML 里就已经是正确的身份（用户名、额度芯片），不会"先游客后用户"地闪一下（R-A2）。
 */
export function Providers({ children, initialMe = null }: { children: React.ReactNode; initialMe?: Me | null }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  // A5 / §18.4.5：service worker 在这里注册，也就是**每一页**——落在首页的读者
  // 同样要把壳缓存下来，而不是必须先打开一份报告。非生产环境是 no-op。
  useEffect(() => {
    registerOfflineSupport();
  }, []);

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={200} skipDelayDuration={400} disableHoverableContent>
        <MeBridge initialMe={initialMe}>{children}</MeBridge>
        <ToastProvider />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
