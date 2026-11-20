import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuditTable } from '@/components/admin/AuditTable';
import { CreateUserDialog } from '@/components/admin/CreateUserDialog';
import { OverviewDashboard } from '@/components/admin/OverviewDashboard';
import { buildOverviewCards } from '@/components/admin/OverviewCards';
import { SettingsForm } from '@/components/admin/SettingsForm';
import { UsersTable } from '@/components/admin/UsersTable';
import { notify } from '@/components/ui/Toast';
import { ApiError, api } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import type { AdminOverview, AdminSettings, AdminUser } from '@/lib/types';

import { renderWithMe, resetAuthUi, userMe } from './authHarness';

/**
 * AC-215（locked chip / 概览卡链接）、AC-234（批量工具条 / 导出按钮 / 安全事件卡）、
 * AC-243 的前端部分（authRecoveryEnabled 开关）、D-10 文案。
 */

vi.mock('next/navigation', () => ({
  usePathname: () => '/admin/users',
  useRouter: () => ({ refresh() {}, push() {}, replace() {} }),
}));

// 概览的折线图是 canvas：替身。
vi.mock('@/components/admin/TrendChart', () => ({ default: () => <div data-testid="trend-chart-stub" /> }));

const apiError = (status: number, code: string, detail: Record<string, unknown> = {}) =>
  new ApiError(status, { error: { code, message: 'server', requestId: 'req-9', detail } });

function adminUser(i: number, overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: `id${i}`,
    username: `user${String(i).padStart(3, '0')}`,
    displayName: null,
    role: 'user',
    status: 'active',
    createdAt: '2026-11-01T00:00:00Z',
    lastLoginAt: null,
    locked: false,
    quota: { report: { limit: 3, usedToday: 0, bonus: 0, remaining: 3 } },
    ...overrides,
  };
}

const MANY = Array.from({ length: 120 }, (_, i) => adminUser(i));
const FEW = [adminUser(1), adminUser(2), adminUser(3), adminUser(900, { id: 'adm', username: 'bob_admin', role: 'admin' })];

function mockUsers(items: AdminUser[], total = items.length) {
  return vi.spyOn(adminApi, 'adminUsers').mockResolvedValue({ items, total, nextCursor: null });
}

beforeEach(() => {
  resetAuthUi();
  vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(notify, 'success').mockImplementation(() => 'ok');
  vi.spyOn(notify, 'info').mockImplementation(() => 'ok');
  vi.spyOn(notify, 'error').mockImplementation(() => 'ok');
});
afterEach(() => vi.restoreAllMocks());

