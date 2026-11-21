import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AdminShell } from '@/components/admin/AdminShell';
import { AuditTable } from '@/components/admin/AuditTable';
import { InvitesPanel, inviteState } from '@/components/admin/InvitesPanel';
import { OverviewDashboard } from '@/components/admin/OverviewDashboard';
import { QuotaRequestsPanel } from '@/components/admin/QuotaRequestsPanel';
import { SettingsForm } from '@/components/admin/SettingsForm';
import AdminLayout from '@/app/admin/layout';
import { api, ApiError } from '@/lib/api';
import { adminApi, createAdminApi } from '@/lib/api.admin';
import { copyAdmin } from '@/lib/copy.admin';
import { permissionsForRole } from '@/lib/permissions';
import type {
  AdminOverview,
  AdminQuotaRequest,
  AdminSettings,
  AuditRow,
  InviteOut,
  Me,
} from '@/lib/types';

import { GUEST, renderWithMe, resetAuthUi, userMe } from './authHarness';

/* ── 环境桩 ─────────────────────────────────────────────────────────────────── */

const nav = vi.hoisted(() => ({ pathname: '/admin' }));
vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ refresh() {}, push() {}, replace() {} }),
  redirect: (to: string) => {
    throw new Error(`REDIRECT:${to}`);
  },
}));

const server = vi.hoisted(() => ({ me: vi.fn() }));
vi.mock('@/lib/serverApi', () => ({ serverApi: async () => ({ me: server.me }) }));

// 概览的折线图是 canvas（jsdom 画不了）：换成同接口的替身，仍走 `next/dynamic` 的懒加载路径。
vi.mock('@/components/admin/TrendChart', () => ({
  default: ({ signups, runs }: { signups: unknown[]; runs: unknown[] }) => (
    <div data-testid="trend-chart-stub">{`${signups.length}/${runs.length}`}</div>
  ),
}));

function apiError(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new ApiError(status, { error: { code, message: 'server', requestId: 'req-88', detail } });
}

/** 结构 a11y（axe 未安装 → 结构断言兜底，口径同 `a11y.test.tsx`）。 */
function expectAccessibleStructure(root: HTMLElement) {
  const controls = root.querySelectorAll<HTMLElement>(
    'button, a[href], input:not([type="hidden"]), select, textarea, [role="menuitem"], [role="radio"]',
  );
  const unnamed: string[] = [];
  controls.forEach((el) => {
    const labelled =
      el.getAttribute('aria-label') ||
      el.getAttribute('aria-labelledby') ||
      el.getAttribute('title') ||
      (el.textContent ?? '').trim() ||
      (el.id && root.querySelector(`label[for="${el.id}"]`)) ||
      el.closest('label');
    if (!labelled) unnamed.push(el.outerHTML.slice(0, 120));
    expect(Number(el.getAttribute('tabindex') ?? 0)).toBeLessThanOrEqual(0);
  });
  expect(unnamed).toEqual([]);
  // 每张数据表都有 caption 或可访问名，且表头带 scope。
  root.querySelectorAll('table').forEach((table) => {
    expect(table.querySelector('caption') ?? table.getAttribute('aria-label')).toBeTruthy();
    table.querySelectorAll('thead th').forEach((th) => expect(th.getAttribute('scope')).toBe('col'));
  });
  // 只有一个 h1。
  expect(root.querySelectorAll('h1').length).toBeLessThanOrEqual(1);
}

beforeEach(() => {
  resetAuthUi();
  nav.pathname = '/admin';
  vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [], total: 0 });
});

afterEach(() => {
  vi.restoreAllMocks();
  server.me.mockReset();
});

/* ── 服务端守卫（AC-203）──────────────────────────────────────────────────── */

