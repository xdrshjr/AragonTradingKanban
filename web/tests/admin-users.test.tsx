import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { UsersTable } from '@/components/admin/UsersTable';
import { userActionsFor, needsTypedConfirm } from '@/components/admin/userActions';
import { ApiError } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import type { AdminUser, AdminUserDetail } from '@/lib/types';

import { renderWithMe, resetAuthUi, userMe } from './authHarness';

function adminUser(overrides: Partial<AdminUser> & Pick<AdminUser, 'id' | 'username'>): AdminUser {
  return {
    displayName: null,
    role: 'user',
    status: 'active',
    createdAt: '2026-11-01T00:00:00Z',
    lastLoginAt: '2026-11-19T00:00:00Z',
    locked: false,
    quota: { report: { limit: 3, usedToday: 1, bonus: 0, remaining: 2 } },
    ...overrides,
  };
}

const ALICE = adminUser({ id: 'a1', username: 'alice', displayName: 'Alice' });
const BOB = adminUser({ id: 'b1', username: 'bob', role: 'admin' });
const ROOT = adminUser({ id: 'r1', username: 'jdqqjr', role: 'root' });
const DAVE = adminUser({ id: 'd1', username: 'dave', locked: true });
const CAROL = adminUser({ id: 'c1', username: 'carol', status: 'disabled' });
// `userMe()` 的当前用户 id 是 `u1`：这一行就是“我自己”。
const ME_ROW = adminUser({ id: 'u1', username: 'alice_self', role: 'user' });

const LIST = [ALICE, BOB, ROOT, DAVE, CAROL];

function detailOf(user: AdminUser): AdminUserDetail {
  return {
    ...user,
    quotaStatus: {
      report: { limit: 3, usedToday: 1, bonus: 2, remaining: 4, resetAt: '2099-01-01T16:00:00Z', pendingRequest: null },
      deepAsk: { limit: 10, usedToday: 0, bonus: 0, remaining: 10, resetAt: '2099-01-01T16:00:00Z', pendingRequest: null },
      tz: 'Asia/Shanghai',
    },
    failedLogins: 0,
    sessions: [
      { id: 's1', current: false, createdAt: '2026-11-19T00:00:00Z', lastSeenAt: '2026-11-20T00:00:00Z', ip: '203.0.113.0', userAgent: 'Chrome / Windows' },
      { id: 's2', current: false, createdAt: '2026-11-10T00:00:00Z', lastSeenAt: '2026-11-18T00:00:00Z', ip: '198.51.100.0', userAgent: 'Safari / iOS' },
    ],
    recentRuns: [{ id: 'run1', symbol: '600519', status: 'succeeded', startedAt: '2026-11-20T01:00:00Z' }],
  };
}

function apiError(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new ApiError(status, { error: { code, message: 'server', requestId: 'req-77', detail } });
}

function mockList(items: AdminUser[] = LIST) {
  return vi.spyOn(adminApi, 'adminUsers').mockResolvedValue({ items, total: items.length, nextCursor: null });
}

/** 点用户名那个按钮（行里的第一个按钮）打开抽屉。 */
async function openDrawer(user: ReturnType<typeof userEvent.setup>, username: string) {
  const row = await screen.findByTestId(`user-row-${username}`);
  await user.click(within(row).getAllByRole('button')[0] as HTMLElement);
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, username: string) {
  await user.click(await screen.findByRole('button', { name: `${username} 的更多操作` }));
  return screen.findByRole('menu');
}

function menuLabels(menu: HTMLElement): string[] {
  return within(menu)
    .getAllByRole('menuitem')
    .map((item) => item.textContent ?? '');
}

beforeEach(() => {
  resetAuthUi();
  vi.spyOn(adminApi, 'adminUser').mockImplementation(async (id: string) => detailOf(LIST.concat(ME_ROW).find((u) => u.id === id) ?? ALICE));
  vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [], total: 0 });
});

