import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGenerationStream } from '@/components/generation/useGenerationStream';
import { Providers } from '@/components/shell/Providers';
import { api, ApiError } from '@/lib/api';
import { emitAuthEvent } from '@/lib/authEvents';
import { GUEST_ME, ME_QUERY_KEY, useAuthUi, useMe, useRequireAuth } from '@/lib/auth';
import type { Me } from '@/lib/types';

import { bucket, resetAuthUi, userMe } from './authHarness';

const router = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/' }));

function apiError(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });
}

function Who() {
  const me = useMe();
  return <p data-testid="who">{me.authenticated ? (me.user?.username ?? 'open') : 'guest'}</p>;
}

beforeEach(() => {
  resetAuthUi();
  router.refresh.mockReset();
  router.replace.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('Providers / MeBridge（§24.3.15a）', () => {
  it('initialMe 作为 initialData 注入：首帧即是正确身份，不发 /auth/me', () => {
    const me = vi.spyOn(api, 'me');
    render(
      <Providers initialMe={userMe('user')}>
        <Who />
      </Providers>,
    );
    expect(screen.getByTestId('who')).toHaveTextContent('alice');
    expect(me).not.toHaveBeenCalled();
  });

  it('服务端取不到 me（initialMe=null）：先按游客渲染，客户端随后自己取一次', async () => {
    const me = vi.spyOn(api, 'me').mockResolvedValue(userMe('user'));
    render(
      <Providers initialMe={null}>
        <Who />
      </Providers>,
    );
    expect(screen.getByTestId('who')).toHaveTextContent('guest');
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('alice'));
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('会话失效事件：回落 guest、通知 SW 清缓存、打开登录弹窗（不整页刷新）', async () => {
    const postMessage = vi.fn();
    Object.defineProperty(navigator, 'serviceWorker', { value: { controller: { postMessage } }, configurable: true });
    render(
      <Providers initialMe={userMe('user')}>
        <Who />
      </Providers>,
    );
    act(() => emitAuthEvent('unauthorized', { reason: 'expired' }));

    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('guest'));
    expect(postMessage).toHaveBeenCalledWith({ type: 'purge-user-cache' });
    expect(useAuthUi.getState().authOpen).toBe(true);
    expect(router.refresh).not.toHaveBeenCalled();
    Object.defineProperty(navigator, 'serviceWorker', { value: undefined, configurable: true });
  });

  it('本来就是游客时的 401 事件被忽略（那是调用方自己的“请先登录”分支）', () => {
    render(
      <Providers initialMe={GUEST_ME}>
        <Who />
      </Providers>,
    );
    act(() => emitAuthEvent('unauthorized', { reason: 'expired' }));
    expect(useAuthUi.getState().authOpen).toBe(false);
  });

  it('403 password_change_required：全局送去强制改密页；已在该页时不重复跳', () => {
    render(
      <Providers initialMe={userMe('root', null, { mustChangePassword: true })}>
        <Who />
      </Providers>,
    );
    act(() => emitAuthEvent('password-change-required'));
    expect(router.replace).toHaveBeenCalledWith('/account/password?forced=1');

    router.replace.mockReset();
    window.history.pushState({}, '', '/account/password?forced=1');
    act(() => emitAuthEvent('password-change-required'));
    expect(router.replace).not.toHaveBeenCalled();
    window.history.pushState({}, '', '/');
  });

  it('其它标签页的 BroadcastChannel 消息：重取 me 并刷新服务端渲染（R-A16）', async () => {
    class FakeChannel {
      static instances: FakeChannel[] = [];
      onmessage?: (event: { data: unknown }) => void;
      constructor() {
        FakeChannel.instances.push(this);
      }
      postMessage() {}
      close() {}
    }
    vi.stubGlobal('BroadcastChannel', FakeChannel);
    const me = vi.spyOn(api, 'me').mockResolvedValue(GUEST_ME);

    render(
      <Providers initialMe={userMe('user')}>
        <Who />
      </Providers>,
    );
    const channel = FakeChannel.instances.find((instance) => instance.onmessage);
    expect(channel).toBeDefined();
    act(() => channel!.onmessage?.({ data: { type: 'signed-out' } }));

    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('guest'));
    expect(me).toHaveBeenCalled();
    expect(router.refresh).toHaveBeenCalled();
  });
});

describe('AuthDialog（懒加载弹窗）', () => {
  it('打开 → 登录成功：写入 me、关闭弹窗、焦点交还给意图里的控件，且不自动生成', async () => {
    const meAfter = userMe('user');
    vi.spyOn(api, 'authConfig').mockResolvedValue({
      authMode: 'enforced',
      registrationMode: 'open',
      registrationOpen: true,
      passwordPolicy: { min: 8, max: 128, minClasses: 2 },
      usernamePolicy: { min: 3, max: 20, pattern: '^[A-Za-z0-9_]+$' },
      captchaKind: 'captcha',
      forceRootPasswordChange: true,
    });
    const login = vi.spyOn(api, 'login').mockResolvedValue(meAfter);
    const createReport = vi.spyOn(api, 'createReport');

    function Page() {
      const me = useMe();
      const require = useRequireAuth({ type: 'generate', focusId: 'gen-btn' });
      return (
        <div>
          <p data-testid="who">{me.authenticated ? me.user?.username : 'guest'}</p>
          <button id="gen-btn" type="button" onClick={() => require(() => void api.createReport('x'))}>
            生成
          </button>
        </div>
      );
    }
    const view = render(
      <Providers initialMe={GUEST_ME}>
        <Page />
      </Providers>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '生成' }));
    // 游客：没有发请求，弹窗出现（懒加载）
    expect(createReport).not.toHaveBeenCalled();
    // 弹窗是 next/dynamic 懒加载的：冷模块图下首次 import 可能超过默认的 1 s。
    const dialog = await screen.findByRole('dialog', {}, { timeout: 10_000 });
    expect(dialog).toHaveAccessibleName('登录后即可生成投研报告');

    await user.type(await screen.findByLabelText('用户名', {}, { timeout: 10_000 }), 'alice');
    await user.type(screen.getByLabelText('密码'), 'secret-pass{Enter}');

    await waitFor(() => expect(login).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('who')).toHaveTextContent('alice'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.getElementById('gen-btn')).toHaveFocus());
    // 花钱的动作必须由人再点一次：登录成功后没有自动生成
    expect(createReport).not.toHaveBeenCalled();
    expect(router.refresh).toHaveBeenCalled();
    view.unmount();
  }, 30_000);

  it('注册标签：取 /auth/config 渲染规则；登录 ↔ 注册可切换', async () => {
    const cfg = vi.spyOn(api, 'authConfig').mockResolvedValue({
      authMode: 'enforced',
      registrationMode: 'closed',
      registrationOpen: false,
      passwordPolicy: { min: 8, max: 128, minClasses: 2 },
      usernamePolicy: { min: 3, max: 20, pattern: '^[A-Za-z0-9_]+$' },
      captchaKind: 'captcha',
      forceRootPasswordChange: true,
    });
    render(<Providers initialMe={GUEST_ME}><Who /></Providers>);
    act(() => useAuthUi.getState().openAuth('register'));
    expect(await screen.findByTestId('register-closed', {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(cfg).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('tab', { name: '登录' }));
    expect(await screen.findByTestId('login-form')).toBeInTheDocument();
  }, 30_000);
});