describe('app/admin/layout.tsx（AC-203）', () => {
  const child = <p>后台内容</p>;

  it('guest → redirect(/login?next=/admin)', async () => {
    server.me.mockResolvedValue({ ...GUEST });
    await expect(AdminLayout({ children: child })).rejects.toThrow('REDIRECT:/login?next=/admin');
  });

  it('普通 user → 渲染 403 视图（不是 404、不是空白），且不渲染后台内容', async () => {
    server.me.mockResolvedValue(userMe('user'));
    const element = await AdminLayout({ children: child });
    render(element);
    expect(screen.getByRole('heading', { name: '无权访问管理后台' })).toBeInTheDocument();
    expect(screen.getByTestId('admin-forbidden')).toHaveTextContent('403');
    expect(screen.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/');
    expect(screen.queryByText('后台内容')).not.toBeInTheDocument();
    expect(screen.queryByText(/404/)).not.toBeInTheDocument();
  });

  it('admin / root → 渲染外壳与子页面', async () => {
    for (const role of ['admin', 'root'] as const) {
      server.me.mockResolvedValue(userMe(role));
      const view = renderWithMe(await AdminLayout({ children: child }), { me: userMe(role) });
      expect(screen.getByText('后台内容')).toBeInTheDocument();
      expect(screen.getByRole('navigation', { name: '管理后台导航' })).toBeInTheDocument();
      view.unmount();
    }
  });

  it('强制改密态 → 先去改密', async () => {
    server.me.mockResolvedValue(userMe('admin', {}, { mustChangePassword: true }));
    await expect(AdminLayout({ children: child })).rejects.toThrow(/REDIRECT:\/account\/password\?forced=1/);
  });

  it('认证服务没响应：不重定向到登录，也不放行，明说“暂时无法确认身份”', async () => {
    server.me.mockRejectedValue(new Error('boom'));
    render(await AdminLayout({ children: child }));
    expect(screen.getByRole('heading', { name: '暂时无法确认身份' })).toBeInTheDocument();
    expect(screen.queryByText('后台内容')).not.toBeInTheDocument();
  });

  it('AUTH_MODE=open（无主体、权限全集）→ 放行', async () => {
    const open: Me = {
      authMode: 'open',
      authenticated: true,
      user: null,
      permissions: [...permissionsForRole('root')],
      quota: null,
    };
    server.me.mockResolvedValue(open);
    renderWithMe(await AdminLayout({ children: child }), { me: open });
    expect(screen.getByText('后台内容')).toBeInTheDocument();
  });
});

/* ── 外壳 ───────────────────────────────────────────────────────────────────── */

describe('AdminShell', () => {
  it('左侧导航：当前页 aria-current=page；额度申请带待处理数字', async () => {
    nav.pathname = '/admin/users';
    vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [], total: 4 });
    const view = renderWithMe(
      <AdminShell>
        <p>x</p>
      </AdminShell>,
      { me: userMe('admin') },
    );
    const navEl = screen.getByRole('navigation', { name: '管理后台导航' });
    expect(within(navEl).getByRole('link', { name: /用户/ })).toHaveAttribute('aria-current', 'page');
    expect(within(navEl).getByRole('link', { name: /概览/ })).not.toHaveAttribute('aria-current');
    expect(within(navEl).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '/admin',
      '/admin/users',
      '/admin/invites',
      '/admin/quota',
      '/admin/workflows',
      '/admin/audit',
      '/admin/settings',
    ]);
    expect(await within(navEl).findByText('4 条待处理')).toBeInTheDocument();
    // 面包屑反映当前页。
    expect(screen.getByRole('navigation', { name: '面包屑' })).toHaveTextContent('用户');
    expectAccessibleStructure(view.container);
  });

  it('待处理数字取不到时不报错、不显示红点', async () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockRejectedValue(apiError(500, 'internal_error'));
    renderWithMe(
      <AdminShell>
        <p>x</p>
      </AdminShell>,
      { me: userMe('admin') },
    );
    await waitFor(() => expect(adminApi.adminQuotaRequests).toHaveBeenCalled());
    expect(screen.queryByText(/条待处理/)).not.toBeInTheDocument();
  });

  it('窄屏：导航是同一个 <nav>（横向滚动），页面内容区自己滚动而不是 body', () => {
    renderWithMe(
      <AdminShell>
        <p>x</p>
      </AdminShell>,
      { me: userMe('admin') },
    );
    const list = within(screen.getByRole('navigation', { name: '管理后台导航' })).getByRole('list');
    expect(list.className).toContain('overflow-x-auto');
    expect(list.className).toContain('min-[900px]:flex-col');
    expect(screen.getByRole('main').className).toContain('overflow-y-auto');
  });
});

/* ── 概览 ───────────────────────────────────────────────────────────────────── */

function overviewFixture(overrides: Partial<AdminOverview> = {}): AdminOverview {
  const days = Array.from({ length: 14 }, (_, i) => `2026-11-${String(7 + i).padStart(2, '0')}`);
  const audit: AuditRow[] = [
    { id: 'au1', at: '2026-11-20T01:00:00Z', actorName: 'jdqqjr', actorRole: 'root', action: 'user.role.change', targetName: 'bob', ip: '203.0.113.0' },
    { id: 'au2', at: '2026-11-20T00:30:00Z', actorName: null, actorRole: null, action: 'root.bootstrap', targetName: null, ip: null },
  ];
  return {
    users: { total: 128, byRole: { user: 125, admin: 2, root: 1 }, active7d: 40, disabled: 3, locked: 2 },
    signups: days.map((day, i) => ({ day, count: i < 7 ? 0 : 2 })),
    runs: { today: 17, succeeded: 12, failed: 3, successRate: 0.8 },
    runsDaily: days.map((day) => ({ day, count: 5 })),
    quota: { consumedToday: 20, refundedToday: 3 },
    topUsers7d: [
      { userId: 'u1', username: 'alice', runs: 9 },
      { userId: 'u2', username: 'bob', runs: 4 },
    ],
    pendingQuotaRequests: 4,
    recentAudit: audit,
    ...overrides,
  };
}

