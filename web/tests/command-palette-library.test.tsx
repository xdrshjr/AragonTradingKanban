/**
 * ★v14 评审（R-2）：命令面板的「报告库」入口与「最近 5 份报告」。
 *
 * 这一组用例存在的理由很具体：v14 的实现给 `CommandPalette` 加了 `onOpenLibrary` /
 * `recentReports` 两个 prop，**但没有任何调用方传它们**。tsc 不会红（prop 是可选的）、
 * 页面也不报错——只是"命令面板里能从最近一份报告回到它"这个能力**根本不存在**，
 * 而完成报告里写的是"命令面板入口已落地"。空 prop 是最难发现的一种未实现。
 *
 * 三条各守一件事：
 *
 * 1. `onOpenLibrary` 在时出现「报告库」；**不在时整项消失**（不是画一个点了没反应的按钮）。
 * 2. 最近 5 份报告的一个要点开**那一版**（`onOpenReport(symbol, id)`），不是"这个标的的最新版"
 *    （U-0b 的原话：点 v12 却看到 v13）。所以断言收到的是 `id`，而不是只收到 symbol。
 * 3. 面板关掉之后不该还能被点（`toggle(false)`）：命令面板的每一项都必须自己收起面板，
 *    否则"点了、跳转了、面板还盖在上面"。
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CommandPalette, type CommandPaletteProps } from '@/components/shell/CommandPalette';
import { copy } from '@/lib/copy';
import { useUiStore } from '@/lib/store';
import type { ReportSummary } from '@/lib/types';

const ID_V12 = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
const ID_V13 = '0123456789abcdef0123456789abcdef';

const RECENT = [
  { id: ID_V13, symbol: '600519.SH', name: '贵州茅台', headline: '直营渠道改革兑现利润弹性', version: 13 },
  { id: ID_V12, symbol: '600519.SH', name: '贵州茅台', headline: '需求见底，价格带先动', version: 12 },
];

function baseProps(overrides: Partial<CommandPaletteProps> = {}): CommandPaletteProps {
  return {
    instruments: [],
    reports: [] as ReportSummary[],
    onSelectInstrument: vi.fn(),
    onSelectVersion: vi.fn(),
    onGenerate: vi.fn(),
    onExport: vi.fn(),
    onToggleChart: vi.fn(),
    ...overrides,
  };
}

/** 面板必须先处于打开态才会渲染。 */
beforeEach(() => {
  useUiStore.setState({ commandPaletteOpen: true });
});

describe('CommandPalette · 报告库入口（★R-2）', () => {
  it('传了 onOpenLibrary 就出现「报告库」，点一下交给调用方并收起面板', async () => {
    const onOpenLibrary = vi.fn();
    render(<CommandPalette {...baseProps({ onOpenLibrary })} />);

    const item = screen.getByText(copy.library.open);
    await userEvent.click(item);

    expect(onOpenLibrary).toHaveBeenCalledTimes(1);
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
  });

  it('没有 onOpenLibrary 时**整项不出现**（不画一个点了没反应的入口）', () => {
    render(<CommandPalette {...baseProps()} />);
    expect(screen.queryByText(copy.library.open)).toBeNull();
  });

  it('最近 5 份报告点开的是**那一版**（symbol + reportId，不是"最新版"）', async () => {
    const onOpenReport = vi.fn();
    render(<CommandPalette {...baseProps({ onOpenLibrary: vi.fn(), onOpenReport, recentReports: RECENT })} />);

    // 行文案形如「贵州茅台 · v12」；点**旧的那一份**，断言带的是 v12 的 id。
    await userEvent.click(screen.getByText('贵州茅台 · v12'));

    expect(onOpenReport).toHaveBeenCalledWith('600519.SH', ID_V12);
    expect(onOpenReport).not.toHaveBeenCalledWith('600519.SH', ID_V13);
  });

  it('`recentReports` 没有消费者（`onOpenReport` 缺席）时不出现死项', () => {
    render(<CommandPalette {...baseProps({ onOpenLibrary: vi.fn(), recentReports: RECENT })} />);
    expect(screen.queryByText('贵州茅台 · v12')).toBeNull();
  });

  it('最多 5 条（再多就不是"最近"了，而是第二份报告库）', () => {
    const many = Array.from({ length: 8 }, (_, index) => ({
      id: `${String(index).padStart(2, '0')}${'a'.repeat(30)}`,
      symbol: '600519.SH',
      name: `标的 ${index}`,
      headline: 'h',
      version: index + 1,
    }));
    render(<CommandPalette {...baseProps({ onOpenLibrary: vi.fn(), onOpenReport: vi.fn(), recentReports: many })} />);

    expect(screen.getByText('标的 4 · v5')).toBeTruthy();
    expect(screen.queryByText('标的 5 · v6')).toBeNull();
  });
});