describe('UsersTable · 批量选择与工具条（AC-234）', () => {
  it('勾选后出现吸顶工具条：选中数、批量赠送额度、清除选择；清除后工具条消失', async () => {
    mockUsers(FEW);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root', null) });
    await screen.findByTestId('user-row-user001');
    expect(screen.queryByTestId('bulk-toolbar')).toBeNull();

    await user.click(screen.getByLabelText('选择 user001'));
    await user.click(screen.getByLabelText('选择 user002'));
    const bar = screen.getByTestId('bulk-toolbar');
    expect(bar).toHaveTextContent('已选 2 项');
    expect(within(bar).getByRole('button', { name: '批量赠送额度' })).toBeInTheDocument();
    await user.click(within(bar).getByRole('button', { name: '清除选择' }));
    expect(screen.queryByTestId('bulk-toolbar')).toBeNull();
  });

  it('表头复选框：选择本页全部 / 取消；半选态用 indeterminate', async () => {
    mockUsers(FEW);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('root', null) });
    await screen.findByTestId('user-row-user001');
    const all = screen.getByLabelText('选择本页全部用户') as HTMLInputElement;
    await user.click(screen.getByLabelText('选择 user001'));
    expect(all.indeterminate).toBe(true);
    await user.click(all);
    expect(screen.getByTestId('bulk-toolbar')).toHaveTextContent('已选 4 项'); // root 可对 admin 发额度
    await user.click(all);
    expect(screen.queryByTestId('bulk-toolbar')).toBeNull();
  });

  it('admin 视角：admin 目标行的复选框禁用（服务端对越权目标整体 403，这里不让选）', async () => {
    mockUsers(FEW);
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await screen.findByTestId('user-row-user001');
    expect(screen.getByLabelText('选择 bob_admin')).toBeDisabled();
    expect(screen.getByLabelText('选择 user001')).toBeEnabled();
  });

  it('选满 100：禁用继续勾选并提示上限', async () => {
    mockUsers(MANY);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await screen.findByTestId('user-row-user000');
    await user.click(screen.getByLabelText('选择本页全部用户'));
    const bar = screen.getByTestId('bulk-toolbar');
    expect(bar).toHaveTextContent('已选 100 项');
    expect(bar).toHaveTextContent('最多一次选择 100 项');
    expect(screen.getByLabelText('选择 user110')).toBeDisabled();
    // 已选中的仍可取消勾选
    expect(screen.getByLabelText('选择 user000')).toBeEnabled();
    await user.click(screen.getByLabelText('选择 user000'));
    expect(screen.getByTestId('bulk-toolbar')).toHaveTextContent('已选 99 项');
    expect(screen.getByLabelText('选择 user110')).toBeEnabled();
  }, 30_000);

  it('没有 admin.quota.manage 的角色（普通用户视角）：没有复选列', async () => {
    mockUsers(FEW);
    renderWithMe(<UsersTable />, { me: userMe('user') });
    await screen.findByTestId('user-row-user001');
    expect(screen.queryByLabelText('选择本页全部用户')).toBeNull();
  });

  it('批量发放：范围 −50..50 且非 0；成功后以 {userIds, grant, note} 调用并清空选择', async () => {
    mockUsers(FEW);
    const bulk = vi.spyOn(adminApi, 'adminBulkQuota').mockResolvedValue({ applied: 2, skipped: 0, batchId: 'abc123def456' });
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await screen.findByTestId('user-row-user001');
    await user.click(screen.getByLabelText('选择 user001'));
    await user.click(screen.getByLabelText('选择 user002'));
    await user.click(screen.getByRole('button', { name: '批量赠送额度' }));

    const dialog = within(await screen.findByRole('dialog'));
    const amount = dialog.getByLabelText('每人赠送次数');
    for (const bad of ['0', '51', '-51', '1.5', '']) {
      await user.clear(amount);
      if (bad) await user.type(amount, bad);
      expect(dialog.getByRole('button', { name: /确认(发放|收回)/ })).toBeDisabled();
    }
    await user.clear(amount);
    await user.type(amount, '50');
    await user.type(dialog.getByLabelText('备注（可选）'), '活动补偿');
    await user.click(dialog.getByRole('button', { name: '确认发放' }));

    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(1));
    expect(bulk).toHaveBeenCalledWith({ userIds: ['id1', 'id2'], grant: 50, note: '活动补偿' });
    await waitFor(() => expect(screen.queryByTestId('bulk-toolbar')).toBeNull());
    expect(notify.success).toHaveBeenCalledWith('已为 2 个账号调整额度');
  });

  it('收回（负数）：按钮文案变为“确认收回”；skipped > 0 时结果里说明被跳过的人数', async () => {
    mockUsers(FEW);
    const bulk = vi.spyOn(adminApi, 'adminBulkQuota').mockResolvedValue({ applied: 1, skipped: 1, batchId: 'b' });
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await screen.findByTestId('user-row-user001');
    await user.click(screen.getByLabelText('选择 user001'));
    await user.click(screen.getByLabelText('选择 user002'));
    await user.click(screen.getByRole('button', { name: '批量赠送额度' }));
    const dialog = within(await screen.findByRole('dialog'));
    const amount = dialog.getByLabelText('每人赠送次数');
    await user.clear(amount);
    await user.type(amount, '-5');
    await user.click(dialog.getByRole('button', { name: '确认收回' }));
    await waitFor(() => expect(bulk).toHaveBeenCalledWith({ userIds: ['id1', 'id2'], grant: -5 }));
    await waitFor(() =>
      expect(notify.success).toHaveBeenCalledWith('已为 1 个账号调整额度，1 个账号没有可收回的赠送余额，已跳过'),
    );
  });

  it('单飞：pending 时再点不再发第二个请求；失败（404/403）时对话框保持打开并显示 requestId', async () => {
    mockUsers(FEW);
    let release!: (value: { applied: number; skipped: number; batchId: string }) => void;
    const bulk = vi
      .spyOn(adminApi, 'adminBulkQuota')
      .mockImplementationOnce(() => new Promise((resolve) => (release = resolve)))
      .mockRejectedValueOnce(apiError(403, 'forbidden'));
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await screen.findByTestId('user-row-user001');
    await user.click(screen.getByLabelText('选择 user001'));
    await user.click(screen.getByRole('button', { name: '批量赠送额度' }));
    const dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: '确认发放' }));
    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(1));
    const busy = dialog.getByRole('button', { name: '确认发放' });
    expect(busy).toHaveAttribute('aria-busy', 'true');
    await user.click(busy);
    expect(bulk).toHaveBeenCalledTimes(1);
    release({ applied: 1, skipped: 0, batchId: 'x' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // 第二次：失败
    await user.click(screen.getByLabelText('选择 user002'));
    await user.click(screen.getByRole('button', { name: '批量赠送额度' }));
    const again = within(await screen.findByRole('dialog'));
    await user.click(again.getByRole('button', { name: '确认发放' }));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('筛选变化时清空选择（不对看不见的人批量操作）', async () => {
    mockUsers(FEW);
    const user = userEvent.setup();
    renderWithMe(<UsersTable />, { me: userMe('admin') });
    await screen.findByTestId('user-row-user001');
    await user.click(screen.getByLabelText('选择 user001'));
    expect(screen.getByTestId('bulk-toolbar')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '管理员' }));
    await waitFor(() => expect(screen.queryByTestId('bulk-toolbar')).toBeNull());
  });
});

