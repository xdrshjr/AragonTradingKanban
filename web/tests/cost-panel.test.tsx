import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BudgetGateDialog, CostPanel } from '@/components/cost/CostPanel';
import { copy, fill } from '@/lib/copy';
import type { CostSummary } from '@/lib/types';

const mocks = vi.hoisted(() => ({
  costSummary:
    vi.fn<(window?: '7d' | '30d' | '90d' | 'mtd' | 'ytd') => Promise<CostSummary>>(),
  setBudget: vi.fn<(monthlyUsd: number) => Promise<CostSummary>>(),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: { ...actual.api, costSummary: mocks.costSummary, setBudget: mocks.setBudget },
  };
});

/**
 * 成本面板 + 预算闸门（spec §19.3.5 / §19.5.1 / AC-55 / AC-56 / AC-63）。
 *
 * 全部用例围绕 `NULL ≠ 0`：未知成本不许变成 `$0.00`，不限额不许显示一条"剩余 $—"，
 * 账本缺口只提示不报警。这几条一旦混了，面板就会对着月度花费说谎——而它存在的
 * 唯一理由就是那句数字可信。
 */

function summary(overrides: Partial<CostSummary> = {}): CostSummary {
  return {
    window: '30d',
    from: '2026-01-11T00:00:00Z',
    to: '2026-02-10T00:00:00Z',
    spentUsd: 3.42,
    knownRuns: 18,
    unknownRuns: 0,
    byKind: { run: 2.91, ask_deep: 0.51 },
    byModel: [{ model: 'claude-sonnet', usd: 3.42, runs: 18 }],
    budget: { monthlyUsd: 25, remainingUsd: 21.58, resetAt: '2026-03-01T00:00:00Z', state: 'ok' },
    ledgerGap: 0,
    ...overrides,
  };
}

/** 从模板里取前缀，避免在测试里抄一份中文（文案改动不该让测试变红）。 */
const remainingPrefix = copy.cost.budgetRemaining.slice(0, copy.cost.budgetRemaining.indexOf('{'));

function renderPanel() {
  return render(<CostPanel open onOpenChange={vi.fn()} />);
}

beforeEach(() => {
  mocks.costSummary.mockReset();
  mocks.setBudget.mockReset();
});

describe('CostPanel · 汇总与空账本', () => {
  it('已知花费按两位小数展示，未知次数单独成句（不折算成 $0.00）', async () => {
    mocks.costSummary.mockResolvedValue(summary({ unknownRuns: 4 }));
    renderPanel();

    expect(await screen.findByText(fill(copy.cost.spent, { usd: '3.42' }))).toBeInTheDocument();
    expect(screen.getByText('另有 4 次成本未知')).toBeInTheDocument();
    // 关键反例：4 次未知绝不能被"补贴"成 $0.00 的已知花费。
    expect(screen.queryByText(fill(copy.cost.spent, { usd: '0.00' }))).toBeNull();
    expect(mocks.costSummary).toHaveBeenCalledWith('30d');
  });

  it('账本没有记录时给"暂无成本记录"，而不是一行 $0.00', async () => {
    mocks.costSummary.mockResolvedValue(
      summary({ spentUsd: 0, knownRuns: 0, unknownRuns: 0, byKind: {}, byModel: [] }),
    );
    renderPanel();

    expect(await screen.findByText(copy.cost.empty)).toBeInTheDocument();
  });

  it('ledgerGap > 0 时提示账本缺行（报告不受影响）', async () => {
    mocks.costSummary.mockResolvedValue(summary({ ledgerGap: 2 }));
    renderPanel();

    expect(await screen.findByText(fill(copy.cost.gap, { count: 2 }))).toBeInTheDocument();
  });

  it('加载失败时给一句可读的错误 + 重试，重试成功后就地恢复', async () => {
    mocks.costSummary.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(summary());
    const user = userEvent.setup();
    renderPanel();

    expect(await screen.findByRole('alert')).toHaveTextContent(copy.cost.loadFailed);

    await user.click(screen.getByRole('button', { name: copy.chart.retry }));
    expect(await screen.findByText(fill(copy.cost.spent, { usd: '3.42' }))).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});

describe('CostPanel · 预算（AC-63 四态）', () => {
  it('unlimited：只说"未设上限"，不渲染剩余额度行', async () => {
    mocks.costSummary.mockResolvedValue(
      summary({ budget: { monthlyUsd: 0, remainingUsd: null, resetAt: null, state: 'unlimited' } }),
    );
    renderPanel();

    expect(await screen.findByText(copy.cost.budgetUnlimited)).toBeInTheDocument();
    // 剩余额度为 null 时显示"剩余 $—"会被读成额度用光，所以整行不出现。
    expect(screen.queryByText(remainingPrefix, { exact: false })).toBeNull();
  });

  it('exceeded：状态行照实说"已超上限"，并给出剩余额度与重置时间', async () => {
    mocks.costSummary.mockResolvedValue(
      summary({
        budget: { monthlyUsd: 25, remainingUsd: 0, resetAt: '2026-03-01T00:00:00Z', state: 'exceeded' },
      }),
    );
    renderPanel();

    expect(await screen.findByText(copy.cost.state.exceeded)).toBeInTheDocument();
    expect(screen.getByText(remainingPrefix, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(copy.cost.budgetResetAt.slice(0, copy.cost.budgetResetAt.indexOf('{')), {
      exact: false,
    })).toBeInTheDocument();
  });

  it('保存预算：按数字提交、随后重取汇总并 toast', async () => {
    mocks.costSummary.mockResolvedValue(summary());
    mocks.setBudget.mockResolvedValue(summary());
    const user = userEvent.setup();
    renderPanel();

    // 按 role 取：预算区块本身也带同名 aria-label，只有输入框是 textbox。
    const input = await screen.findByRole('textbox', { name: copy.cost.budget });
    await waitFor(() => expect(input).toHaveValue('25'));

    await user.clear(input);
    await user.type(input, '40');
    await user.click(screen.getByRole('button', { name: copy.cost.save }));

    await waitFor(() => expect(mocks.setBudget).toHaveBeenCalledWith(40));
    // 保存后必须重取一次：预算写的是 settings，面板显示的剩余要与闸门用同一份事实。
    await waitFor(() => expect(mocks.costSummary).toHaveBeenCalledTimes(2));
  });
});

describe('BudgetGateDialog · 402 闸门（AC-56）', () => {
  it('把已花/上限/预估摊开，主按钮"仍然生成"交给 onOverride', async () => {
    const onOverride = vi.fn();
    const onCancel = vi.fn();
    const user = userEvent.setup();

    render(
      <BudgetGateDialog
        open
        spentUsd={25}
        budgetUsd={25}
        estimateUsd={0.21}
        onOverride={onOverride}
        onCancel={onCancel}
      />,
    );

    // Radix 的 sr-only 标题与可见标题是同一句话，所以断言落在 dialog 整体文本上。
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(fill(copy.cost.gateTitle, { spent: '25.00', budget: '25.00' }));
    expect(dialog).toHaveTextContent(fill(copy.cost.gateBody, { estimate: '0.21' }));

    await user.click(screen.getByRole('button', { name: copy.cost.gateOverride }));
    expect(onOverride).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('无法预估时显示"无法预估本次成本"，不伪造 $0.00', async () => {
    render(
      <BudgetGateDialog
        open
        spentUsd={25}
        budgetUsd={25}
        estimateUsd={null}
        onOverride={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(await screen.findByText(copy.cost.estimateUnknown)).toBeInTheDocument();
  });
});