afterEach(() => vi.restoreAllMocks());

describe('userActionsFor：§24.3.14 矩阵的体验层裁剪', () => {
  const root = userMe('root');
  const admin = userMe('admin');

  it('root 对 user：全部动作含任命；对 admin：含降级、无任命；对 root：只读', () => {
    const onUser = userActionsFor(root, ALICE);
    expect([...onUser.actions].sort()).toEqual(
      ['adjustQuota', 'delete', 'disable', 'promote', 'resetPassword', 'revokeSessions'].sort(),
    );
    const onAdmin = userActionsFor(root, BOB);
    expect(onAdmin.actions.has('demote')).toBe(true);
    expect(onAdmin.actions.has('promote')).toBe(false);
    expect(userActionsFor(root, ROOT).readOnly).toBe(true);
  });

  it('admin：对 user 没有任命 / 降级；对 admin 与 root 完全只读', () => {
    const onUser = userActionsFor(admin, ALICE);
    expect(onUser.actions.has('promote')).toBe(false);
    expect(onUser.actions.has('demote')).toBe(false);
    expect(onUser.actions.has('delete')).toBe(true);
    expect(userActionsFor(admin, BOB)).toMatchObject({ readOnly: true, reason: 'protected' });
    expect(userActionsFor(admin, ROOT)).toMatchObject({ readOnly: true, reason: 'protected' });
  });

  it('对自己：没有停用 / 删除 / 降级 / 重置 / 下线（self_action_forbidden 的体验层）', () => {
    const set = userActionsFor(admin, ME_ROW);
    expect(set.self).toBe(true);
    for (const action of ['disable', 'delete', 'demote', 'promote', 'resetPassword', 'revokeSessions'] as const) {
      expect(set.actions.has(action)).toBe(false);
    }
  });

  it('被锁定 → 有解锁；已停用 → 是启用而不是停用；已注销 → 只读', () => {
    expect(userActionsFor(admin, DAVE).actions.has('unlock')).toBe(true);
    expect(userActionsFor(admin, ALICE).actions.has('unlock')).toBe(false);
    const disabled = userActionsFor(admin, CAROL);
    expect(disabled.actions.has('enable')).toBe(true);
    expect(disabled.actions.has('disable')).toBe(false);
    expect(userActionsFor(admin, adminUser({ id: 'x', username: 'x', status: 'deleted' })).reason).toBe('deleted');
  });

  it('type-to-confirm 只用于：删除、降级 admin、重置 admin 密码', () => {
    expect(needsTypedConfirm('delete', ALICE)).toBe(true);
    expect(needsTypedConfirm('demote', BOB)).toBe(true);
    expect(needsTypedConfirm('resetPassword', BOB)).toBe(true);
    expect(needsTypedConfirm('resetPassword', ALICE)).toBe(false);
    expect(needsTypedConfirm('disable', ALICE)).toBe(false);
  });
});