describe('QuotaExceededDialog', { timeout: 30_000 }, () => {
  function open(detail: Parameters<ReturnType<typeof useAuthUi.getState>['openQuota']>[0], me: Me) {
    render(<Providers initialMe={me}><Who /></Providers>);
    act(() => useAuthUi.getState().openQuota(detail));
  }

  const detail = {
    resource: 'report' as const,
    scope: 'user' as const,
    limit: 3,
    usedToday: 3,
    bonus: 0,
    resetAt: '2099-01-01T16:00:00Z',
    canRequest: true,
  };

  it('用完：说清已用 / 重置时间；理由必填；提交带 resource+amount+reason，随后显示“处理中”', async () => {
    const create = vi.spyOn(api, 'createQuotaRequest').mockResolvedValue({
      id: 'q1', resource: 'report', amount: 3, reason: 'r', status: 'pending', createdAt: '2026-11-20T00:00:00Z',
    });
    vi.spyOn(api, 'me').mockResolvedValue(
      userMe('user', { usedToday: 3, remaining: 0, pendingRequest: { id: 'q1', createdAt: '2026-11-20T00:00:00Z' } }),
    );
    open(detail, userMe('user', { usedToday: 3, remaining: 0 }));

    const dialog = await screen.findByTestId('quota-dialog', {}, { timeout: 10_000 });
    expect(dialog).toHaveTextContent('今日生成额度已用完');
    expect(dialog).toHaveTextContent('已用 3/3');
    expect(dialog).toHaveTextContent('重置（');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '提交申请' }));
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByText('请填写申请理由。')).toBeInTheDocument();

    await user.type(screen.getByLabelText('申请理由'), '需要为新标的生成研报');
    await user.click(screen.getByRole('button', { name: '5 次' }));
    await user.click(screen.getByRole('button', { name: '提交申请' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ resource: 'report', amount: 5, reason: '需要为新标的生成研报' }));
    expect(await screen.findByText(/待处理的额度申请/, {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.queryByLabelText('申请理由')).toBeNull();
  });

  it('已有 pending 申请：直接显示处理中，不再给表单', async () => {
    open(detail, userMe('user', { usedToday: 3, remaining: 0, pendingRequest: { id: 'q1', createdAt: '2026-11-20T00:00:00Z' } }));
    expect(await screen.findByText(/待处理的额度申请/, {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(screen.queryByLabelText('申请理由')).toBeNull();
  });

  it('全站名额已满（scope=site）：说明原因，不显示申请表单（批准也无济于事）', async () => {
    open({ ...detail, scope: 'site', canRequest: false }, userMe('user', { usedToday: 0, remaining: 3 }));
    const dialog = await screen.findByTestId('quota-dialog', {}, { timeout: 10_000 });
    expect(dialog).toHaveTextContent('今日全站生成名额已用完');
    expect(dialog).toHaveTextContent('申请额外额度对此不起作用');
    expect(screen.queryByLabelText('申请理由')).toBeNull();
    expect(screen.queryByRole('button', { name: '提交申请' })).toBeNull();
  });

  it('提交失败显示可读错误（统一话术），不关闭对话框', async () => {
    vi.spyOn(api, 'createQuotaRequest').mockRejectedValue(apiError(429, 'rate_limited', { retryAfterMs: 30_000 }));
    open(detail, userMe('user', { usedToday: 3, remaining: 0 }));
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('申请理由', {}, { timeout: 10_000 }), 'x');
    await user.click(screen.getByRole('button', { name: '提交申请' }));
    expect(await screen.findByText('操作过于频繁。')).toBeInTheDocument();
    expect(screen.getByTestId('quota-dialog')).toBeInTheDocument();
  });
});

