import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountClient } from '@/components/auth/AccountClient';
import { api, ApiError } from '@/lib/api';
import { clearRecoveryUnsaved, markRecoveryUnsaved } from '@/lib/recoveryKey';
import type { Me } from '@/lib/types';

import { renderWithMe, resetAuthUi, userMe } from './authHarness';

/** AC-224（账号页恢复码卡 / 提示条）、AC-225–228 的前端部分（危险区）。 */

const router = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/account' }));

const apiError = (status: number, code: string, detail: Record<string, unknown> = {}) =>
  new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });

const CONFIG_ON = { recoveryEnabled: true } as Awaited<ReturnType<typeof api.authConfig>>;

beforeEach(() => {
  resetAuthUi();
  clearRecoveryUnsaved();
  vi.spyOn(api, 'me').mockResolvedValue(userMe('user'));
  vi.spyOn(api, 'myQuotaRequests').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'sessions').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'quotaLedger').mockResolvedValue({ items: [], nextCursor: null });
  vi.spyOn(api, 'authConfig').mockResolvedValue(CONFIG_ON);
  vi.spyOn(api, 'logout').mockResolvedValue(undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
  router.replace.mockClear();
});

/** 账号页安全标签自带“当前密码”（改密表单）：对话框内的字段要限定在 dialog 里查。 */
const dlg = () => within(screen.getByRole('dialog'));

async function openSecurity(me: Me) {
  const user = userEvent.setup();
  renderWithMe(<AccountClient />, { me });
  await user.click(screen.getByRole('tab', { name: '安全' }));
  return user;
}

describe('账号页 · 恢复码卡', () => {
  it('已设置：显示状态与“生成新的恢复码”；未设置：显示“尚未设置”与“生成恢复码”', async () => {
    await openSecurity(userMe('user', {}, { hasRecoveryKey: true }));
    expect(await screen.findByTestId('recovery-status')).toHaveTextContent('已设置');
    expect(screen.getByRole('button', { name: '生成新的恢复码' })).toBeInTheDocument();
  });

  it('未设置（迭代 1 期间注册的老用户）：状态为“尚未设置”', async () => {
    await openSecurity(userMe('user', {}, { hasRecoveryKey: false }));
    expect(await screen.findByTestId('recovery-status')).toHaveTextContent('尚未设置');
    expect(screen.getByRole('button', { name: '生成恢复码' })).toBeInTheDocument();
  });

  it('recoveryEnabled=false：整卡隐藏', async () => {
    vi.spyOn(api, 'authConfig').mockResolvedValue({ recoveryEnabled: false } as Awaited<ReturnType<typeof api.authConfig>>);
    await openSecurity(userMe('user', {}, { hasRecoveryKey: true }));
    await waitFor(() => expect(api.authConfig).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByTestId('recovery-status')).toBeNull());
  });

  it('root：不渲染恢复码卡与危险区（root 只走离线 CLI）', async () => {
    await openSecurity(userMe('root', null));
    await waitFor(() => expect(api.authConfig).toHaveBeenCalled());
    expect(screen.queryByTestId('recovery-status')).toBeNull();
    expect(screen.queryByTestId('danger-zone')).toBeNull();
  });

  it('轮换：输入密码 → 展示新码 → 勾选并继续后状态变为“已设置 · 日期”', async () => {
    const rotate = vi.spyOn(api, 'rotateRecoveryKey').mockResolvedValue({ recoveryKey: 'AAAAA-BBBBB-CCCCC-DDDDD', setAt: '2026-11-20T08:00:00Z' });
    const user = await openSecurity(userMe('user', {}, { hasRecoveryKey: false }));
    await user.click(await screen.findByRole('button', { name: '生成恢复码' }));
    await user.type(dlg().getByLabelText('当前密码'), 'Str0ng-pass-1');
    await user.click(screen.getByRole('button', { name: '验证并生成' }));
    await waitFor(() => expect(rotate).toHaveBeenCalledWith('Str0ng-pass-1'));
    expect(await screen.findByTestId('recovery-key-dialog')).toHaveTextContent('AAAAA-BBBBB-CCCCC-DDDDD');
    await user.click(screen.getByLabelText(/我已妥善保存/));
    await user.click(screen.getByRole('button', { name: '继续' }));
    await waitFor(() => expect(screen.getByTestId('recovery-status')).toHaveTextContent(/已设置 · /));
  });
});

describe('账号页 · “尚未保存恢复码”提示条', () => {
  it('注册时选了跳过：账号页常驻提示条，可关闭，关闭后不再出现', async () => {
    const me = userMe('user');
    markRecoveryUnsaved(me.user!.id);
    const user = userEvent.setup();
    const { unmount } = renderWithMe(<AccountClient />, { me });
    const banner = await screen.findByTestId('recovery-unsaved-banner');
    expect(banner).toHaveTextContent('尚未保存恢复码');
    await user.click(within(banner).getByRole('button', { name: '知道了' }));
    expect(screen.queryByTestId('recovery-unsaved-banner')).toBeNull();
    unmount();

    renderWithMe(<AccountClient />, { me });
    expect(screen.queryByTestId('recovery-unsaved-banner')).toBeNull();
  });

  it('没有“跳过”标记：不显示', () => {
    renderWithMe(<AccountClient />, { me: userMe('user') });
    expect(screen.queryByTestId('recovery-unsaved-banner')).toBeNull();
  });
});