describe('UsersTable：行菜单按角色裁剪', () => {
  it('root 视角：user 行有任命；admin 行有降级、没有任命；root 行只有“查看详情”', async () => {
    mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });

    let menu = await openMenu(user, 'alice');
    expect(menuLabels(menu)).toEqual(['查看详情', '停用账号', '重置密码', '全部下线', '任命为管理员', '删除账号']);
    await user.keyboard('{Escape}');

    menu = await openMenu(user, 'bob');
    const bobItems = menuLabels(menu);
    expect(bobItems).toContain('降为普通用户');
    expect(bobItems).not.toContain('任命为管理员');
    await user.keyboard('{Escape}');

    menu = await openMenu(user, 'jdqqjr');
    expect(menuLabels(menu)).toEqual(['查看详情']);
  });

  it('admin 视角：看不到任何任命 / 降级；对 admin 与 root 行只有“查看详情”', async () => {
    mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });

    let menu = await openMenu(user, 'alice');
    const items = menuLabels(menu);
    expect(items).not.toContain('任命为管理员');
    expect(items).not.toContain('降为普通用户');
    expect(items).toContain('删除账号');
    await user.keyboard('{Escape}');

    menu = await openMenu(user, 'bob');
    expect(menuLabels(menu)).toEqual(['查看详情']);
    await user.keyboard('{Escape}');

    menu = await openMenu(user, 'jdqqjr');
    expect(menuLabels(menu)).toEqual(['查看详情']);
  });

  it('被锁定的行有“解锁”，已停用的行是“启用账号”', async () => {
    mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    expect(menuLabels(await openMenu(user, 'dave'))).toContain('解锁');
    await user.keyboard('{Escape}');
    const carol = menuLabels(await openMenu(user, 'carol'));
    expect(carol).toContain('启用账号');
    expect(carol).not.toContain('停用账号');
  });

  it('自己的那一行没有停用 / 删除 / 降级', async () => {
    mockList([ME_ROW, ALICE]);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    const items = menuLabels(await openMenu(user, 'alice_self'));
    expect(items).not.toContain('停用账号');
    expect(items).not.toContain('删除账号');
  });

  it('列表展示：角色徽标、状态文字（不只靠颜色）、今日用量 used/limit', async () => {
    mockList();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    const row = await screen.findByTestId('user-row-alice');
    expect(within(row).getByText('正常')).toBeInTheDocument();
    expect(within(row).getByText('1/3')).toBeInTheDocument();
    expect(within(screen.getByTestId('user-row-dave')).getByText('已锁定')).toBeInTheDocument();
    expect(within(screen.getByTestId('user-row-carol')).getByText('已停用')).toBeInTheDocument();
    expect(within(screen.getByTestId('user-row-bob')).getByText('管理员')).toBeInTheDocument();
  });
});

