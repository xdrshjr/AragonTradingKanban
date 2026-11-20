import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';

import { TooltipProvider } from '@/components/ui/Tooltip';
import { GUEST_ME, MeContext, useAuthUi } from '@/lib/auth';
import { permissionsForRole } from '@/lib/permissions';
import type { Me, QuotaBucket, Role } from '@/lib/types';

/** 认证相关组件测试的共用夹具与渲染器（不是测试文件：文件名不含 `.test.`）。 */

export function bucket(overrides: Partial<QuotaBucket> = {}): QuotaBucket {
  return {
    limit: 3,
    usedToday: 0,
    bonus: 0,
    remaining: 3,
    resetAt: '2099-01-01T16:00:00Z',
    pendingRequest: null,
    ...overrides,
  };
}

export function userMe(
  role: Role = 'user',
  quota: Partial<QuotaBucket> | null = {},
  extra: Partial<Me['user'] & object> = {},
): Me {
  const unlimited = role !== 'user';
  const report = unlimited ? bucket({ limit: null, remaining: null, usedToday: 0 }) : bucket(quota ?? {});
  return {
    authMode: 'enforced',
    authenticated: true,
    user: {
      id: 'u1',
      username: role === 'root' ? 'jdqqjr' : 'alice',
      displayName: null,
      role,
      status: 'active',
      createdAt: '2026-01-01T00:00:00Z',
      lastLoginAt: '2026-11-19T00:00:00Z',
      mustChangePassword: false,
      passwordIsDefault: false,
      ...extra,
    },
    permissions: [...permissionsForRole(role)],
    quota: quota === null && !unlimited ? null : { report, deepAsk: bucket({ limit: 10, remaining: 10 }), tz: 'Asia/Shanghai' },
  };
}

export const GUEST: Me = GUEST_ME;

export interface RenderOptions {
  me?: Me;
  client?: QueryClient;
}

export function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

/** 渲染到一个固定身份的 `MeContext` 里（含 QueryClient 与 TooltipProvider）。 */
export function renderWithMe(ui: ReactElement, { me = GUEST, client = makeClient() }: RenderOptions = {}) {
  const setMe = (next: Me) => client.setQueryData(['me'], next);
  const view = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MeContext.Provider value={{ me, setMe, refreshMe: async () => {} }}>{ui}</MeContext.Provider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return {
    ...view,
    client,
    rerenderWithMe: (nextUi: ReactElement, nextMe: Me) =>
      view.rerender(
        <QueryClientProvider client={client}>
          <TooltipProvider>
            <MeContext.Provider value={{ me: nextMe, setMe, refreshMe: async () => {} }}>{nextUi}</MeContext.Provider>
          </TooltipProvider>
        </QueryClientProvider>,
      ),
  };
}

/** 重置全局的登录 / 额度弹窗开关（zustand 是模块级单例）。 */
export function resetAuthUi() {
  useAuthUi.setState({
    authOpen: false,
    authTab: 'login',
    intent: null,
    authEverOpened: false,
    quotaOpen: false,
    quotaDetail: null,
    quotaEverOpened: false,
  });
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

export function errorResponse(status: number, code: string, detail: Record<string, unknown> = {}): Response {
  return jsonResponse({ error: { code, message: 'server', requestId: 'req-test', detail } }, status);
}
