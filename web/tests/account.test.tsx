import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountClient } from '@/components/auth/AccountClient';
import { ChangePasswordForm } from '@/components/auth/ChangePasswordForm';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { api, ApiError } from '@/lib/api';

import { renderWithMe, resetAuthUi, userMe } from './authHarness';

const router = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/account' }));

function apiError(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new ApiError(status, { error: { code, message: 'server', requestId: 'req-1', detail } });
}

beforeEach(() => {
  resetAuthUi();
  vi.spyOn(api, 'me').mockResolvedValue(userMe('user'));
  vi.spyOn(api, 'myQuotaRequests').mockResolvedValue({
    items: [
      { id: 'q1', resource: 'report', amount: 3, reason: '新标的', status: 'rejected', note: '本周额度已发放完毕', createdAt: '2026-11-18T00:00:00Z' },
    ],
  });
  vi.spyOn(api, 'sessions').mockResolvedValue({
    items: [
      { id: 's1', current: true, createdAt: '2026-11-19T00:00:00Z', lastSeenAt: '2026-11-20T00:00:00Z', ip: '203.0.113.0', userAgent: 'Chrome / Windows' },
      { id: 's2', current: false, createdAt: '2026-11-10T00:00:00Z', lastSeenAt: '2026-11-18T00:00:00Z', ip: '198.51.100.0', userAgent: 'Safari / iOS' },
    ],
  });
  vi.spyOn(api, 'quotaLedger').mockResolvedValue({
    items: [
      { id: 'l1', at: '2026-11-20T01:00:00Z', resource: 'report', delta: -1, reason: 'run', source: 'daily', refId: 'r', note: null },
      { id: 'l2', at: '2026-11-20T01:05:00Z', resource: 'report', delta: 1, reason: 'run_refund', source: 'daily', refId: 'r', note: null },
    ],
    nextCursor: null,
  });
});

afterEach(() => vi.restoreAllMocks());

describe('AccountClient', () => {
  it('概览：资料、额度环（日额度 / 赠送 / 重置）、额度申请的处理结果与备注', async () => {
    renderWithMe(<AccountClient />, { me: userMe('user', { usedToday: 1, bonus: 2, remaining: 4 }) });
    expect(screen.getByRole('heading', { name: '账号' })).toBeInTheDocument();
    expect(screen.getAllByText('alice').length).toBeGreaterThan(0);
    expect(screen.getAllByRole('img', { name: /日额度剩余 2，共 3，另有赠送 2/ }).length).toBeGreaterThan(0);
    expect(await screen.findByText('已拒绝')).toBeInTheDocument();
    expect(screen.getByText('本周额度已发放完毕')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '申请额外额度' })).toBeEnabled();
  });

  it('已有 pending 申请：按钮显示“额度申请处理中”并禁用', async () => {
    renderWithMe(<AccountClient />, {
      me: userMe('user', { usedToday: 3, remaining: 0, pendingRequest: { id: 'q9', createdAt: '2026-11-20T00:00:00Z' } }),
    });
    expect(screen.getByRole('button', { name: '额度申请处理中' })).toBeDisabled();
  });

  it('仍在用初始密码（passwordIsDefault）：顶部有不可关闭的警示条，点“去修改”切到安全标签', async () => {
    const user = userEvent.setup();
    renderWithMe(<AccountClient />, { me: userMe('root', null, { passwordIsDefault: true }) });
    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent('仍在使用初始密码');
    await user.click(within(banner).getByRole('button', { name: '去修改' }));
    expect(await screen.findByLabelText('当前密码')).toBeInTheDocument();
  });

  it('安全：登录设备列表（当前设备不可退出）；“退出其它设备”要二次确认', async () => {
    const revokeOthers = vi.spyOn(api, 'revokeOtherSessions').mockResolvedValue({ revoked: 1 });
    const user = userEvent.setup();
    renderWithMe(<AccountClient />, { me: userMe('user') });
    await user.click(screen.getByRole('tab', { name: '安全' }));

    expect(await screen.findByText('Chrome / Windows')).toBeInTheDocument();
    expect(screen.getByText('当前设备')).toBeInTheDocument();
    // 只有非当前设备有“退出”
    expect(screen.getAllByRole('button', { name: '退出' })).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: '退出其它设备' }));
    const dialog = await screen.findByRole('dialog');
    expect(revokeOthers).not.toHaveBeenCalled(); // 确认之前不调用
    await user.click(within(dialog).getByRole('button', { name: '退出其它设备' }));
    await waitFor(() => expect(revokeOthers).toHaveBeenCalledTimes(1));
  });

  it('额度流水：事件中文化、变化带符号、来源', async () => {
    const user = userEvent.setup();
    renderWithMe(<AccountClient />, { me: userMe('user') });
    await user.click(screen.getByRole('tab', { name: '额度流水' }));
    expect(await screen.findByText('研报 · 生成研报')).toBeInTheDocument();
    expect(screen.getByText('研报 · 生成未完成，已退还')).toBeInTheDocument();
    expect(screen.getByText('+1')).toBeInTheDocument();
    expect(screen.getByText('-1')).toBeInTheDocument();
  });

  it('显示名保存：调用 PATCH /auth/profile 并写回 me', async () => {
    const updated = userMe('user', {}, { displayName: 'Zed' });
    const update = vi.spyOn(api, 'updateProfile').mockResolvedValue(updated);
    const user = userEvent.setup();
    const { client } = renderWithMe(<AccountClient />, { me: userMe('user') });
    await user.type(screen.getByLabelText('显示名'), 'Zed');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(update).toHaveBeenCalledWith({ displayName: 'Zed' }));
    await waitFor(() => expect(client.getQueryData(['me'])).toEqual(updated));
  });
});