describe('UsersTable · locked 筛选 chip（AC-215）', () => {
  it('initialLocked：请求带 locked=true，显示可清除的 chip；清除后再次请求不带 locked', async () => {
    const list = mockUsers(FEW);
    const user = userEvent.setup();
    renderWithMe(<UsersTable initialLocked />, { me: userMe('admin') });
    const chip = await screen.findByTestId('locked-chip');
    expect(chip).toHaveTextContent('仅看已锁定账号');
    expect(list.mock.calls[0]![0]).toMatchObject({ locked: true });

    await user.click(within(chip).getByRole('button', { name: /清除/ }));
    expect(screen.queryByTestId('locked-chip')).toBeNull();
    await waitFor(() => expect(list.mock.calls.at(-1)![0]).not.toHaveProperty('locked', true));
  });

  it('adminApi.adminUsers 把 locked 编码成 locked=1', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200, headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    try {
      const { createAdminApi } = await import('@/lib/api.admin');
      await createAdminApi({}).adminUsers({ locked: true, role: 'user' });
      expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toMatch(/\/admin\/users\?role=user&locked=1&limit=50$/);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('导出按钮（AC-234 / §25.3.5a）', () => {
  it('用户页：admin 有“导出 CSV”直链（带当前筛选），普通用户视角不渲染', async () => {
    mockUsers(FEW);
    const user = userEvent.setup();
    const view = renderWithMe(<UsersTable initialLocked />, { me: userMe('admin') });
    const link = await screen.findByRole('link', { name: /导出 CSV/ });
    expect(link).toHaveAttribute('download');
    expect(link.getAttribute('href')).toMatch(/\/api\/v1\/admin\/users\/export\.csv\?locked=1$/);
    link.addEventListener('click', (event) => event.preventDefault()); // jsdom 不实现下载导航
    await user.click(link);
    expect(notify.info).toHaveBeenCalledWith('已开始导出，文件由浏览器下载');
    view.unmount();

    renderWithMe(<UsersTable />, { me: userMe('user') });
    await screen.findByTestId('user-row-user001');
    expect(screen.queryByRole('link', { name: /导出 CSV/ })).toBeNull();
  });

  it('审计页：导出直链带动作 / 操作者等过滤；无 admin.audit.read 不渲染', async () => {
    vi.spyOn(adminApi, 'adminAudit').mockResolvedValue({ items: [], nextCursor: null });
    const view = renderWithMe(<AuditTable initialAction="auth.login.failure" />, { me: userMe('admin') });
    const link = await screen.findByRole('link', { name: /导出 CSV/ });
    expect(link.getAttribute('href')).toMatch(/\/api\/v1\/admin\/audit\/export\.csv\?action=auth\.login\.failure$/);
    view.unmount();
    renderWithMe(<AuditTable />, { me: userMe('user') });
    expect(screen.queryByRole('link', { name: /导出 CSV/ })).toBeNull();
  });

  it('adminApi 的导出 URL 与对应列表接口使用同一套过滤参数名', () => {
    expect(adminApi.adminUsersExportUrl({ q: 'ab', role: 'user', status: 'active', locked: true })).toMatch(
      /\/admin\/users\/export\.csv\?q=ab&role=user&status=active&locked=1$/,
    );
    expect(adminApi.adminAuditExportUrl({ action: 'admin.export', actor: 'root', from: '2026-11-01T00:00:00.000Z' })).toMatch(
      /\/admin\/audit\/export\.csv\?action=admin\.export&actor=root&from=2026-11-01T00%3A00%3A00\.000Z$/,
    );
  });
});

describe('审计页：由 ?action= 还原筛选（AC-234）', () => {
  it('合法动作：下拉框预选并以它请求；未知动作被忽略', async () => {
    const audit = vi.spyOn(adminApi, 'adminAudit').mockResolvedValue({ items: [], nextCursor: null });
    const view = renderWithMe(<AuditTable initialAction="auth.recovery.failure" />, { me: userMe('admin') });
    await waitFor(() => expect(audit).toHaveBeenCalled());
    expect(audit.mock.calls[0]![0]).toMatchObject({ action: 'auth.recovery.failure' });
    expect(screen.getByLabelText('动作')).toHaveValue('auth.recovery.failure');
    view.unmount();

    audit.mockClear();
    renderWithMe(<AuditTable initialAction="totally.bogus" />, { me: userMe('admin') });
    await waitFor(() => expect(audit).toHaveBeenCalled());
    expect(audit.mock.calls[0]![0]?.action).toBeUndefined();
  });
});

function overview(overrides: Partial<AdminOverview> = {}): AdminOverview {
  return {
    users: { total: 10, byRole: { user: 9, root: 1 }, active7d: 5, disabled: 0, locked: 2 },
    signups: [],
    runs: { today: 0, succeeded: 0, failed: 0, successRate: null },
    runsDaily: [],
    quota: { consumedToday: 0, refundedToday: 0 },
    topUsers7d: [],
    pendingQuotaRequests: 0,
    recentAudit: [],
    security24h: {
      loginFailures: 58,
      loginFailureBursts: 3,
      loginFailuresCapped: false,
      lockouts: 1,
      recoveryFailures: 0,
      recoveries: 2,
      selfDeletes: 0,
      exports: 4,
    },
    ...overrides,
  };
}

describe('概览：安全事件（24 小时）与“被锁定账号”卡（AC-215/234）', () => {
  it('被锁定账号卡链接到 /admin/users?locked=1；为 0 时不是链接', () => {
    const locked = buildOverviewCards(overview()).find((card) => card.key === 'locked')!;
    expect(locked.href).toBe('/admin/users?locked=1');
    const none = buildOverviewCards(overview({ users: { total: 1, byRole: {}, active7d: 0, disabled: 0, locked: 0 } })).find((card) => card.key === 'locked')!;
    expect(none.href).toBeUndefined();
  });

  it('安全事件一排：失败类显示“尝试次数”，非零数字是指向 /admin/audit?action= 的链接，零值不是链接', async () => {
    vi.spyOn(adminApi, 'adminOverview').mockResolvedValue(overview());
    renderWithMe(<OverviewDashboard />, { me: userMe('admin') });
    const row = await screen.findByTestId('security-events');
    const failures = within(row).getByTestId('security-loginFailures');
    expect(failures).toHaveTextContent('58');
    expect(failures).toHaveTextContent('共 3 组');
    expect(within(failures).getByRole('link')).toHaveAttribute('href', '/admin/audit?action=auth.login.failure');
    expect(within(within(row).getByTestId('security-lockouts')).getByRole('link')).toHaveAttribute('href', '/admin/audit?action=auth.login.lockout');
    expect(within(within(row).getByTestId('security-recoveries')).getByRole('link')).toHaveAttribute('href', '/admin/audit?action=auth.password.recover');
    expect(within(within(row).getByTestId('security-exports')).getByRole('link')).toHaveAttribute('href', '/admin/audit?action=admin.export');
    // 零值：没有链接
    expect(within(within(row).getByTestId('security-recoveryFailures')).queryByRole('link')).toBeNull();
    expect(within(within(row).getByTestId('security-selfDeletes')).queryByRole('link')).toBeNull();
  });

  it('达到 5000 组上限：数字后带 +，提示“5000+ 组”', async () => {
    vi.spyOn(adminApi, 'adminOverview').mockResolvedValue(
      overview({
        security24h: {
          loginFailures: 20000,
          loginFailureBursts: 5000,
          loginFailuresCapped: true,
          lockouts: 0,
          recoveryFailures: 0,
          recoveries: 0,
          selfDeletes: 0,
          exports: 0,
        },
      }),
    );
    renderWithMe(<OverviewDashboard />, { me: userMe('admin') });
    const tile = await screen.findByTestId('security-loginFailures');
    expect(tile).toHaveTextContent('20000+');
    expect(tile).toHaveTextContent('5000+ 组');
  });

  it('全为零：一句“没有安全事件”；后端未返回 security24h（旧版本）时整行不渲染', async () => {
    vi.spyOn(adminApi, 'adminOverview').mockResolvedValue(
      overview({
        security24h: { loginFailures: 0, loginFailureBursts: 0, loginFailuresCapped: false, lockouts: 0, recoveryFailures: 0, recoveries: 0, selfDeletes: 0, exports: 0 },
      }),
    );
    const view = renderWithMe(<OverviewDashboard />, { me: userMe('admin') });
    expect(await screen.findByText('过去 24 小时没有安全事件。')).toBeInTheDocument();
    view.unmount();

    vi.spyOn(adminApi, 'adminOverview').mockResolvedValue(overview({ security24h: undefined }));
    renderWithMe(<OverviewDashboard />, { me: userMe('admin') });
    await screen.findByTestId('overview-card-total');
    expect(screen.queryByTestId('security-events')).toBeNull();
  });
});

describe('设置：authRecoveryEnabled 开关（AC-243 前端）', () => {
  const SETTINGS: AdminSettings = {
    registrationMode: 'open',
    authRecoveryEnabled: true,
    defaultDailyReport: 3,
    defaultDailyDeepAsk: 10,
    signupBonus: 0,
    siteDailyCap: 0,
  };

  beforeEach(() => {
    vi.spyOn(api, 'costSummary').mockResolvedValue({} as Awaited<ReturnType<typeof api.costSummary>>);
    vi.spyOn(adminApi, 'adminFeatured').mockResolvedValue({ items: [] });
  });

  it('只发布尔值：关掉 → PATCH {authRecoveryEnabled:false}；无改动时保存键禁用', async () => {
    vi.spyOn(adminApi, 'adminSettings').mockResolvedValue(SETTINGS);
    const patch = vi.spyOn(adminApi, 'adminPatchSettings').mockResolvedValue({ ...SETTINGS, authRecoveryEnabled: false });
    const user = userEvent.setup();
    renderWithMe(<SettingsForm />, { me: userMe('admin') });
    const card = (await screen.findByRole('heading', { name: '自助找回密码' })).closest('section') as HTMLElement;
    const toggle = within(card).getByRole('switch', { name: /允许自助找回密码/ });
    expect(toggle).toBeChecked();
    const save = within(card).getByRole('button', { name: '保存' });
    expect(save).toBeDisabled();

    await user.click(toggle);
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ authRecoveryEnabled: false }));
    expect(typeof patch.mock.calls[0]![0].authRecoveryEnabled).toBe('boolean');
  });

  it('后端未返回该键（旧版本）：整区不渲染', async () => {
    const { authRecoveryEnabled: _omit, ...legacy } = SETTINGS;
    void _omit;
    vi.spyOn(adminApi, 'adminSettings').mockResolvedValue(legacy as AdminSettings);
    renderWithMe(<SettingsForm />, { me: userMe('admin') });
    await screen.findByRole('radiogroup', { name: '注册模式' });
    expect(screen.queryByRole('heading', { name: '自助找回密码' })).toBeNull();
  });

  it('审计动作词表含 5 个新动作（31 → 36）的中文标签', async () => {
    const { copyAdmin } = await import('@/lib/copy.admin');
    expect(Object.keys(copyAdmin.audit.actions)).toHaveLength(36);
  });
});

describe('CreateUserDialog 文案（D-10 / A-18）', () => {
  it('说明“该用户首次登录必须修改密码”（无论是否手填初始密码）', async () => {
    renderWithMe(<CreateUserDialog open onOpenChange={() => {}} />, { me: userMe('admin') });
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('该用户首次登录必须修改密码');
  });
});