describe('账号页 · 危险区（自助注销，AC-225/228 前端）', () => {
  async function openDialog() {
    const user = await openSecurity(userMe('user'));
    const zone = await screen.findByTestId('danger-zone');
    await user.click(within(zone).getByRole('button', { name: '注销账号…' }));
    return user;
  }

  it('“确认注销”必须同时输入密码与完全一致的用户名才启用（区分大小写）', async () => {
    const user = await openDialog();
    const submit = screen.getByRole('button', { name: '确认注销' });
    expect(submit).toBeDisabled();

    await user.type(dlg().getByLabelText('当前密码'), 'Str0ng-pass-1');
    expect(submit).toBeDisabled(); // 只有密码

    await user.type(dlg().getByLabelText('键入用户名确认'), 'Alice');
    expect(submit).toBeDisabled(); // 大小写不符

    await user.clear(dlg().getByLabelText('键入用户名确认'));
    await user.type(dlg().getByLabelText('键入用户名确认'), 'alice');
    expect(submit).toBeEnabled();

    await user.clear(dlg().getByLabelText('当前密码'));
    expect(submit).toBeDisabled(); // 只有用户名
  });

  it('成功：以 {password, confirm} 调用；回落 guest 并回首页', async () => {
    const del = vi.spyOn(api, 'deleteAccount').mockResolvedValue(undefined);
    const user = await openDialog();
    await user.type(dlg().getByLabelText('当前密码'), 'Str0ng-pass-1');
    await user.type(dlg().getByLabelText('键入用户名确认'), 'alice');
    await user.click(screen.getByRole('button', { name: '确认注销' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith({ password: 'Str0ng-pass-1', confirm: 'alice' }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/'));
    expect(api.logout).toHaveBeenCalled();
  });

  it('密码错（401）：错误落在密码字段、清空、聚焦；对话框保持打开', async () => {
    vi.spyOn(api, 'deleteAccount').mockRejectedValue(apiError(401, 'invalid_credentials'));
    const user = await openDialog();
    await user.type(dlg().getByLabelText('当前密码'), 'wrong-password');
    await user.type(dlg().getByLabelText('键入用户名确认'), 'alice');
    await user.click(screen.getByRole('button', { name: '确认注销' }));
    expect(await screen.findByText('用户名或密码不正确。')).toBeInTheDocument();
    expect(dlg().getByLabelText('当前密码')).toHaveValue('');
    expect(dlg().getByLabelText('当前密码')).toHaveFocus();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it('root_protected（理论上界面已不渲染）：服务端拒绝时给出一句话，不跳转', async () => {
    vi.spyOn(api, 'deleteAccount').mockRejectedValue(apiError(403, 'root_protected', { action: 'self_delete' }));
    const user = await openDialog();
    await user.type(dlg().getByLabelText('当前密码'), 'Str0ng-pass-1');
    await user.type(dlg().getByLabelText('键入用户名确认'), 'alice');
    await user.click(screen.getByRole('button', { name: '确认注销' }));
    expect(await screen.findByText('根管理员账号不可被修改。')).toBeInTheDocument();
    expect(router.replace).not.toHaveBeenCalled();
  });

  it('单飞：pending 时再点不再发第二个请求；按钮 aria-busy', async () => {
    let release!: () => void;
    const del = vi.spyOn(api, 'deleteAccount').mockImplementation(() => new Promise<void>((resolve) => (release = resolve)));
    const user = await openDialog();
    await user.type(dlg().getByLabelText('当前密码'), 'Str0ng-pass-1');
    await user.type(dlg().getByLabelText('键入用户名确认'), 'alice');
    await user.click(screen.getByRole('button', { name: '确认注销' }));
    await waitFor(() => expect(del).toHaveBeenCalledTimes(1));
    const busy = screen.getByRole('button', { name: '确认注销' });
    expect(busy).toHaveAttribute('aria-busy', 'true');
    await user.click(busy);
    await user.type(dlg().getByLabelText('键入用户名确认'), '{Enter}');
    expect(del).toHaveBeenCalledTimes(1);
    release();
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/'));
  });

  it('危险区是红色描边的独立区块，不在主按钮色里', async () => {
    await openSecurity(userMe('admin'));
    const zone = await screen.findByTestId('danger-zone');
    expect(zone.className).toContain('border-[var(--danger)]');
    expect(within(zone).getByRole('heading', { name: '注销账号' })).toBeInTheDocument();
  });
});
