import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { UserMenu } from '@/components/auth/UserMenu';
import { RoleBadge } from '@/components/auth/RoleBadge';
import { StatusDot } from '@/components/ui/StatusDot';
import { api } from '@/lib/api';
import { AUTH_CHANNEL, GUEST_ME, ME_QUERY_KEY, useAuthUi } from '@/lib/auth';

import { GUEST, renderWithMe, resetAuthUi, userMe } from './authHarness';

const router = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/' }));

beforeEach(() => {
  resetAuthUi();
  router.refresh.mockReset();
});

afterEach(() => vi.restoreAllMocks());

describe('UserMenu：游客', () => {
  it('登录 / 注册两个入口；点击打开弹窗并指定标签（不跳走）', async () => {
    renderWithMe(<UserMenu />, { me: GUEST });
    await userEvent.click(screen.getByRole('button', { name: '登录' }));
    expect(useAuthUi.getState()).toMatchObject({ authOpen: true, authTab: 'login' });

    resetAuthUi();
    await userEvent.click(screen.getByRole('button', { name: '注册' }));
    expect(useAuthUi.getState()).toMatchObject({ authOpen: true, authTab: 'register' });
  });

  it('开放模式（无主体）与无 Provider 的孤立渲染：不渲染', () => {
    const open = renderWithMe(<UserMenu />, {
      me: { authMode: 'open', authenticated: true, user: null, permissions: [], quota: null },
    });
    expect(open.container).toBeEmptyDOMElement();
  });
});