describe('OverviewDashboard', () => {
  it('6 张指标卡 + 折线图（懒加载）+ Top 用户 + 最近审计，数字来自一次 /admin/overview', async () => {
    const overview = vi.spyOn(adminApi, 'adminOverview').mockResolvedValue(overviewFixture());
    const view = renderWithMe(<OverviewDashboard />, { me: userMe('admin') });

    // 骨架屏先出现
    expect(screen.getByRole('status', { name: '加载中…' })).toBeInTheDocument();

    const total = await screen.findByTestId('overview-card-total');
    expect(total).toHaveTextContent('总用户');
    expect(total).toHaveTextContent('128');
    expect(screen.getByTestId('overview-card-new7d')).toHaveTextContent('14'); // 近 7 日 7×2
    expect(screen.getByTestId('overview-card-runs')).toHaveTextContent('17');
    expect(screen.getByTestId('overview-card-rate')).toHaveTextContent('80%');
    expect(screen.getByTestId('overview-card-rate')).toHaveTextContent('成功 12 / 失败 3');
    const pending = screen.getByTestId('overview-card-pending');
    expect(pending).toHaveTextContent('4');
    expect(within(pending).getByRole('link')).toHaveAttribute('href', '/admin/quota');
    expect(screen.getByTestId('overview-card-locked')).toHaveTextContent('2');
    expect(within(screen.getByRole('list', { name: '概览' })).getAllByRole('listitem')).toHaveLength(6);

    // 折线图是 next/dynamic 懒加载的
    expect(await screen.findByTestId('trend-chart-stub')).toHaveTextContent('14/14');
    // 图表的同数据表格给读屏器
    const table = screen.getByRole('table', { name: '近 14 日注册与生成次数' });
    expect(within(table).getAllByRole('row')).toHaveLength(15);

    // Top 5 与最近审计
    expect(screen.getByRole('heading', { name: '近 7 日用量 Top 5' })).toBeInTheDocument();
    expect(screen.getByText('9 次')).toBeInTheDocument();
    expect(screen.getByText('变更角色')).toBeInTheDocument();
    expect(screen.getByText('根管理员引导')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看全部' })).toHaveAttribute('href', '/admin/audit');

    expect(overview).toHaveBeenCalledTimes(1);
    expectAccessibleStructure(view.container);
  });

  it('今日没有已结束任务：成功率显示 —，没有待处理 / 锁定时数字不高亮且无链接', async () => {
    vi.spyOn(adminApi, 'adminOverview').mockResolvedValue(
      overviewFixture({
        runs: { today: 0, succeeded: 0, failed: 0, successRate: null },
        pendingQuotaRequests: 0,
        users: { total: 1, byRole: { root: 1 }, active7d: 1, disabled: 0, locked: 0 },
        topUsers7d: [],
        recentAudit: [],
      }),
    );
    renderWithMe(<OverviewDashboard />, { me: userMe('admin') });
    expect(await screen.findByTestId('overview-card-rate')).toHaveTextContent('—');
    expect(screen.getByTestId('overview-card-rate')).toHaveTextContent('今日暂无已结束任务');
    expect(within(screen.getByTestId('overview-card-pending')).queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByTestId('overview-card-locked')).toHaveTextContent('当前没有账号被锁定');
    expect(screen.getByText('近 7 日没有生成记录')).toBeInTheDocument();
    expect(screen.getByText('暂无审计记录')).toBeInTheDocument();
  });

  it('错误态带 requestId，可重试', async () => {
    const overview = vi
      .spyOn(adminApi, 'adminOverview')
      .mockRejectedValueOnce(apiError(500, 'internal_error'))
      .mockResolvedValueOnce(overviewFixture());
    const user = userEvent.setup();
    renderWithMe(<OverviewDashboard />, { me: userMe('admin') });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('请求编号 req-88');
    await user.click(within(alert).getByRole('button', { name: '重试' }));
    expect(await screen.findByTestId('overview-card-total')).toBeInTheDocument();
    expect(overview).toHaveBeenCalledTimes(2);
  });
});

/* ── 邀请码 ─────────────────────────────────────────────────────────────────── */

function invite(overrides: Partial<InviteOut> & Pick<InviteOut, 'id' | 'code'>): InviteOut {
  return {
    maxUses: 5,
    usedCount: 1,
    expiresAt: '2099-01-01T00:00:00Z',
    bonusCredits: 3,
    note: '内测群',
    disabledAt: null,
    createdAt: '2026-11-01T00:00:00Z',
    ...overrides,
  };
}

describe('InvitesPanel', () => {
  it('inviteState：撤销 > 用尽 > 过期 > 可用', () => {
    const now = new Date('2026-11-20T00:00:00Z').getTime();
    expect(inviteState(invite({ id: '1', code: 'A', disabledAt: '2026-11-01T00:00:00Z', usedCount: 5 }), now)).toBe('revoked');
    expect(inviteState(invite({ id: '1', code: 'A', usedCount: 5 }), now)).toBe('exhausted');
    expect(inviteState(invite({ id: '1', code: 'A', expiresAt: '2026-11-19T00:00:00Z' }), now)).toBe('expired');
    expect(inviteState(invite({ id: '1', code: 'A', expiresAt: null }), now)).toBe('active');
  });

  it('服务端给了 status 就以它为准（撤销 > 过期 > 用尽 > 可用）', () => {
    const now = new Date('2026-11-20T00:00:00Z').getTime();
    // 字段推出来是“已用尽”，但服务端说“已过期”（它的优先级是过期 > 用尽）：信服务端。
    expect(inviteState(invite({ id: '1', code: 'A', usedCount: 5, status: 'expired' }), now)).toBe('expired');
  });

  it('列表：等宽邀请码、已用/上限、状态文字、复制与撤销', async () => {
    vi.spyOn(adminApi, 'adminInvites').mockResolvedValue({
      items: [
        invite({ id: 'i1', code: 'K7QM-X2PD' }),
        invite({ id: 'i2', code: 'AAAA-BBBB', disabledAt: '2026-11-02T00:00:00Z' }),
      ],
    });
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const view = renderWithMe(<InvitesPanel />, { me: userMe('admin') });

    expect(await screen.findByText('K7QM-X2PD')).toBeInTheDocument();
    expect(screen.getAllByText('1 / 5')).toHaveLength(2);
    expect(screen.getByText('可用')).toBeInTheDocument();
    expect(screen.getByText('已撤销')).toBeInTheDocument();
    // 已撤销的行没有撤销按钮
    expect(screen.queryByRole('button', { name: '撤销邀请码 AAAA-BBBB' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '复制邀请码 K7QM-X2PD' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('K7QM-X2PD'));
    expectAccessibleStructure(view.container);
  });

  it('撤销：确认对话框 → DELETE → 刷新列表', async () => {
    const list = vi
      .spyOn(adminApi, 'adminInvites')
      .mockResolvedValueOnce({ items: [invite({ id: 'i1', code: 'K7QM-X2PD' })] })
      .mockResolvedValueOnce({ items: [invite({ id: 'i1', code: 'K7QM-X2PD', disabledAt: '2026-11-20T00:00:00Z' })] });
    const revoke = vi.spyOn(adminApi, 'adminRevokeInvite').mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithMe(<InvitesPanel />, { me: userMe('admin') });

    await user.click(await screen.findByRole('button', { name: '撤销邀请码 K7QM-X2PD' }));
    const dialog = await screen.findByRole('dialog', { name: '撤销邀请码 K7QM-X2PD' });
    await user.click(within(dialog).getByRole('button', { name: '撤销邀请码' }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('i1'));
    expect(await screen.findByText('已撤销')).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('创建：校验范围；成功后只显示一次邀请码并可复制', async () => {
    vi.spyOn(adminApi, 'adminInvites').mockResolvedValue({ items: [] });
    const create = vi
      .spyOn(adminApi, 'adminCreateInvite')
      .mockResolvedValue(invite({ id: 'i9', code: 'ZZZZ-1111', maxUses: 10, usedCount: 0 }));
    const user = userEvent.setup();
    renderWithMe(<InvitesPanel />, { me: userMe('admin') });

    expect(await screen.findByText('还没有邀请码')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: '创建邀请码' })[0] as HTMLElement);
    const dialog = await screen.findByRole('dialog', { name: '创建邀请码' });

    const uses = within(dialog).getByLabelText('可使用次数（1–100）');
    await user.clear(uses);
    await user.type(uses, '101');
    await user.click(within(dialog).getByRole('button', { name: '创建' }));
    expect(within(dialog).getByText('可使用次数需在 1 至 100 之间。')).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();

    await user.clear(uses);
    await user.type(uses, '10');
    await user.selectOptions(within(dialog).getByLabelText('有效期'), 'permanent');
    await user.type(within(dialog).getByLabelText('附带赠送额度（0–20）'), '5');
    await user.click(within(dialog).getByRole('button', { name: '创建' }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ maxUses: 10, expiresInDays: null, bonusCredits: 5 }),
    );
    expect(await within(dialog).findByTestId('secret-value')).toHaveTextContent('ZZZZ-1111');
  });
});

/* ── 额度申请 ───────────────────────────────────────────────────────────────── */

function qreq(overrides: Partial<AdminQuotaRequest> & Pick<AdminQuotaRequest, 'id'>): AdminQuotaRequest {
  return {
    resource: 'report',
    amount: 3,
    reason: '想多看几个新标的',
    status: 'pending',
    createdAt: '2026-11-20T00:00:00Z',
    user: { id: 'u9', username: 'erin', displayName: 'Erin' },
    userRuns7d: 6,
    ...overrides,
  };
}

describe('QuotaRequestsPanel', () => {
  it('待处理：用户、理由、近 7 日用量；通过默认 +3，可改数量', async () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockImplementation(async (status = 'pending') =>
      status === 'pending' ? { items: [qreq({ id: 'q1' })], total: 1 } : { items: [], total: 0 },
    );
    const resolve = vi.spyOn(adminApi, 'adminResolveQuotaRequest').mockResolvedValue({ ...qreq({ id: 'q1' }), status: 'approved' });
    const user = userEvent.setup();
    const view = renderWithMe(<QuotaRequestsPanel />, { me: userMe('admin') });

    const row = await screen.findByTestId('quota-request-erin');
    expect(within(row).getByText('想多看几个新标的')).toBeInTheDocument();
    expect(within(row).getByText('近 7 日生成 6 次')).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /通过 erin 的申请并发放 3 次/ })).toHaveTextContent('通过 +3');

    const grant = within(row).getByLabelText('通过的次数');
    await user.clear(grant);
    await user.type(grant, '5');
    await user.click(within(row).getByRole('button', { name: /通过 erin 的申请并发放 5 次/ }));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('q1', { decision: 'approve', grant: 5 }));
    expectAccessibleStructure(view.container);
  });

  it('通过数量越界不发请求', async () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [qreq({ id: 'q1' })], total: 1 });
    const resolve = vi.spyOn(adminApi, 'adminResolveQuotaRequest');
    const user = userEvent.setup();
    renderWithMe(<QuotaRequestsPanel />, { me: userMe('admin') });
    const row = await screen.findByTestId('quota-request-erin');
    const grant = within(row).getByLabelText('通过的次数');
    await user.clear(grant);
    await user.type(grant, '21');
    await user.click(within(row).getByRole('button', { name: /通过 erin 的申请/ }));
    expect(within(row).getByText('通过次数需在 1 至 20 之间。')).toBeInTheDocument();
    expect(resolve).not.toHaveBeenCalled();
  });

  it('拒绝：理由必填；填写后带 note 提交', async () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [qreq({ id: 'q1' })], total: 1 });
    const resolve = vi.spyOn(adminApi, 'adminResolveQuotaRequest').mockResolvedValue({ ...qreq({ id: 'q1' }), status: 'rejected' });
    const user = userEvent.setup();
    renderWithMe(<QuotaRequestsPanel />, { me: userMe('admin') });
    await user.click(await screen.findByRole('button', { name: '拒绝 erin 的申请' }));

    const dialog = await screen.findByRole('dialog', { name: '拒绝 erin 的申请' });
    await user.click(within(dialog).getByRole('button', { name: '拒绝申请' }));
    expect(within(dialog).getByText('请填写拒绝理由。')).toBeInTheDocument();
    expect(resolve).not.toHaveBeenCalled();
    expect(within(dialog).getByLabelText('拒绝理由')).toHaveFocus();

    await user.type(within(dialog).getByLabelText('拒绝理由'), '本周额度已发放完毕');
    await user.click(within(dialog).getByRole('button', { name: '拒绝申请' }));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('q1', { decision: 'reject', note: '本周额度已发放完毕' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '拒绝 erin 的申请' })).not.toBeInTheDocument());
  });

  it('申请人账号已清理（user 为 null）时不崩，显示 —', async () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [qreq({ id: 'q7', user: null })], total: 1 });
    renderWithMe(<QuotaRequestsPanel />, { me: userMe('admin') });
    const row = await screen.findByTestId('quota-request-—');
    expect(within(row).getByRole('button', { name: /通过 — 的申请/ })).toBeInTheDocument();
  });

  it('已处理标签页只读：状态文字、处理说明，没有任何操作按钮', async () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockImplementation(async (status = 'pending') =>
      status === 'resolved'
        ? {
            items: [qreq({ id: 'q2', status: 'rejected', resolutionNote: '本周额度已发放完毕', resolvedAt: '2026-11-20T01:00:00Z' })],
            total: 1,
          }
        : { items: [], total: 0 },
    );
    const user = userEvent.setup();
    renderWithMe(<QuotaRequestsPanel />, { me: userMe('admin') });
    expect(await screen.findByText('没有待处理的申请')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '已处理' }));
    const row = await screen.findByTestId('quota-resolved-erin');
    expect(within(row).getByText('已拒绝')).toBeInTheDocument();
    expect(within(row).getByText(/本周额度已发放完毕/)).toBeInTheDocument();
    expect(within(row).queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('adminApi.adminQuotaRequests：后端只有 pending / approved / rejected', () => {
  it("'resolved' = approved ∪ rejected，按处理时间倒序；'pending' 只发一次请求", async () => {
    const urls: string[] = [];
    const bodyFor = (url: string) => {
      if (url.includes('status=approved')) {
        return { items: [qreq({ id: 'a', status: 'approved', resolvedAt: '2026-11-20T02:00:00Z' })], total: 1 };
      }
      if (url.includes('status=rejected')) {
        return { items: [qreq({ id: 'r', status: 'rejected', resolvedAt: '2026-11-20T03:00:00Z' })], total: 1 };
      }
      return { items: [qreq({ id: 'p' })], total: 1 };
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        return new Response(JSON.stringify(bodyFor(url)), { status: 200, headers: { 'content-type': 'application/json' } });
      }),
    );
    try {
      // 全局 beforeEach 已把 `adminApi.adminQuotaRequests` 换成桩，这里要真实现：用一个新实例。
      const real = createAdminApi({});
      const resolved = await real.adminQuotaRequests('resolved');
      expect(resolved.items.map((item) => item.id)).toEqual(['r', 'a']);
      expect(resolved.total).toBe(2);
      expect(urls.some((url) => url.includes('status=resolved'))).toBe(false);

      urls.length = 0;
      const pending = await real.adminQuotaRequests('pending');
      expect(pending.items.map((item) => item.id)).toEqual(['p']);
      expect(urls).toHaveLength(1);
      expect(urls[0]).toContain('status=pending');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

/* ── 审计 ───────────────────────────────────────────────────────────────────── */

describe('AuditTable', () => {
  const rows: AuditRow[] = [
    {
      id: 'a1',
      at: '2026-11-20T01:00:00Z',
      actorName: 'jdqqjr',
      actorRole: 'root',
      action: 'quota.grant',
      targetName: 'alice',
      ip: '203.0.113.0',
      requestId: 'rq1',
      detail: { resource: 'report', grant: 5 },
    },
    { id: 'a2', at: '2026-11-20T00:00:00Z', actorName: null, actorRole: null, action: 'root.bootstrap', targetName: null, ip: null },
  ];

  it('只读表格：中文动作标签、操作者、对象、可展开的详情 JSON', async () => {
    vi.spyOn(adminApi, 'adminAudit').mockResolvedValue({ items: rows, nextCursor: null });
    const user = userEvent.setup();
    const view = renderWithMe(<AuditTable />, { me: userMe('admin') });
    // 动作下拉里也有同样的中文标签，所以只在表格里断言。
    const table = await screen.findByRole('table');
    expect(within(table).getByText('发放额度')).toBeInTheDocument();
    expect(within(table).getByText('根管理员引导')).toBeInTheDocument();
    expect(within(table).getByText('系统')).toBeInTheDocument();
    expect(within(table).getByText('quota.grant')).toBeInTheDocument();

    await user.click(within(table).getByText('详情', { selector: 'summary' }));
    expect(within(table).getByText(/"grant": 5/)).toBeInTheDocument();
    // 没有任何写操作入口
    expect(within(table).queryByRole('button')).not.toBeInTheDocument();
    expectAccessibleStructure(view.container);
  });

  it('筛选：动作 / 操作者 / 对象 / 日期范围随“筛选”提交，并可清除', async () => {
    const audit = vi.spyOn(adminApi, 'adminAudit').mockResolvedValue({ items: rows, nextCursor: null });
    const user = userEvent.setup();
    renderWithMe(<AuditTable />, { me: userMe('admin') });
    await screen.findByRole('table');

    await user.selectOptions(screen.getByLabelText('动作'), 'quota.grant');
    await user.type(screen.getByLabelText('操作者'), 'jdqqjr');
    await user.type(screen.getByLabelText('对象'), 'alice');
    await user.type(screen.getByLabelText('开始日期'), '2026-11-01');
    await user.type(screen.getByLabelText('结束日期'), '2026-11-20');
    await user.click(screen.getByRole('button', { name: '筛选' }));

    await waitFor(() =>
      expect(audit).toHaveBeenLastCalledWith(
        expect.objectContaining({ action: 'quota.grant', actor: 'jdqqjr', target: 'alice' }),
      ),
    );
    const last = audit.mock.calls[audit.mock.calls.length - 1]![0]!;
    expect(new Date(last.from as string).getTime()).toBe(new Date('2026-11-01T00:00:00').getTime());
    expect(new Date(last.to as string).getTime()).toBe(new Date('2026-11-20T23:59:59.999').getTime());

    await user.click(screen.getByRole('button', { name: '清除' }));
    await waitFor(() =>
      expect(audit).toHaveBeenLastCalledWith(expect.objectContaining({ action: undefined, actor: undefined })),
    );
  });

  it('游标分页、空态', async () => {
    const audit = vi
      .spyOn(adminApi, 'adminAudit')
      .mockResolvedValueOnce({ items: [rows[0]!], nextCursor: 'c1' })
      .mockResolvedValueOnce({ items: [rows[1]!], nextCursor: null });
    const user = userEvent.setup();
    const view = renderWithMe(<AuditTable />, { me: userMe('admin') });
    const table = await screen.findByRole('table');
    expect(within(table).getByText('发放额度')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '加载更多' }));
    await waitFor(() => expect(within(screen.getByRole('table')).getByText('根管理员引导')).toBeInTheDocument());
    expect(audit).toHaveBeenLastCalledWith(expect.objectContaining({ before: 'c1' }));
    view.unmount();

    vi.spyOn(adminApi, 'adminAudit').mockResolvedValue({ items: [], nextCursor: null });
    renderWithMe(<AuditTable />, { me: userMe('admin') });
    expect(await screen.findByText('没有符合条件的审计记录')).toBeInTheDocument();
  });

  it('错误态带 requestId', async () => {
    vi.spyOn(adminApi, 'adminAudit').mockRejectedValue(apiError(403, 'forbidden'));
    renderWithMe(<AuditTable />, { me: userMe('admin') });
    expect(await screen.findByRole('alert')).toHaveTextContent('请求编号 req-88');
  });
});

/* ── 设置 ───────────────────────────────────────────────────────────────────── */

const SETTINGS: AdminSettings = {
  registrationMode: 'open',
  defaultDailyReport: 3,
  defaultDailyDeepAsk: 10,
  signupBonus: 0,
  siteDailyCap: 0,
};

describe('SettingsForm', () => {
  beforeEach(() => {
    vi.spyOn(adminApi, 'adminSettings').mockResolvedValue(SETTINGS);
    vi.spyOn(api, 'costSummary').mockResolvedValue({
      window: 'mtd',
      from: '',
      to: '',
      spentUsd: 1.5,
      estimatedUsd: 0,
      spentIncludingEstimatedUsd: 1.5,
      budget: { monthlyUsd: 50, remainingUsd: 48.5, resetAt: null, state: 'ok' },
    } as unknown as Awaited<ReturnType<typeof api.costSummary>>);
    vi.spyOn(adminApi, 'adminFeatured').mockResolvedValue({ items: [] });
  });

  it('三选一的注册模式单选卡片：当前值选中、每张附一句后果说明，保存只发改动', async () => {
    const patch = vi.spyOn(adminApi, 'adminPatchSettings').mockResolvedValue({ ...SETTINGS, registrationMode: 'invite' });
    const user = userEvent.setup();
    const view = renderWithMe(<SettingsForm />, { me: userMe('admin') });

    const group = await screen.findByRole('radiogroup', { name: '注册模式' });
    const radios = within(group).getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(within(group).getByRole('radio', { name: /开放注册/ })).toBeChecked();
    expect(within(group).getByText(/必须持有有效邀请码/)).toBeInTheDocument();

    const save = screen.getAllByRole('button', { name: '保存' })[0] as HTMLElement;
    expect(save).toBeDisabled();
    await user.click(within(group).getByRole('radio', { name: /仅限邀请/ }));
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ registrationMode: 'invite' }));
    expectAccessibleStructure(view.container);
  });

  it('额度与上限：越界在本地拦住并聚焦；合法时只提交变化的字段', async () => {
    const patch = vi.spyOn(adminApi, 'adminPatchSettings').mockResolvedValue({ ...SETTINGS, siteDailyCap: 200 });
    const user = userEvent.setup();
    renderWithMe(<SettingsForm />, { me: userMe('admin') });

    const cap = await screen.findByLabelText('全站日生成总量上限');
    expect(screen.getByText(/月度预算默认不设上限，这是唯一的人头护栏/)).toBeInTheDocument();
    await user.clear(cap);
    await user.type(cap, '99999');
    await user.click(screen.getAllByRole('button', { name: '保存' })[1] as HTMLElement);
    expect(await screen.findByText(/需在 0 至 10000 之间/)).toBeInTheDocument();
    expect(cap).toHaveFocus();
    expect(patch).not.toHaveBeenCalled();

    await user.clear(cap);
    await user.type(cap, '200');
    await user.click(screen.getAllByRole('button', { name: '保存' })[1] as HTMLElement);
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ siteDailyCap: 200 }));
  });

  it('服务端 422 invalid_setting(field) 回落到对应输入框并聚焦', async () => {
    vi.spyOn(adminApi, 'adminPatchSettings').mockRejectedValue(apiError(422, 'invalid_setting', { field: 'signup_bonus' }));
    const user = userEvent.setup();
    renderWithMe(<SettingsForm />, { me: userMe('admin') });
    const bonus = await screen.findByLabelText('新用户赠送额度');
    await user.clear(bonus);
    await user.type(bonus, '5');
    await user.click(screen.getAllByRole('button', { name: '保存' })[1] as HTMLElement);
    expect(await screen.findByText(/新用户赠送额度 需在 0 至 20 之间/)).toBeInTheDocument();
    expect(bonus).toHaveFocus();
  });

  it('admin 看不到月度预算与精选自选；root 看得到', async () => {
    const view = renderWithMe(<SettingsForm />, { me: userMe('admin') });
    await screen.findByRole('radiogroup', { name: '注册模式' });
    expect(screen.queryByRole('heading', { name: '月度预算' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '精选自选列表' })).not.toBeInTheDocument();
    view.unmount();

    renderWithMe(<SettingsForm />, { me: userMe('root') });
    expect(await screen.findByRole('heading', { name: '月度预算' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: '精选自选列表' })).toBeInTheDocument();
  });

  it('root：月度预算保存走 POST /cost/budget；精选自选可搜索添加与移除', async () => {
    const setBudget = vi.spyOn(api, 'setBudget').mockResolvedValue({} as Awaited<ReturnType<typeof api.setBudget>>);
    vi.spyOn(adminApi, 'adminFeatured').mockResolvedValue({
      items: [
        {
          instrument: { id: 'i1', symbol: '600519', name: '贵州茅台', market: 'CN', currency: 'CNY', tz: 'Asia/Shanghai' },
          sortOrder: 0,
          pinned: false,
          addedAt: '2026-11-01T00:00:00Z',
        },
      ],
    } as Awaited<ReturnType<typeof adminApi.adminFeatured>>);
    vi.spyOn(api, 'searchSymbols').mockResolvedValue({
      hits: [{ symbol: '000001', name: '平安银行', market: 'CN', currency: 'CNY', score: 1, existing: true }],
    });
    vi.spyOn(api, 'resolveSymbol').mockResolvedValue({
      instrument: { id: 'i2', symbol: '000001', name: '平安银行', market: 'CN', currency: 'CNY', tz: 'Asia/Shanghai' },
      created: false,
      needsConfirm: false,
      candidates: [],
    });
    const add = vi.spyOn(adminApi, 'adminAddFeatured').mockResolvedValue({ items: [] });
    const remove = vi.spyOn(adminApi, 'adminRemoveFeatured').mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithMe(<SettingsForm />, { me: userMe('root') });

    const budget = await screen.findByLabelText('月度预算（美元）');
    await waitFor(() => expect(budget).toHaveValue(50));
    await user.clear(budget);
    await user.type(budget, '80');
    const budgetCard = screen.getByRole('heading', { name: '月度预算' }).closest('section') as HTMLElement;
    await user.click(within(budgetCard).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(setBudget).toHaveBeenCalledWith(80));

    await user.click(await screen.findByRole('button', { name: '移除 贵州茅台' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('i1'));

    await user.type(screen.getByLabelText('搜索标的'), '平安');
    await user.click(screen.getByRole('button', { name: '搜索' }));
    await user.click(await screen.findByRole('button', { name: /添加标的 000001/ }));
    await waitFor(() => expect(add).toHaveBeenCalledWith('i2'));
  });

  it('加载失败：错误态带 requestId', async () => {
    vi.spyOn(adminApi, 'adminSettings').mockRejectedValue(apiError(500, 'internal_error'));
    renderWithMe(<SettingsForm />, { me: userMe('admin') });
    expect(await screen.findByRole('alert')).toHaveTextContent('请求编号 req-88');
  });
});

