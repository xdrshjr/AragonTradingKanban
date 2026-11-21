import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import golden from '../../contracts/workflow_ops.golden.json';
import { WorkflowEditor } from '@/components/workflow/WorkflowEditor';
import { WorkflowEditorLoader } from '@/components/workflow/WorkflowEditorLoader';
import { ApiError } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import { SPLIT_STORAGE_KEY } from '@/lib/workflow/layout';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { TemplateDetail, ValidationReport, WorkflowDoc } from '@/lib/workflow/types';

import { TooltipProvider } from '@/components/ui/Tooltip';

// 画布 / 面板由别的模块测试；这里只验证外壳的装配、比例与让位。
vi.mock('@/components/workflow/WorkflowCanvas', () => ({ default: () => <div data-testid="canvas-stub" /> }));
vi.mock('@/components/workflow/AssistPanel', () => ({ default: () => <div data-testid="assist-stub">助手</div> }));
vi.mock('@/components/workflow/Inspector', () => ({ Inspector: () => <div data-testid="inspector-stub" /> }));
vi.mock('@/components/workflow/StructurePane', () => ({
  StructurePane: ({ mode = 'docked', onOpen }: { mode?: string; onOpen?: () => void }) => (
    <button type="button" data-testid={`structure-${mode}`} onClick={onOpen}>
      结构
    </button>
  ),
}));
vi.mock('@/components/workflow/BottomDock', () => ({ BottomDock: () => <div data-testid="dock-stub" /> }));
vi.mock('@/components/workflow/PublishDialog', () => ({
  PublishDialog: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="发布对话框" /> : null),
}));

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, refresh() {}, replace() {} }),
  usePathname: () => '/admin/workflows/wf_t',
}));

const BASE = (golden as unknown as { ops: { doc: WorkflowDoc }[] }).ops[0]!.doc;
const REPORT: ValidationReport = { issues: [], errorCount: 0, warningCount: 0, publishable: true, blank: false };

function detail(overrides: Partial<TemplateDetail['meta']> = {}, validation = REPORT): TemplateDetail {
  return {
    meta: {
      id: 'wf_t',
      name: '估值加强版',
      description: '',
      isDefault: false,
      isBuiltin: false,
      status: 'active',
      latestVersion: 2,
      stepCount: 1,
      dirty: true,
      updatedAt: '2026-11-19T00:00:00Z',
      updatedByName: null,
      runs30d: 0,
      ...overrides,
    },
    draft: { doc: structuredClone(BASE), revision: 4, validation, dirty: true },
    versions: [],
  };
}

function setViewport(width: number, height = 800): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
}

function mount(width: number, data = detail()) {
  setViewport(width);
  useWorkflowStore.getState().reset();
  useWorkflowStore.getState().load(data);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <WorkflowEditor />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  nav.push.mockClear();
});
afterEach(() => {
  useWorkflowStore.getState().reset();
  vi.restoreAllMocks();
  setViewport(1024, 768);
});

describe('70/30 编辑器外壳（AC-W26）', () => {
  it('1280px：左 896 / 右 384（70% / 30%），右栏 ≥ 320，助手常驻', async () => {
    const { container } = mount(1280);
    const root = container.querySelector<HTMLElement>('[data-testid="workflow-editor"]')!;
    expect(root.style.getPropertyValue('--wf-left-w')).toBe('896px');
    expect(root.style.getPropertyValue('--wf-right-w')).toBe('384px');
    expect(await screen.findByTestId('assist-stub')).toBeInTheDocument();
    expect(screen.getByRole('separator', { name: /调整流程画布与 AI 助手的宽度/ })).toHaveAttribute('aria-valuenow', '70');
  });

  it('1280px 内层：结构树与检查器并排，底部工具窗默认收起', async () => {
    mount(1280);
    const left = screen.getByTestId('wf-left');
    expect(left).toHaveAttribute('data-structure', 'docked');
    expect(left).toHaveAttribute('data-inspector', 'docked');
    expect(left).toHaveAttribute('data-dock', 'collapsed');
    expect(await screen.findByTestId('canvas-stub')).toBeInTheDocument();
    expect(screen.getByTestId('structure-docked')).toBeInTheDocument();
  });

  it('1100px：结构树自动收为图标条；点击图标以浮层展开', async () => {
    mount(1100);
    expect(screen.getByTestId('wf-left')).toHaveAttribute('data-structure', 'icon');
    await userEvent.click(screen.getByTestId('structure-icon'));
    expect(screen.getByTestId('structure-docked')).toBeInTheDocument();
  });

  it('方向键 ±16px、Home / End 夹取、双击复位，并持久化', async () => {
    mount(1280);
    const separator = screen.getByRole('separator');
    separator.focus();
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    await waitFor(() => expect(Number(separator.getAttribute('aria-valuenow'))).toBeGreaterThan(70));
    fireEvent.keyDown(separator, { key: 'End' });
    // 1280px 下 80% 会让右栏 < 320px：像素兜底把左栏顶在 75%（右栏恒 ≥ 320）。
    await waitFor(() => expect(separator).toHaveAttribute('aria-valuenow', '75'));
    expect(window.localStorage.getItem(SPLIT_STORAGE_KEY)).toBe('80');
    fireEvent.keyDown(separator, { key: 'Home' });
    await waitFor(() => expect(separator).toHaveAttribute('aria-valuenow', '55'));
    fireEvent.doubleClick(separator);
    await waitFor(() => expect(separator).toHaveAttribute('aria-valuenow', '70'));
  });

  it('刷新后保持：从 localStorage 读回比例', async () => {
    window.localStorage.setItem(SPLIT_STORAGE_KEY, '60');
    mount(1280);
    await waitFor(() => expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '60'));
  });

  it('<1100px：右栏收为抽屉，状态栏有常驻开关，⌘J 打开', async () => {
    mount(1000);
    expect(screen.queryByTestId('wf-right')).toBeNull();
    expect(screen.queryByRole('separator')).toBeNull();
    const toggle = screen.getByRole('button', { name: /AI 助手/ });
    await userEvent.click(toggle);
    expect(await screen.findByTestId('assist-stub')).toBeInTheDocument();
  });

  it('⌘J 在抽屉模式下开合 AI 面板', async () => {
    mount(1000);
    fireEvent.keyDown(window, { key: 'j', metaKey: true });
    expect(await screen.findByTestId('assist-stub')).toBeInTheDocument();
  });

  it('<768px：只读查看并提示在桌面端编辑', async () => {
    mount(700);
    await waitFor(() => expect(useWorkflowStore.getState().readOnly).toBe(true));
    expect(screen.getByText('请在桌面端编辑流程（当前为只读查看）')).toBeInTheDocument();
    expect(useWorkflowStore.getState().lockedByTemplate).toBe(false);
  });
});