describe('UsersTable：type-to-confirm 与一次性临时密码', () => {
  it('删除用户：必须输入用户名才可确认；确认后带 confirm=用户名 调用', async () => {
    mockList();
    const del = vi.spyOn(adminApi, 'adminDeleteUser').mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });

    await openMenu(user, 'alice');
    await user.click(screen.getByRole('menuitem', { name: '删除账号' }));

    const dialog = await screen.findByRole('dialog', { name: '删除 alice' });
    const confirm = within(dialog).getByRole('button', { name: '删除账号' });
    expect(confirm).toBeDisabled();

    const input = within(dialog).getByLabelText('输入用户名 alice 以确认');
    await user.type(input, 'ali');
    expect(confirm).toBeDisabled();
    await user.type(input, 'ce');
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await waitFor(() => expect(del).toHaveBeenCalledWith('a1', 'alice'));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '删除 alice' })).not.toBeInTheDocument());
  });

  it('删除确认：大小写不符不放行', async () => {
    mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openMenu(user, 'alice');
    await user.click(screen.getByRole('menuitem', { name: '删除账号' }));
    const dialog = await screen.findByRole('dialog', { name: '删除 alice' });
    await user.type(within(dialog).getByLabelText('输入用户名 alice 以确认'), 'ALICE');
    expect(within(dialog).getByRole('button', { name: '删除账号' })).toBeDisabled();
  });

  it('root 降级 admin：要输入用户名；admin 目标的重置密码同样要输入', async () => {
    mockList();
    const patch = vi.spyOn(adminApi, 'adminPatchUser').mockResolvedValue(BOB);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });

    await openMenu(user, 'bob');
    await user.click(screen.getByRole('menuitem', { name: '降为普通用户' }));
    let dialog = await screen.findByRole('dialog', { name: '将 bob 降为普通用户' });
    const confirm = within(dialog).getByRole('button', { name: '降为普通用户' });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText('输入用户名 bob 以确认'), 'bob');
    await user.click(confirm);
    await waitFor(() => expect(patch).toHaveBeenCalledWith('b1', { role: 'user' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '将 bob 降为普通用户' })).not.toBeInTheDocument());

    await openMenu(user, 'bob');
    await user.click(screen.getByRole('menuitem', { name: '重置密码' }));
    dialog = await screen.findByRole('dialog', { name: '重置 bob 的密码' });
    expect(within(dialog).getByRole('button', { name: '重置密码' })).toBeDisabled();
    expect(within(dialog).getByLabelText('输入用户名 bob 以确认')).toBeInTheDocument();
  });

  it('重置普通用户密码：不要求输入用户名；临时密码只显示一次，带复制', async () => {
    mockList();
    const reset = vi.spyOn(adminApi, 'adminResetPassword').mockResolvedValue({ temporaryPassword: 'Tmp-9xKq27Zd' });
    const user = userEvent.setup();
    // `userEvent.setup()` 会装一个自己的剪贴板替身；在它之后再换成可断言的桩。
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderWithMe(<UsersTable />, { me: userMe('admin') });

    await openMenu(user, 'alice');
    await user.click(screen.getByRole('menuitem', { name: '重置密码' }));
    const confirmDialog = await screen.findByRole('dialog', { name: '重置 alice 的密码' });
    expect(within(confirmDialog).queryByRole('textbox')).not.toBeInTheDocument();
    await user.click(within(confirmDialog).getByRole('button', { name: '重置密码' }));
    await waitFor(() => expect(reset).toHaveBeenCalledWith('a1'));

    const secretDialog = await screen.findByRole('dialog', { name: '临时密码' });
    expect(within(secretDialog).getByTestId('secret-value')).toHaveTextContent('Tmp-9xKq27Zd');
    expect(within(secretDialog).getAllByText(/只显示这一次/).length).toBeGreaterThan(0);

    await user.click(within(secretDialog).getByRole('button', { name: /复制/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Tmp-9xKq27Zd'));
    expect(await within(secretDialog).findByText('已复制')).toBeInTheDocument();

    // 关闭后再也看不到：重新打开列表也没有任何地方保留它。
    await user.click(within(secretDialog).getByRole('button', { name: '我已记录，关闭' }));
    await waitFor(() => expect(screen.queryByTestId('secret-value')).not.toBeInTheDocument());
    expect(document.body.textContent).not.toContain('Tmp-9xKq27Zd');
  });

  it('停用：原因随 PATCH 一起提交；失败时对话框保持打开并给出 requestId', async () => {
    mockList();
    const patch = vi
      .spyOn(adminApi, 'adminPatchUser')
      .mockRejectedValueOnce(apiError(403, 'admin_protected'))
      .mockResolvedValueOnce(ALICE);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });

    await openMenu(user, 'alice');
    await user.click(screen.getByRole('menuitem', { name: '停用账号' }));
    const dialog = await screen.findByRole('dialog', { name: '停用 alice' });
    await user.type(within(dialog).getByLabelText(/停用原因/), '异常调用');
    await user.click(within(dialog).getByRole('button', { name: '停用账号' }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith('a1', { status: 'disabled', disabledReason: '异常调用' }));
    // 失败：对话框还在，可以重试。
    expect(screen.getByRole('dialog', { name: '停用 alice' })).toBeInTheDocument();

    await user.click(within(screen.getByRole('dialog', { name: '停用 alice' })).getByRole('button', { name: '停用账号' }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '停用 alice' })).not.toBeInTheDocument());
  });

  it('解锁：无需确认，直接调用', async () => {
    mockList();
    const unlock = vi.spyOn(adminApi, 'adminUnlockUser').mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openMenu(user, 'dave');
    await user.click(screen.getByRole('menuitem', { name: '解锁' }));
    await waitFor(() => expect(unlock).toHaveBeenCalledWith('d1'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('UsersTable：筛选、分页、空态与错误态', () => {
  it('搜索（防抖后）与角色 / 状态筛选作为查询参数发给服务端', async () => {
    const list = mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    await screen.findByTestId('user-row-alice');

    await user.type(screen.getByLabelText('按用户名前缀搜索'), 'Ali');
    await waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ q: 'ali' })));

    await user.click(screen.getByRole('button', { name: '管理员' }));
    await waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ q: 'ali', role: 'admin' })));

    await user.click(screen.getByRole('button', { name: '已停用' }));
    await waitFor(() =>
      expect(list).toHaveBeenCalledWith(expect.objectContaining({ q: 'ali', role: 'admin', status: 'disabled' })),
    );
  });

  it('非法前缀（含 % 等）不发请求并就地提示', async () => {
    const list = mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    await screen.findByTestId('user-row-alice');
    list.mockClear();
    await user.type(screen.getByLabelText('按用户名前缀搜索'), 'a%b');
    expect(await screen.findByText(/只含小写字母、数字与下划线/)).toBeInTheDocument();
    expect(list).not.toHaveBeenCalledWith(expect.objectContaining({ q: 'a%b' }));
  });

  it('游标分页：加载更多用上一页的 nextCursor 作为 before', async () => {
    const list = vi
      .spyOn(adminApi, 'adminUsers')
      .mockResolvedValueOnce({ items: [ALICE], total: 2, nextCursor: 'cur-1' })
      .mockResolvedValueOnce({ items: [BOB], total: 2, nextCursor: null });
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    await screen.findByTestId('user-row-alice');
    await user.click(screen.getByRole('button', { name: '加载更多' }));
    await screen.findByTestId('user-row-bob');
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ before: 'cur-1' }));
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
  });

  it('空结果：一句话 + 清除筛选；加载中有骨架屏', async () => {
    vi.spyOn(adminApi, 'adminUsers').mockResolvedValue({ items: [], total: 0, nextCursor: null });
    renderWithMe(<UsersTable />, { me: userMe('root') });
    expect(screen.getByRole('status', { name: '加载中…' })).toBeInTheDocument();
    expect(await screen.findByText('没有符合条件的用户')).toBeInTheDocument();
  });

  it('错误态：显示人话 + 请求编号 + 重试', async () => {
    const list = vi
      .spyOn(adminApi, 'adminUsers')
      .mockRejectedValueOnce(apiError(500, 'internal_error'))
      .mockResolvedValueOnce({ items: [ALICE], total: 1, nextCursor: null });
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('请求编号 req-77');
    await user.click(within(alert).getByRole('button', { name: '重试' }));
    expect(await screen.findByTestId('user-row-alice')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('只有持 admin.users.manage 才有“创建账号”', async () => {
    mockList();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    expect(await screen.findByRole('button', { name: '创建账号' })).toBeInTheDocument();
  });
});

describe('UserDrawer', () => {
  it('点行打开抽屉：资料、额度、活动会话、近期生成、危险区', async () => {
    mockList();
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    await openDrawer(user, 'alice');

    const drawer = await screen.findByRole('dialog', { name: '用户详情' });
    expect(within(drawer).getByText('资料')).toBeInTheDocument();
    expect(await within(drawer).findByText('活动会话')).toBeInTheDocument();
    expect(within(drawer).getByText('Chrome / Windows')).toBeInTheDocument();
    expect(within(drawer).getByText('近 20 次生成')).toBeInTheDocument();
    expect(within(drawer).getByText('600519')).toBeInTheDocument();
    expect(within(drawer).getByText('危险操作')).toBeInTheDocument();
    // 额度摘要：今日 1/3、日额度 3、赠送 2
    const summary = within(drawer).getByTestId('drawer-quota-summary');
    expect(summary).toHaveTextContent('1 / 3');
    expect(summary).toHaveTextContent('2');
  });

  it('逐条吊销会话', async () => {
    mockList();
    const revoke = vi.spyOn(adminApi, 'adminRevokeSession').mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openDrawer(user, 'alice');
    const drawer = await screen.findByRole('dialog', { name: '用户详情' });
    await user.click(await within(drawer).findByRole('button', { name: '吊销会话 Safari / iOS' }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('a1', 's2'));
  });

  it('发放赠送额度：校验 -50..50 非零整数，成功后调用接口', async () => {
    mockList();
    const adjust = vi.spyOn(adminApi, 'adminAdjustQuota').mockResolvedValue(detailOf(ALICE).quotaStatus!);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openDrawer(user, 'alice');
    const drawer = await screen.findByRole('dialog', { name: '用户详情' });
    const input = await within(drawer).findByLabelText(/赠送次数/);

    await user.type(input, '0');
    await user.click(within(drawer).getByRole('button', { name: '发放' }));
    expect(within(drawer).getByText(/非零整数/)).toBeInTheDocument();
    expect(adjust).not.toHaveBeenCalled();

    await user.clear(input);
    await user.type(input, '5');
    await user.click(within(drawer).getByRole('button', { name: '发放' }));
    await waitFor(() => expect(adjust).toHaveBeenCalledWith('a1', { resource: 'report', grant: 5 }));
  });

  it('日额度覆盖：不限 → -1；跟随默认 → null', async () => {
    mockList();
    const adjust = vi.spyOn(adminApi, 'adminAdjustQuota').mockResolvedValue(detailOf(ALICE).quotaStatus!);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openDrawer(user, 'alice');
    const drawer = await screen.findByRole('dialog', { name: '用户详情' });
    await within(drawer).findByText('活动会话');

    await user.click(within(drawer).getByRole('radio', { name: '不限' }));
    await user.click(within(drawer).getByRole('button', { name: '保存日额度' }));
    await waitFor(() => expect(adjust).toHaveBeenCalledWith('a1', { resource: 'report', dailyLimit: -1 }));

    await user.click(within(drawer).getByRole('radio', { name: '跟随默认' }));
    await user.click(within(drawer).getByRole('button', { name: '保存日额度' }));
    await waitFor(() => expect(adjust).toHaveBeenCalledWith('a1', { resource: 'report', dailyLimit: null }));
  });

  it('危险区按角色裁剪：admin 看不到任命 / 降级；root 看到任命；root 行只读并有说明', async () => {
    mockList();
    const user = userEvent.setup();
    const view = renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openDrawer(user, 'alice');
    let drawer = await screen.findByRole('dialog', { name: '用户详情' });
    await within(drawer).findByText('活动会话');
    expect(within(drawer).queryByRole('button', { name: '任命为管理员' })).not.toBeInTheDocument();
    expect(within(drawer).queryByRole('button', { name: '降为普通用户' })).not.toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: '删除账号' })).toBeInTheDocument();
    view.unmount();

    const user2 = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root') });
    await openDrawer(user2, 'alice');
    drawer = await screen.findByRole('dialog', { name: '用户详情' });
    await within(drawer).findByText('活动会话');
    expect(within(drawer).getByRole('button', { name: '任命为管理员' })).toBeInTheDocument();
    await user2.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '用户详情' })).not.toBeInTheDocument());

    await openDrawer(user2, 'jdqqjr');
    drawer = await screen.findByRole('dialog', { name: '用户详情' });
    expect(await within(drawer).findByText(/只读/)).toBeInTheDocument();
  });

  it('从抽屉的危险区删除：同样要求输入用户名', async () => {
    mockList();
    const del = vi.spyOn(adminApi, 'adminDeleteUser').mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await openDrawer(user, 'alice');
    const drawer = await screen.findByRole('dialog', { name: '用户详情' });
    await user.click(await within(drawer).findByRole('button', { name: '删除账号' }));

    const confirmDialog = await screen.findByRole('dialog', { name: '删除 alice' });
    await user.type(within(confirmDialog).getByLabelText('输入用户名 alice 以确认'), 'alice');
    await user.click(within(confirmDialog).getByRole('button', { name: '删除账号' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('a1', 'alice'));
  });
});