/* ── 文案纪律 ───────────────────────────────────────────────────────────────── */

describe('copyAdmin', () => {
  it('不用感叹号；审计词表（§24.3.10）每一项都有中文标签', () => {
    const flat = JSON.stringify(copyAdmin);
    expect(flat).not.toMatch(/[!！]/);
    const vocabulary = [
      'auth.login.success', 'auth.login.failure', 'auth.login.lockout', 'auth.logout', 'auth.register',
      'auth.password.change', 'auth.password.reset', 'auth.session.revoke',
      'user.create', 'user.role.change', 'user.disable', 'user.enable', 'user.delete', 'user.unlock',
      'quota.grant', 'quota.limit.set', 'quota.request.create', 'quota.request.approve', 'quota.request.reject',
      'invite.create', 'invite.revoke', 'settings.update', 'featured.add', 'featured.remove',
      'schedule.create', 'schedule.update', 'schedule.delete', 'report.manage_other', 'run.cancel_other',
      'root.bootstrap', 'root.password.cli_reset',
      // ★Y3–Y5（§25.4.1）：31 → 36
      'auth.recovery.rotate', 'auth.recovery.failure', 'auth.password.recover', 'user.self_delete', 'admin.export',
      // 研究流程图（report-workflow-graph §3.9）：36 → 41
      'workflow.create', 'workflow.publish', 'workflow.rollback', 'workflow.default.set', 'workflow.archive',
    ];
    for (const action of vocabulary) {
      expect(copyAdmin.audit.actions[action], action).toBeTruthy();
    }
    expect(Object.keys(copyAdmin.audit.actions).sort()).toEqual([...vocabulary].sort());
  });
});
