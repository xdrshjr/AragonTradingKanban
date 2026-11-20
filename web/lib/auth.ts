'use client';

/**
 * 认证状态与动作的客户端入口（spec §24.3.15a）。
 *
 * 单一真源是 TanStack Query 的 `['me']`：根 `layout.tsx`（服务端）取一次 `Me`，经
 * `Providers initialMe` 作为 `initialData` 注入；这里只暴露**读取**（`useMe/useCan`）与
 * **动作**（`useRequireAuth` / `useAuthActions`）。授权依据永远在服务端——`can()` 只负责体验层。
 *
 * ★没有 `MeProvider` 的渲染环境（单元测试、孤立渲染一个组件）得到的是 `OPEN_ME`：与 `AUTH_MODE=open`
 * 同义，即"没有认证层时按原本的单人工具行为渲染"。生产里根布局永远挂着 Providers，所以这条退路只在
 * 孤立渲染时生效，不会让真实访客获得任何多出来的东西（服务端照常拒绝）。
 */

import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useMemo } from 'react';
import { create } from 'zustand';

import { api } from './api';
import type { QuotaExceededDetail } from './authErrors';
import { purgeUserCache } from './offline';
import { bucketView } from './quota';
import { GUEST_PERMISSIONS, can as canPermission, permissionsForRole } from './permissions';
import type { Me } from './types';

export { safeNext } from './safeNext';

export const ME_QUERY_KEY = ['me'] as const;

/** 游客（或服务端拿不到 `me` 时的降级）：`authenticated:false`，只有 guest 权限。 */
export const GUEST_ME: Me = {
  authMode: 'enforced',
  authenticated: false,
  user: null,
  permissions: [...GUEST_PERMISSIONS],
  quota: null,
};

/** `AUTH_MODE=open` / 无 Provider 的孤立渲染：权限全集，视为已认证。 */
export const OPEN_ME: Me = {
  authMode: 'open',
  authenticated: true,
  user: null,
  // root 是 admin 与 service 的超集，所以“权限全集”就是 root 的展开。
  permissions: [...permissionsForRole('root')],
  quota: null,
};

export interface MeContextValue {
  me: Me;
  /** 直接写入 `['me']`（登录/注册成功后，响应体就是 `Me`）。 */
  setMe: (me: Me) => void;
  /** 作废并重取 `['me']`（改密、申请额度、发起生成之后）。 */
  refreshMe: () => Promise<void>;
}

export const MeContext = createContext<MeContextValue>({
  me: OPEN_ME,
  setMe: () => {},
  refreshMe: async () => {},
});

export function useMe(): Me {
  return useContext(MeContext).me;
}

export function useMeActions(): Pick<MeContextValue, 'setMe' | 'refreshMe'> {
  const { setMe, refreshMe } = useContext(MeContext);
  return { setMe, refreshMe };
}

/** 当前主体是否拥有某权限（体验层，不是授权依据）。 */
export function useCan(permission: string): boolean {
  const me = useMe();
  return canPermission(me, permission);
}

/** 游客 = 没有会话，且不是 `open` 模式。 */
export function isGuest(me: Me): boolean {
  return !me.authenticated && me.authMode !== 'open';
}

export function useIsGuest(): boolean {
  return isGuest(useMe());
}

/* ── 登录弹窗 / 额度弹窗的全局开关（zustand，只放 UI 状态）──────────────────── */

/** 登录后想做什么：成功后回到原页面原位置并聚焦那个控件——**不自动发起花钱的动作**。 */
export interface AuthIntent {
  type: 'generate' | 'generic';
  symbol?: string;
  /** 登录成功后要聚焦的元素 id（比如生成按钮）。 */
  focusId?: string;
}

export type AuthTab = 'login' | 'register';

interface AuthUiState {
  authOpen: boolean;
  authTab: AuthTab;
  intent: AuthIntent | null;
  /** 弹窗组件是一次性懒加载的：第一次打开后保持挂载（关闭动画与焦点归还要它活着）。 */
  authEverOpened: boolean;
  quotaOpen: boolean;
  quotaDetail: QuotaExceededDetail | null;
  quotaEverOpened: boolean;
  openAuth: (tab?: AuthTab, intent?: AuthIntent | null) => void;
  setAuthTab: (tab: AuthTab) => void;
  closeAuth: () => void;
  openQuota: (detail?: QuotaExceededDetail | null) => void;
  closeQuota: () => void;
}