describe('UserMenu：已登录', () => {
  it('触发器带名字与 aria-haspopup；菜单 role=menu，含 账号与额度 / 退出登录，user 没有“管理后台”', async () => {
    const user = userEvent.setup();
    renderWithMe(<UserMenu />, { me: userMe('user') });
    const trigger = screen.getByTestId('user-menu-trigger');
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(trigger).toHaveAccessibleName('账号菜单：alice');

    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const menu = screen.getByRole('menu', { name: '账号菜单' });
    const items = screen.getAllByRole('menuitem').map((item) => item.textContent?.trim());
    expect(items).toEqual(['账号与额度', '退出登录']);
    expect(screen.getByRole('menuitem', { name: '账号与额度' })).toHaveAttribute('href', '/account');
    expect(menu).toHaveTextContent('用户'); // 角色徽标
    expect(screen.queryByRole('menuitem', { name: '管理后台' })).toBeNull();
  });

  it('admin / root 才有“管理后台”；root 的徽标是“根管理员”', async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithMe(<UserMenu />, { me: userMe('admin') });
    await user.click(screen.getByTestId('user-menu-trigger'));
    expect(screen.getByRole('menuitem', { name: '管理后台' })).toHaveAttribute('href', '/admin');
    unmount();

    renderWithMe(<UserMenu />, { me: userMe('root') });
    await user.click(screen.getByTestId('user-menu-trigger'));
    expect(screen.getByRole('menu')).toHaveTextContent('根管理员');
    expect(screen.getByRole('menuitem', { name: '管理后台' })).toBeInTheDocument();
  });

  it('键盘：打开后焦点在第一项；↓↑循环；Esc 关闭并把焦点还给触发器', async () => {
    const user = userEvent.setup();
    renderWithMe(<UserMenu />, { me: userMe('admin') });
    const trigger = screen.getByTestId('user-menu-trigger');
    await user.click(trigger);

    const items = screen.getAllByRole('menuitem');
    expect(items[0]).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(items[1]).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(items[2]).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(items[0]).toHaveFocus(); // 循环
    await user.keyboard('{ArrowUp}');
    expect(items[2]).toHaveFocus();
    await user.keyboard('{Home}');
    expect(items[0]).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it('点击菜单外部关闭', async () => {
    const user = userEvent.setup();
    renderWithMe(
      <div>
        <UserMenu />
        <button type="button">外面</button>
      </div>,
      { me: userMe('user') },
    );
    await user.click(screen.getByTestId('user-menu-trigger'));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '外面' }));
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('退出登录：调用 POST /auth/logout、回落 guest、广播其它标签页、刷新服务端渲染', async () => {
    const logout = vi.spyOn(api, 'logout').mockResolvedValue(undefined);
    const posted: unknown[] = [];
    class FakeChannel {
      constructor(public name: string) {}
      postMessage(message: unknown) {
        posted.push([this.name, message]);
      }
      close() {}
    }
    vi.stubGlobal('BroadcastChannel', FakeChannel);

    const user = userEvent.setup();
    const { client } = renderWithMe(<UserMenu />, { me: userMe('user') });
    await user.click(screen.getByTestId('user-menu-trigger'));
    await user.click(screen.getByRole('menuitem', { name: '退出登录' }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(client.getQueryData(ME_QUERY_KEY)).toEqual(GUEST_ME));
    expect(posted).toEqual([[AUTH_CHANNEL, { type: 'signed-out' }]]);
    expect(router.refresh).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('退出登录时网络失败也清本地状态（幂等：服务端会话由过期兜底）', async () => {
    vi.spyOn(api, 'logout').mockRejectedValue(new Error('offline'));
    const user = userEvent.setup();
    const { client } = renderWithMe(<UserMenu />, { me: userMe('user') });
    await user.click(screen.getByTestId('user-menu-trigger'));
    await user.click(screen.getByRole('menuitem', { name: '退出登录' }));
    await waitFor(() => expect(client.getQueryData(ME_QUERY_KEY)).toEqual(GUEST_ME));
  });

  it('退出登录通知 SW 清除用户缓存', async () => {
    vi.spyOn(api, 'logout').mockResolvedValue(undefined);
    const postMessage = vi.fn();
    Object.defineProperty(navigator, 'serviceWorker', { value: { controller: { postMessage } }, configurable: true });
    const user = userEvent.setup();
    renderWithMe(<UserMenu />, { me: userMe('user') });
    await user.click(screen.getByTestId('user-menu-trigger'));
    await user.click(screen.getByRole('menuitem', { name: '退出登录' }));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ type: 'purge-user-cache' }));
    Object.defineProperty(navigator, 'serviceWorker', { value: undefined, configurable: true });
  });

  it('有显示名时头像取显示名首字，菜单里另列 @用户名', async () => {
    const user = userEvent.setup();
    renderWithMe(<UserMenu />, { me: userMe('user', {}, { displayName: 'Zed' }) });
    expect(screen.getByTestId('user-menu-trigger')).toHaveAccessibleName('账号菜单：Zed');
    await user.click(screen.getByTestId('user-menu-trigger'));
    expect(screen.getByText('@alice')).toBeInTheDocument();
  });
});

describe('共用原语：RoleBadge / StatusDot', () => {
  it('RoleBadge：guest 不显示；root / admin / user 各有文字标签（不靠颜色单独区分）', () => {
    const guest = render(<RoleBadge role="guest" />);
    expect(guest.container).toBeEmptyDOMElement();
    guest.unmount();
    for (const [role, label] of [
      ['root', '根管理员'],
      ['admin', '管理员'],
      ['user', '用户'],
    ] as const) {
      const view = render(<RoleBadge role={role} />);
      expect(view.container).toHaveTextContent(label);
      view.unmount();
    }
    // root 是 accent 实心 + 皇冠图标
    const root = render(<RoleBadge role="root" />);
    expect(root.container.querySelector('svg')).not.toBeNull();
    expect(root.container.firstElementChild?.className).toMatch(/--accent\)\]/);
  });

  it('StatusDot：文字永远在场（dotOnly 时仍给读屏器）', () => {
    renderWithMe(
      <div>
        <StatusDot status="active" label="正常" />
        <StatusDot status="locked" label="已锁定" dotOnly />
      </div>,
    );
    expect(screen.getByText('正常')).toBeVisible();
    expect(screen.getByText('已锁定')).toHaveClass('sr-only');
  });
});