describe('标题栏与状态栏', () => {
  it('显示名称、版本徽标与"草稿有未发布修改"；状态栏显示 rev / 步骤数 / 估算', () => {
    mount(1280);
    expect(screen.getByTestId('workflow-title')).toHaveTextContent('估值加强版');
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('草稿有未发布修改')).toBeInTheDocument();
    const bar = screen.getByRole('contentinfo', { name: '编辑器状态栏' });
    expect(within(bar).getByText('rev 4')).toBeInTheDocument();
    expect(within(bar).getByText('步骤 1')).toBeInTheDocument();
    expect(within(bar).getByText(/估算 ≈ \d+\/40 回合/)).toBeInTheDocument();
  });

  it('内置模板：只读锁 + 【复制为可编辑副本】创建副本并跳转（AC-W31 外壳部分）', async () => {
    const create = vi.spyOn(adminApi.workflows, 'create').mockResolvedValue({
      meta: detail().meta,
      draft: detail().draft,
      versions: [],
    });
    mount(1280, detail({ isBuiltin: true, name: '标准五段式研究', id: 'wf_builtin_standard' }));
    expect(useWorkflowStore.getState().readOnly).toBe(true);
    expect(screen.getByRole('button', { name: '发布…' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '复制为可编辑副本' }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ name: '标准五段式研究 副本', from: { templateId: 'wf_builtin_standard' } }),
    );
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/admin/workflows/wf_t'));
  });

  it('⌘⇧P 打开发布对话框；有错误时自动展开问题页签', async () => {
    const withError: ValidationReport = {
      issues: [{ code: 'cycle', severity: 'error', entityKind: 'graph', entityId: null }],
      errorCount: 1,
      warningCount: 0,
      publishable: false,
      blank: false,
    };
    mount(1280, detail({}, withError));
    await waitFor(() => expect(useWorkflowStore.getState().panes.dock).toBe(true));
    expect(screen.getByTestId('wf-left')).toHaveAttribute('data-dock', 'docked');
    fireEvent.keyDown(window, { key: 'p', metaKey: true, shiftKey: true });
    expect(await screen.findByRole('dialog', { name: '发布对话框' })).toBeInTheDocument();
  });

  it('⌘Z / ⌘⇧Z 调用撤销重做；焦点在输入框时交还浏览器', () => {
    mount(1280);
    const undo = vi.fn();
    useWorkflowStore.setState({ undo });
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
    const input = document.createElement('input');
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
    input.remove();
  });

  it('aria-live 区域播报 store 的 announce', async () => {
    mount(1280);
    act(() => useWorkflowStore.setState({ announce: '已新增步骤「同业对比」' }));
    expect(screen.getByTestId('wf-announce')).toHaveTextContent('已新增步骤「同业对比」');
  });
});

describe('编辑器加载器', () => {
  function renderLoader() {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TooltipProvider>
          <WorkflowEditorLoader templateId="wf_t" />
        </TooltipProvider>
      </QueryClientProvider>,
    );
  }

  it('取到详情后装进 store 并渲染编辑器', async () => {
    setViewport(1280);
    useWorkflowStore.getState().reset();
    vi.spyOn(adminApi.workflows, 'get').mockResolvedValue(detail());
    renderLoader();
    expect(await screen.findByTestId('workflow-editor')).toBeInTheDocument();
    expect(useWorkflowStore.getState().revision).toBe(4);
  });

  it('404 显示"不存在或已被归档"', async () => {
    vi.spyOn(adminApi.workflows, 'get').mockRejectedValue(
      new ApiError(404, { error: { code: 'workflow_not_found', message: 'x', requestId: '-', detail: {} } }),
    );
    renderLoader();
    expect(await screen.findByText('流程不存在或已被归档')).toBeInTheDocument();
  });

  it('其它错误显示错误态与重试', async () => {
    vi.spyOn(adminApi.workflows, 'get').mockRejectedValue(new Error('boom'));
    renderLoader();
    expect(await screen.findByRole('alert')).toHaveTextContent('流程加载失败');
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument();
  });
});