export const useAuthUi = create<AuthUiState>((set) => ({
  authOpen: false,
  authTab: 'login',
  intent: null,
  authEverOpened: false,
  quotaOpen: false,
  quotaDetail: null,
  quotaEverOpened: false,
  openAuth: (tab = 'login', intent = null) => set({ authOpen: true, authTab: tab, intent, authEverOpened: true }),
  setAuthTab: (tab) => set({ authTab: tab }),
  closeAuth: () => set({ authOpen: false }),
  openQuota: (detail = null) => set({ quotaOpen: true, quotaDetail: detail, quotaEverOpened: true }),
  closeQuota: () => set({ quotaOpen: false }),
}));

/**
 * 需要登录的动作的统一入口：已登录就执行，游客就打开登录弹窗并记住意图。
 * 返回 `true` 表示已放行（并已调用 `run`）。
 */
export function useRequireAuth(intent?: AuthIntent): (run?: () => void) => boolean {
  const me = useMe();
  const openAuth = useAuthUi((state) => state.openAuth);
  const guest = isGuest(me);
  return useCallback(
    (run?: () => void) => {
      if (guest) {
        openAuth('login', intent ?? { type: 'generic' });
        return false;
      }
      run?.();
      return true;
    },
    [guest, intent, openAuth],
  );
}

/**
 * 生成入口的**前置闸**（热键、左栏的 ✨、命令面板、覆盖层的“重试”共用）：
 * 游客 → 打开登录弹窗；额度为 0 → 打开额度对话框；否则放行。
 *
 * 这只是体验层的“不浪费一次必然失败的请求”——服务端的 401 / 429 仍是最终裁决，
 * 且 `useGenerationStream` 对它们同样会打开对应对话框（并发 / 多标签页导致本地状态过时时）。
 */
export function useGenerationGate(): (run: () => void, symbol?: string) => boolean {
  const me = useMe();
  const openAuth = useAuthUi((state) => state.openAuth);
  const openQuota = useAuthUi((state) => state.openQuota);
  return useCallback(
    (run, symbol) => {
      if (isGuest(me)) {
        openAuth('login', { type: 'generate', symbol });
        return false;
      }
      if (bucketView(me.quota?.report)?.exhausted) {
        openQuota(null);
        return false;
      }
      run();
      return true;
    },
    [me, openAuth, openQuota],
  );
}

/* ── 多标签页同步（R-A16）──────────────────────────────────────────────────── */

export const AUTH_CHANNEL = 'aragon-auth';

export type AuthBroadcast = 'signed-in' | 'signed-out';

/** 不支持 `BroadcastChannel` 的浏览器静默退化：靠 401 回落兜底。 */
export function broadcastAuth(message: AuthBroadcast): void {
  if (typeof BroadcastChannel === 'undefined') return;
  try {
    const channel = new BroadcastChannel(AUTH_CHANNEL);
    channel.postMessage({ type: message });
    channel.close();
  } catch {
    /* 沙箱 / 隐私模式：忽略 */
  }
}

export function subscribeAuthBroadcast(handler: (message: AuthBroadcast) => void): () => void {
  if (typeof BroadcastChannel === 'undefined') return () => {};
  try {
    const channel = new BroadcastChannel(AUTH_CHANNEL);
    channel.onmessage = (event: MessageEvent<{ type?: AuthBroadcast }>) => {
      const type = event.data?.type;
      if (type === 'signed-in' || type === 'signed-out') handler(type);
    };
    return () => channel.close();
  } catch {
    return () => {};
  }
}

/* ── 动作 ──────────────────────────────────────────────────────────────────── */

export interface AuthActions {
  /** 登录 / 注册成功后调用：写 `['me']`、广播、让服务端渲染的部分（首屏用户名）刷新。 */
  completeSignIn: (me: Me) => void;
  /** 退出：调用 `POST /auth/logout`（幂等）、清 SW 的用户缓存、回落 guest。 */
  signOut: () => Promise<void>;
}

/** 供 `UserMenu`、命令面板与账号页共用，保证"退出"只有一种做法。 */
export function useAuthActions(onRefresh?: () => void): AuthActions {
  const queryClient = useQueryClient();

  const completeSignIn = useCallback(
    (me: Me) => {
      queryClient.setQueryData(ME_QUERY_KEY, me);
      // 登录前缓存的"我的"数据（自选、额度流水…）不属于新主体。
      void queryClient.invalidateQueries({ queryKey: ['watchlist'] });
      broadcastAuth('signed-in');
      onRefresh?.();
    },
    [onRefresh, queryClient],
  );

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* 退出是幂等的；网络失败时仍然清本地状态，服务端会话由过期兜底 */
    }
    purgeUserCache();
    queryClient.setQueryData(ME_QUERY_KEY, GUEST_ME);
    void queryClient.invalidateQueries({ queryKey: ['watchlist'] });
    broadcastAuth('signed-out');
    onRefresh?.();
  }, [onRefresh, queryClient]);

  return useMemo(() => ({ completeSignIn, signOut }), [completeSignIn, signOut]);
}
