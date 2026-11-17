import { createElement } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { generationReducer, initialGenerationState } from '@/components/generation/useGenerationStream';
import { GenerationOverlay } from '@/components/generation/GenerationOverlay';
import { BudgetGateDialog } from '@/components/cost/CostPanel';
import { copy, costSourceLabel, fill } from '@/lib/copy';
import { costLabel } from '@/lib/format';
import type { CostSource } from '@/lib/types';

/**
 * 成本三态的界面措辞（spec §20.3.5 / H7；AC-77 的界面侧）。
 *
 * 面板、生成卡片、报告元信息共用 `costSourceLabel()` 一个映射——这是"估算永不被说成
 * 回执"（H-6）唯一能机器判定的地方。三个标签必须**互不相同**：如果 `estimated` 与
 * `reported` 显示同一句话，账本上那两个数就没有意义了。
 *
 * 这个文件是 `.ts`（与 §20.4 的文件表一致），所以用 `createElement` 而不是 JSX——
 * 一份"为了写测试而改文件名"的偏差不值得，而 JSX 只在这里是糖。
 */

const SOURCES: CostSource[] = ['reported', 'estimated', 'unknown'];

function overlay(payload: Record<string, unknown>) {
  const state = generationReducer(
    generationReducer(initialGenerationState, { type: 'queued', runId: 'r' }),
    { type: 'event', event: { seq: 1, type: 'run.done', at: 1, payload } },
  );
  return render(
    createElement(GenerationOverlay, {
      state,
      onCancel: () => {},
      onDismiss: () => {},
      onRetry: () => {},
      agentMode: 'real' as const,
    }),
  );
}

describe('costSourceLabel · 三态三种措辞', () => {
  it('maps each source to its own label', () => {
    const labels = SOURCES.map((source) => costSourceLabel(source));
    expect(labels).toEqual([copy.cost.confirmed, copy.cost.estimatedLabel, copy.cost.unknownLabel]);
    expect(new Set(labels).size).toBe(3);
  });

  it('never calls an estimate "confirmed"', () => {
    expect(costSourceLabel('estimated')).not.toBe(copy.cost.confirmed);
    expect(costSourceLabel(null)).toBe(copy.cost.unknownLabel);
    expect(costSourceLabel(undefined)).toBe(copy.cost.unknownLabel);
  });

  it('keeps the "including estimate" wording explicit', () => {
    expect(fill(copy.cost.includingEstimated, { usd: '0.02' })).toBe('含估算 $0.02');
    expect(copy.cost.estimatedLabel).not.toContain('含估算');
  });
});

describe('GenerationOverlay · 成本三态与重试徽标', () => {
  it('labels a reported cost as confirmed', () => {
    overlay({ reportId: 'rep-1', version: 1, costUsd: 0.42, costKnown: true, costSource: 'reported' });
    const line = screen.getByTestId('generation-cost');
    expect(line.textContent).toContain(copy.cost.confirmed);
    expect(line.textContent).toContain('$0.42');
    expect(line.textContent).not.toContain(copy.cost.estimatedLabel);
  });

  it('labels an estimated cost as an estimate and shows the number twice', () => {
    overlay({
      reportId: 'rep-1',
      version: 1,
      costUsd: null,
      costKnown: false,
      costSource: 'estimated',
      costUsdEst: 0.0172,
    });
    const line = screen.getByTestId('generation-cost');
    expect(line.textContent).toContain(copy.cost.estimatedLabel);
    expect(line.textContent).toContain('$0.02');
    expect(line.textContent).toContain(fill(copy.cost.includingEstimated, { usd: '0.02' }));
  });

  it('says "unknown" rather than $0.00 when there is no number', () => {
    overlay({ reportId: 'rep-1', version: 1, costUsd: null, costKnown: false, costSource: 'unknown' });
    const line = screen.getByTestId('generation-cost');
    expect(line.textContent).toContain(copy.cost.unknownLabel);
    expect(line.textContent).not.toContain('$');
  });

  it('shows a retry badge only when the engine actually retried', () => {
    const withRetry = generationReducer(
      generationReducer(initialGenerationState, { type: 'queued', runId: 'r' }),
      { type: 'event', event: { seq: 2, type: 'run.retry', at: 2, payload: { attempt: 1, maxRetries: 6 } } },
    );
    const view = render(
      createElement(GenerationOverlay, {
        state: withRetry,
        onCancel: () => {},
        onDismiss: () => {},
        onRetry: () => {},
        agentMode: 'real' as const,
      }),
    );
    expect(screen.getByTitle(copy.generation.retrying)).toBeTruthy();
    view.unmount();
  });
});

