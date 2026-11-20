import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GenerateButton } from '@/components/generation/GenerateButton';
import { useAuthUi } from '@/lib/auth';

import { GUEST, renderWithMe, resetAuthUi, userMe } from './authHarness';

/**
 * AC-200（§24.3.15c）：工作台生成按钮四态——guest / 有额度 / 0 额度 / admin。
 * 文案与可点击性是产品：guest 点击弹登录框且**不发任何 POST /reports**，0 额度用 `aria-disabled`（仍可聚焦）。
 */

const fetchMock = vi.fn();

beforeEach(() => {
  resetAuthUi();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('GenerateButton 三态（AC-200）', () => {
  it('guest：文案“登录后生成投研报告”，点击打开登录弹窗、保留意图，且不调用 onGenerate / 不发请求', async () => {
    const onGenerate = vi.fn();
    renderWithMe(<GenerateButton onGenerate={onGenerate} symbol="600519.SH" size="lg" showQuota />, { me: GUEST });

    const button = screen.getByRole('button', { name: '登录后生成投研报告' });
    expect(button).not.toHaveAttribute('aria-disabled');
    expect(button).not.toBeDisabled();
    expect(screen.getByText('游客可浏览全部研报')).toBeInTheDocument();

    await userEvent.click(button);

    expect(onGenerate).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    const ui = useAuthUi.getState();
    expect(ui.authOpen).toBe(true);
    expect(ui.authTab).toBe('login');
    expect(ui.intent).toMatchObject({ type: 'generate', symbol: '600519.SH', focusId: button.id });
  });

  it('guest 的小按钮用短文案（头部空间有限），可访问描述仍说明“游客可浏览”', () => {
    renderWithMe(<GenerateButton onGenerate={() => {}} size="sm" />, { me: GUEST });
    const button = screen.getByRole('button', { name: '登录后生成' });
    expect(button).toHaveAccessibleDescription('游客可浏览全部研报');
  });

  it('user 有额度：照旧触发生成；showQuota 时旁边有“今日剩余 2/3”芯片', async () => {
    const onGenerate = vi.fn();
    renderWithMe(<GenerateButton onGenerate={onGenerate} showQuota>生成投研报告</GenerateButton>, {
      me: userMe('user', { usedToday: 1, remaining: 2 }),
    });

    const button = screen.getByRole('button', { name: /生成投研报告/ });
    expect(button).not.toHaveAttribute('aria-disabled');
    expect(button).toHaveAttribute('data-generate-state', 'ready');
    expect(screen.getByTestId('quota-chip')).toHaveTextContent('今日剩余 2/3');

    await userEvent.click(button);
    expect(onGenerate).toHaveBeenCalledTimes(1);
    expect(useAuthUi.getState().authOpen).toBe(false);
  });

  it('user 额度为 0：aria-disabled、仍可聚焦、描述说明原因；点击打开额度对话框而不是生成', async () => {
    const onGenerate = vi.fn();
    renderWithMe(<GenerateButton onGenerate={onGenerate} />, {
      me: userMe('user', { usedToday: 3, remaining: 0 }),
    });

    const button = screen.getByRole('button', { name: '今日额度已用完' });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    // 关键：不是原生 disabled —— 键盘与读屏用户仍能 Tab 到它、读到原因。
    expect(button).not.toBeDisabled();
    button.focus();
    expect(button).toHaveFocus();
    expect(button).toHaveAccessibleDescription(/今日已用完 3\/3/);

    await userEvent.click(button);
    expect(onGenerate).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useAuthUi.getState().quotaOpen).toBe(true);
  });

  it('额度为 0 但还有赠送额度（remaining>0）：不是用完态', () => {
    renderWithMe(<GenerateButton onGenerate={() => {}}>生成投研报告</GenerateButton>, {
      me: userMe('user', { usedToday: 3, bonus: 2, remaining: 2 }),
    });
    expect(screen.getByRole('button', { name: /生成投研报告/ })).not.toHaveAttribute('aria-disabled');
  });

  it('admin / root：原文案、无芯片、照旧', async () => {
    const onGenerate = vi.fn();
    renderWithMe(<GenerateButton onGenerate={onGenerate} showQuota>生成投研报告</GenerateButton>, { me: userMe('admin') });
    expect(screen.queryByTestId('quota-chip')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /生成投研报告/ }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it('生成进行中：loading + aria-busy，防止双击', async () => {
    const onGenerate = vi.fn();
    renderWithMe(<GenerateButton onGenerate={onGenerate} generating>生成投研报告</GenerateButton>, { me: userMe('admin') });
    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
  });

  it('开放模式 / 无 Provider 的孤立渲染按原行为：原文案、照旧触发', async () => {
    const onGenerate = vi.fn();
    const { render } = await import('@testing-library/react');
    render(<GenerateButton onGenerate={onGenerate}>生成投研报告</GenerateButton>);
    await userEvent.click(screen.getByRole('button', { name: /生成投研报告/ }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it('guest 但 disabled（没有当前标的）：保持原生 disabled，不弹框', async () => {
    renderWithMe(<GenerateButton onGenerate={() => {}} disabled />, { me: GUEST });
    const button = screen.getByRole('button', { name: '登录后生成投研报告' });
    expect(button).toBeDisabled();
  });
});
