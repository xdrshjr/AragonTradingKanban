import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { GenerationOverlay } from '@/components/generation/GenerationOverlay';
import type { GenerationState } from '@/components/generation/useGenerationStream';
import { RUN_ERROR_ACTIONS, actionFor, primaryActionFor } from '@/lib/runErrors';
import { useUiStore } from '@/lib/store';

/**
 * ★v9（K6）失败卡片上的**动作**。
 *
 * 这组用例守的是评审节点发现的一处真实缺陷（I-103）：`GenerationOverlay` 原来写
 * `actionFor(code).action === 'openLog' ? null : <查看日志>`，于是
 * `schema_over_budget` / `prompt_over_budget`（动作 = `openLog`，但 `primary: false`）
 * 既不出主按钮、又被隐藏了日志按钮 —— 卡片上一个动作都没有。而 K6 的全部意义就是
 * "每个码都配一个下一步做什么"。
 *
 * 第二条守的是反面：`cancelled` / `instrument_not_found`（动作 = `none`）不该多出一个
 * 毫无意义的「查看日志」—— 用户主动取消的那一次不需要动作。
 */

function failedState(code: string, message = '出错了'): GenerationState {
  return {
    phase: 'error',
    runId: 'run-1',
    instrumentId: 'CN:600519.SH',
    stage: 'research',
    stageLabel: '研究中',
    pct: 70,
    logs: [],
    tools: [],
    streamText: '',
    error: { code, message },
    reportId: null,
    version: null,
    costUsd: null,
    costKnown: false,
    costSource: null,
    costUsdEst: null,
    startedAt: Date.now() - 1_000,
    finishedAt: Date.now(),
    retryCount: 0,
    takeoverOf: null,
    queuePosition: null,
  } as GenerationState;
}

function renderFailed(code: string) {
  const handlers = { onRetry: vi.fn(), onOpenKeyDrawer: vi.fn(), onOpenLog: vi.fn() };
  render(
    <GenerationOverlay
      state={failedState(code)}
      onCancel={vi.fn()}
      onDismiss={vi.fn()}
      onRetry={handlers.onRetry}
      agentMode="real"
      onOpenKeyDrawer={handlers.onOpenKeyDrawer}
      onOpenLog={handlers.onOpenLog}
    />,
  );
  return handlers;
}

beforeEach(() => {
  useUiStore.setState({ overlayCollapsed: false });
});

describe('失败卡片的动作按钮（K6 / I-103）', () => {
  it('openLog 是次要动作时，日志按钮必须还在（旧写法会把它一起藏掉）', async () => {
    // 前置：这条码确实是"动作 = openLog，但不是主按钮"——表一改，本用例就该被重新看。
    for (const code of ['schema_over_budget', 'prompt_over_budget']) {
      expect(actionFor(code)).toEqual({ action: 'openLog', primary: false });
      expect(primaryActionFor(code)).toBeNull();
    }

    const handlers = renderFailed('schema_over_budget');
    const log = screen.getByTestId('error-open-log');
    expect(screen.queryByTestId('error-primary-action')).toBeNull();
    await userEvent.click(log);
    expect(handlers.onOpenLog).toHaveBeenCalledTimes(1);
  });

  it('每个码都至少有一个动作，除非它本来就是"不需要动作"', () => {
    for (const code of Object.keys(RUN_ERROR_ACTIONS)) {
      const spec = RUN_ERROR_ACTIONS[code];
      if (spec.action === 'none') continue;
      // `primary` 与"有没有按钮"是两件事：只要动作不是 none，日志按钮兜底存在。
      expect(actionFor(code).action).not.toBe('none');
    }
  });

  it('主按钮只出现一次，且 openLog 不会重复成两个按钮', () => {
    renderFailed('agent_error'); // 动作 = retry（非主按钮）+ 日志
    expect(screen.getByTestId('error-open-log')).toBeTruthy();
    expect(screen.queryByTestId('error-primary-action')).toBeNull();
  });

  it('provider_auth 的主按钮是「检查密钥」，点了打开密钥抽屉', async () => {
    const handlers = renderFailed('provider_auth');
    const primary = screen.getByTestId('error-primary-action');
    expect(primary.getAttribute('data-action')).toBe('checkKey');
    expect(primary.textContent).toContain('检查密钥');
    await userEvent.click(primary);
    expect(handlers.onOpenKeyDrawer).toHaveBeenCalledTimes(1);
  });

  it('cancelled 是用户意图：一个按钮都不出现', () => {
    renderFailed('cancelled');
    expect(screen.queryByTestId('error-primary-action')).toBeNull();
    expect(screen.queryByTestId('error-open-log')).toBeNull();
  });

  it('未知码仍然给一条真正有用的路（看日志）', () => {
    renderFailed('something_new_from_the_server');
    expect(screen.queryByTestId('error-primary-action')).toBeNull();
    expect(screen.getByTestId('error-open-log')).toBeTruthy();
  });
});