describe('ChangePasswordForm', () => {
  it('新旧密码相同 / 两次不一致 / 强度不足：都不发请求，并聚焦第一个非法字段', async () => {
    const change = vi.spyOn(api, 'changePassword');
    const user = userEvent.setup();
    renderWithMe(<ChangePasswordForm username="alice" onDone={() => {}} />);

    await user.type(screen.getByLabelText('当前密码'), 'Old-pass-1');
    await user.type(screen.getByLabelText('新密码'), 'Old-pass-1');
    await user.type(screen.getByLabelText('确认新密码'), 'Old-pass-1');
    await user.click(screen.getByRole('button', { name: '修改密码' }));
    expect(screen.getByText('新密码不能与当前密码相同。')).toBeInTheDocument();
    expect(screen.getByLabelText('新密码')).toHaveFocus();

    await user.clear(screen.getByLabelText('新密码'));
    await user.type(screen.getByLabelText('新密码'), 'New-pass-22');
    await user.click(screen.getByRole('button', { name: '修改密码' }));
    expect(screen.getByText('两次输入的新密码不一致。')).toBeInTheDocument();
    expect(screen.getByLabelText('确认新密码')).toHaveFocus();
    expect(change).not.toHaveBeenCalled();
  });

  it('成功：请求体 currentPassword/newPassword，onDone 被调用，表单清空', async () => {
    const change = vi.spyOn(api, 'changePassword').mockResolvedValue(undefined);
    const onDone = vi.fn();
    const user = userEvent.setup();
    renderWithMe(<ChangePasswordForm username="alice" onDone={onDone} />);
    await user.type(screen.getByLabelText('当前密码'), 'Old-pass-1');
    await user.type(screen.getByLabelText('新密码'), 'New-pass-22');
    await user.type(screen.getByLabelText('确认新密码'), 'New-pass-22{Enter}');
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(change).toHaveBeenCalledWith({ currentPassword: 'Old-pass-1', newPassword: 'New-pass-22' });
    expect(screen.getByLabelText('当前密码')).toHaveValue('');
  });

  it('旧密码错：错误挂在“当前密码”并聚焦；弱口令：服务端规则点亮并聚焦“新密码”', async () => {
    const change = vi.spyOn(api, 'changePassword');
    change.mockRejectedValueOnce(apiError(401, 'invalid_credentials'));
    const user = userEvent.setup();
    renderWithMe(<ChangePasswordForm username="alice" onDone={() => {}} />);
    await user.type(screen.getByLabelText('当前密码'), 'wrong');
    await user.type(screen.getByLabelText('新密码'), 'New-pass-22');
    await user.type(screen.getByLabelText('确认新密码'), 'New-pass-22');
    await user.click(screen.getByRole('button', { name: '修改密码' }));
    expect(await screen.findByText('用户名或密码不正确。')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('当前密码')).toHaveFocus());

    change.mockRejectedValueOnce(apiError(422, 'weak_password', { rules: ['common_password'] }));
    await user.click(screen.getByRole('button', { name: '修改密码' }));
    expect(await screen.findByText('密码强度不足。')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('新密码')).toHaveFocus());
  });
});

describe('ConfirmDialog（后台共用原语）', () => {
  it('type-to-confirm：输入与目标完全一致前确认键禁用；确认只调用一次', async () => {
    // 请求在途（未完成）时连点：只能调用一次。
    const onConfirm = vi.fn(() => new Promise<void>(() => {}));
    const user = userEvent.setup();
    renderWithMe(
      <ConfirmDialog
        open
        onOpenChange={() => {}}
        title="删除用户"
        description="此操作会匿名化该账号。"
        confirmLabel="删除"
        requireText="bob"
        onConfirm={onConfirm}
      />,
    );
    const confirm = screen.getByRole('button', { name: '删除' });
    expect(confirm).toBeDisabled();
    // 打开时焦点在输入框（type-to-confirm）
    expect(screen.getByLabelText('请输入 bob 以确认')).toHaveFocus();
    await user.type(screen.getByLabelText('请输入 bob 以确认'), 'Bob');
    expect(confirm).toBeDisabled();
    await user.clear(screen.getByLabelText('请输入 bob 以确认'));
    await user.type(screen.getByLabelText('请输入 bob 以确认'), 'bob');
    expect(confirm).toBeEnabled();
    await user.dblClick(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('不需要输入文字时，默认焦点在“取消”（默认安全）', () => {
    renderWithMe(
      <ConfirmDialog open onOpenChange={() => {}} title="踢下线" description="将吊销该用户全部会话。" confirmLabel="确认" onConfirm={() => {}} />,
    );
    expect(screen.getByRole('button', { name: '取消' })).toHaveFocus();
  });
});
