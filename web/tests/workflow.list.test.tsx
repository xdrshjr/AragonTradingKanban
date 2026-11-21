import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AdminShell } from '@/components/admin/AdminShell';
import { DefaultWorkflowCard } from '@/components/workflow/DefaultWorkflowCard';
import { WorkflowListClient } from '@/components/workflow/WorkflowListClient';
import { adminApi } from '@/lib/api.admin';
import type { TemplateList, TemplateSummary } from '@/lib/workflow/types';

import { renderWithMe, userMe } from './authHarness';

const nav = vi.hoisted(() => ({ pathname: '/admin/workflows', push: vi.fn() }));
vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ refresh() {}, push: nav.push, replace() {} }),
}));

function template(overrides: Partial<TemplateSummary>): TemplateSummary {
  return {
    id: 'wf_x',
    name: '估值加强版',
    description: '',
    isDefault: false,
    isBuiltin: false,
    status: 'active',
    latestVersion: 2,
    stepCount: 6,
    dirty: false,
    updatedAt: '2026-11-19T00:00:00Z',
    updatedByName: 'alice',
    runs30d: 12,
    ...overrides,
  };
}

const LIST: TemplateList = {
  enabled: true,
  defaultTemplateId: 'wf_builtin_standard',
  items: [
    template({ id: 'wf_builtin_standard', name: '标准五段式研究', isBuiltin: true, isDefault: true, latestVersion: 1 }),
    template({ id: 'wf_x', name: '估值加强版', dirty: true }),
    template({ id: 'wf_draft', name: '草稿流程', latestVersion: null }),
  ],
};

beforeEach(() => {
  nav.push.mockClear();
  vi.spyOn(adminApi.workflows, 'list').mockResolvedValue(structuredClone(LIST));
});
afterEach(() => vi.restoreAllMocks());

describe('研究流程列表页', () => {
  it('渲染表格：默认★ / 内置 / 草稿未发布 / 有未发布修改 与列表列', async () => {
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(4);
    expect(within(table).getByText('默认')).toBeInTheDocument();
    expect(within(table).getByText('内置')).toBeInTheDocument();
    expect(within(table).getByText('草稿未发布')).toBeInTheDocument();
    expect(within(table).getByText('有未发布修改')).toBeInTheDocument();
    expect(within(table).getAllByText('12 次')).toHaveLength(3);
  });

  it('内置与默认不能归档；未发布不能设为默认', async () => {
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    await screen.findByRole('table');
    expect(screen.queryByRole('button', { name: '归档 标准五段式研究' })).toBeNull();
    expect(screen.getByRole('button', { name: '归档 估值加强版' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '设为默认 草稿流程' })).toBeNull();
    expect(screen.getByRole('button', { name: '设为默认 估值加强版' })).toBeInTheDocument();
  });

  it('总开关：切换调用 setEnabled(false)', async () => {
    const setEnabled = vi.spyOn(adminApi.workflows, 'setEnabled').mockResolvedValue({ enabled: false });
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    const toggle = await screen.findByRole('switch', { name: /使用自定义研究流程/ });
    await userEvent.click(toggle);
    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith(false));
  });

  it('设为默认要二次确认，确认后调用 setDefault', async () => {
    const setDefault = vi
      .spyOn(adminApi.workflows, 'setDefault')
      .mockResolvedValue({ defaultTemplateId: 'wf_x', version: 2 });
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    await userEvent.click(await screen.findByRole('button', { name: '设为默认 估值加强版' }));
    expect(setDefault).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole('button', { name: '设为默认', hidden: false }));
    await waitFor(() => expect(setDefault).toHaveBeenCalledWith('wf_x'));
  });

  it('复制 → 创建对话框预选「复制已有流程」，提交后跳转到副本编辑器', async () => {
    const create = vi.spyOn(adminApi.workflows, 'create').mockResolvedValue({
      meta: template({ id: 'wf_copy', name: '估值加强版 副本' }),
      draft: {} as never,
      versions: [],
    });
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    await userEvent.click(await screen.findByRole('button', { name: '复制 估值加强版' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('名称')).toHaveValue('估值加强版 副本');
    await userEvent.click(within(dialog).getByRole('button', { name: '创建并编辑' }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ name: '估值加强版 副本', description: '', from: { templateId: 'wf_x' } }),
    );
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/admin/workflows/wf_copy'));
  });

  it('新建：名称必填，空白起点', async () => {
    const create = vi.spyOn(adminApi.workflows, 'create').mockResolvedValue({
      meta: template({ id: 'wf_new', name: '新流程' }),
      draft: {} as never,
      versions: [],
    });
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    await userEvent.click(await screen.findByRole('button', { name: '新建流程' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: '创建并编辑' }));
    expect(within(dialog).getByText(/请填写名称/)).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    await userEvent.type(within(dialog).getByLabelText('名称'), '新流程');
    await userEvent.click(within(dialog).getByRole('button', { name: '创建并编辑' }));
    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: '新流程', description: '', from: 'blank' }));
  });

  it('加载失败显示错误态与重试', async () => {
    vi.spyOn(adminApi.workflows, 'list').mockRejectedValue(new Error('boom'));
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    expect(await screen.findByRole('alert')).toHaveTextContent('研究流程加载失败');
  });

  it('没有模板时给空态与新建入口', async () => {
    vi.spyOn(adminApi.workflows, 'list').mockResolvedValue({ enabled: true, defaultTemplateId: null, items: [] });
    renderWithMe(<WorkflowListClient />, { me: userMe('admin') });
    expect(await screen.findByText('还没有流程')).toBeInTheDocument();
  });
});

describe('导航与设置卡片的权限裁剪（AC-W33）', () => {
  it('admin / root 看到「研究流程」导航，user 看不到', () => {
    vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [], total: 0 });
    for (const role of ['admin', 'root'] as const) {
      const { unmount } = renderWithMe(<AdminShell>内容</AdminShell>, { me: userMe(role) });
      expect(screen.getByRole('link', { name: '研究流程' })).toHaveAttribute('href', '/admin/workflows');
      unmount();
    }
    renderWithMe(<AdminShell>内容</AdminShell>, { me: userMe('user') });
    expect(screen.queryByRole('link', { name: '研究流程' })).toBeNull();
  });

  it('编辑器路径下外壳全出血：隐藏 200px 导航且不套 max-w 容器', () => {
    nav.pathname = '/admin/workflows/wf_x';
    vi.spyOn(adminApi, 'adminQuotaRequests').mockResolvedValue({ items: [], total: 0 });
    const { container } = renderWithMe(<AdminShell>编辑器</AdminShell>, { me: userMe('admin') });
    expect(container.querySelector('main')).toHaveAttribute('data-full-bleed', 'true');
    expect(container.querySelector('main .max-w-\\[1120px\\]')).toBeNull();
    expect(screen.getByRole('navigation', { name: '管理后台导航' })).toHaveClass('hidden');
    nav.pathname = '/admin/workflows';
  });

  it('设置页只读卡片：显示当前默认流程与版本', async () => {
    vi.spyOn(adminApi.workflows, 'defaultSummary').mockResolvedValue({
      templateId: 'wf_builtin_standard',
      name: '标准五段式研究',
      version: 3,
      hash: 'abc',
      enabled: true,
    });
    renderWithMe(<DefaultWorkflowCard />, { me: userMe('admin') });
    await waitFor(() =>
      expect(screen.getByTestId('default-workflow-text')).toHaveTextContent('当前默认研究流程：标准五段式研究 v3'),
    );
    expect(screen.getByRole('link', { name: '管理' })).toHaveAttribute('href', '/admin/workflows');
  });
});