describe('useGenerationStream：准入级拒绝不是 run 级失败（§24.3.15c）', () => {
  function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(ME_QUERY_KEY, userMe('user'));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const onError = vi.fn();
    const hook = renderHook(() => useGenerationStream({ onError }), {
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    });
    return { hook, onError, invalidate };
  }

  it('429 quota_exceeded：打开额度对话框、状态回到 idle、不走 onError / 失败卡片、重取 me', async () => {
    vi.spyOn(api, 'createReport').mockRejectedValue(
      apiError(429, 'quota_exceeded', { resource: 'report', scope: 'user', limit: 3, usedToday: 3, bonus: 0, canRequest: true }),
    );
    const { hook, onError, invalidate } = setup();
    await act(async () => {
      await hook.result.current.start('CN:600519.SH');
    });
    expect(useAuthUi.getState().quotaOpen).toBe(true);
    expect(useAuthUi.getState().quotaDetail).toMatchObject({ scope: 'user', limit: 3, usedToday: 3 });
    expect(hook.result.current.state.phase).toBe('idle');
    expect(hook.result.current.state.error).toBeNull();
    expect(onError).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ME_QUERY_KEY });
  });

  it('401 unauthorized：打开登录弹窗（意图=generate），同样不是生成失败', async () => {
    vi.spyOn(api, 'createReport').mockRejectedValue(apiError(401, 'unauthorized', { reason: 'no_session' }));
    const { hook, onError } = setup();
    await act(async () => {
      await hook.result.current.start('CN:600519.SH');
    });
    expect(useAuthUi.getState()).toMatchObject({ authOpen: true });
    expect(useAuthUi.getState().intent).toMatchObject({ type: 'generate' });
    expect(hook.result.current.state.phase).toBe('idle');
    expect(onError).not.toHaveBeenCalled();
  });

  it('其它错误码照旧：走 exception + onError', async () => {
    vi.spyOn(api, 'createReport').mockRejectedValue(apiError(500, 'internal'));
    const { hook, onError } = setup();
    await act(async () => {
      await hook.result.current.start('CN:600519.SH');
    });
    expect(hook.result.current.state.phase).toBe('error');
    expect(onError).toHaveBeenCalled();
  });
});

describe('额度桶工具（防回归）', () => {
  it('bucket() 夹具与 QuotaBucket 形状一致', () => {
    expect(Object.keys(bucket()).sort()).toEqual(['bonus', 'limit', 'pendingRequest', 'remaining', 'resetAt', 'usedToday']);
  });
});