describe('BudgetGateDialog · 含估算的闸门说明', () => {
  it('shows the server note beside the override button', () => {
    render(
      createElement(BudgetGateDialog, {
        open: true,
        spentUsd: 24.9,
        budgetUsd: 25,
        estimateUsd: 0.72,
        note: '其中 $3.10 为按 token × 单价估算，不是引擎回执；已确认部分为 $21.80。',
        onOverride: () => {},
        onCancel: () => {},
      }),
    );
    expect(screen.getByText(/按 token × 单价估算/)).toBeTruthy();
    expect(screen.getByRole('button', { name: copy.cost.gateOverride })).toBeTruthy();
  });

  it('omits the note when the server did not send one', () => {
    render(
      createElement(BudgetGateDialog, {
        open: true,
        spentUsd: 1,
        budgetUsd: 1,
        estimateUsd: null,
        onOverride: () => {},
        onCancel: () => {},
      }),
    );
    expect(screen.queryByText(/按 token × 单价估算/)).toBeNull();
    expect(screen.getByText(copy.cost.estimateUnknown)).toBeTruthy();
  });
});

describe('costLabel · 报告版本行的三态（quality.cost）', () => {
  it('trusts quality.cost over the legacy two-state columns', () => {
    // 一份 `estimated` 的报告：`costUsd` 是 NULL，`costKnown` 是 false，
    // 但金额在 `quality.cost.estimatedUsd` —— 不读它就只能说"成本未知"，那是漏报。
    expect(costLabel(null, false, { source: 'estimated', estimatedUsd: 0.0172 })).toBe(
      `${copy.cost.estimatedLabel} $0.02`,
    );
    expect(costLabel(null, false, { source: 'estimated', estimatedUsd: 0.0172 })).not.toContain(
      copy.cost.confirmed,
    );
  });

  it('keeps a reported cost verbatim', () => {
    expect(costLabel(1.5, true, { source: 'reported', reportedUsd: 1.5 })).toBe(
      `${copy.cost.confirmed} $1.50`,
    );
  });

  it('falls back to the two-state columns for early reports without quality.cost', () => {
    // §19.6.3：老报告不回填，所以缺键是合法状态，不能因此显示成"未知"或 $0.00。
    expect(costLabel(0.42, true, undefined)).toBe('$0.42');
    expect(costLabel(null, false, undefined)).toBe(copy.cost.unknownLabel);
    expect(costLabel(0.42, true, { source: 'unknown' })).toBe(copy.cost.unknownLabel);
  });
});

describe('GenerationOverlay · 失败卡片的人话与重复告警（H3.5 / H-12）', () => {
  function failing(payload: Record<string, unknown>) {
    const state = generationReducer(
      generationReducer(initialGenerationState, { type: 'queued', runId: 'r' }),
      { type: 'event', event: { seq: 3, type: 'run.error', at: 3, payload } },
    );
    return render(
      createElement(GenerationOverlay, {
        state,
        onCancel: () => {},
        onDismiss: () => {},
        onRetry: () => {},
        agentMode: 'real' as const,
      }),
    );
  }

  it('renders the server hint instead of building its own sentence', () => {
    const hint = '稍等 1–2 分钟再试；若每次都这样，检查网络/代理。';
    failing({ code: 'provider_unavailable', message: 'x', hint, retryable: true });
    expect(screen.getByText(new RegExp(hint.slice(0, 8)))).toBeTruthy();
  });

  it('labels usage_error with a human tag', () => {
    failing({ code: 'usage_error', message: 'unknown option', hint: '看 argv.json' });
    expect(screen.getByText(new RegExp(copy.errors.usageRejected))).toBeTruthy();
  });

  it('switches to the stronger wording at three identical failures', () => {
    failing({ code: 'provider_unavailable', message: 'x', failureFingerprint: 'abc123abc123', repeatCount: 3 });
    expect(screen.getByTestId('generation-repeat').textContent).toBe(
      fill(copy.errors.sameCauseThrice, { count: 3 }),
    );
  });

  it('stays quiet below the threshold and when the count is absent', () => {
    const low = failing({ code: 'provider_unavailable', message: 'x', repeatCount: 2 });
    expect(screen.queryByTestId('generation-repeat')).toBeNull();
    low.unmount();
    failing({ code: 'provider_unavailable', message: 'x' });
    expect(screen.queryByTestId('generation-repeat')).toBeNull();
  });
});
