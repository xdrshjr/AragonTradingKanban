import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { CompareView } from '@/components/report/CompareView';
import { DiffBadge } from '@/components/report/DiffBadge';
import { copy } from '@/lib/copy';
import type { DiffResult, MetricDelta } from '@/lib/types';

function makeDiff(overrides: Partial<DiffResult> = {}): DiffResult {
  return {
    a: 'rp_a',
    b: 'rp_b',
    statusChanged: null,
    ratingChanged: null,
    metricsDelta: [],
    sectionsAdded: [],
    sectionsRemoved: [],
    blocksChanged: [],
    summary: '',
    ...overrides,
  };
}

function metricDelta(overrides: Partial<MetricDelta> & Pick<MetricDelta, 'label'>): MetricDelta {
  return {
    before: null,
    after: null,
    deltaPct: null,
    direction: 'flat',
    polarity: 'neutral',
    tone: 'neutral',
    ...overrides,
  };
}

/** 指标行的「变化」单元格：颜色断言只在这里做，避免误取卡片/表头的 class。 */
function deltaCell(label: string): HTMLElement {
  const header = screen.getByRole('rowheader', { name: label });
  const row = header.closest('tr');
  if (!row) throw new Error(`row for ${label} not found`);
  const cells = within(row).getAllByRole('cell');
  const cell = cells[cells.length - 1];
  if (!cell) throw new Error(`delta cell for ${label} not found`);
  return cell;
}

/**
 * AC-34 前端 / AC-42：指标语义色**只**来自服务端给出的 `tone`（`direction × polarity`）。
 * 这几条用例就是这条规矩的守卫——它们拿"方向与颜色相反"的样本去证明客户端没有
 * 从 `deltaPct` 的正负重新猜一遍颜色。
 */
describe('CompareView · 指标语义色（AC-42）', () => {
  it('看多指标下跌 → 用服务端的 down（坏）色，并给出措辞而不只靠颜色', () => {
    render(
      <CompareView
        loading={false}
        onClose={vi.fn()}
        diff={makeDiff({
          metricsDelta: [
            metricDelta({
              label: '毛利率',
              before: 65.5,
              after: 62.1,
              deltaPct: -5.19,
              direction: 'down',
              polarity: 'bullish',
              tone: 'down',
            }),
          ],
        })}
      />,
    );

    // 真表格 + sr-only 说明（AC-36）
    expect(screen.getByRole('table', { name: copy.compare.metricsDelta })).toBeInTheDocument();

    const cell = deltaCell('毛利率');
    expect(cell.className).toContain('chart-down');
    expect(cell.className).not.toContain('chart-up');
    expect(cell).toHaveTextContent(copy.compare.tone.down);
    expect(cell).toHaveTextContent('▼');
  });

  it('看空指标上涨 → 同样用坏色（证明颜色不是从符号推出来的）', () => {
    render(
      <CompareView
        loading={false}
        onClose={vi.fn()}
        diff={makeDiff({
          metricsDelta: [
            metricDelta({
              label: 'PE(TTM)',
              before: 28.1,
              after: 31.4,
              deltaPct: 11.74,
              direction: 'up',
              polarity: 'bearish',
              tone: 'down',
            }),
            metricDelta({
              label: '营业收入',
              before: 100,
              after: 115.2,
              deltaPct: 15.2,
              direction: 'up',
              polarity: 'bullish',
              tone: 'up',
            }),
          ],
        })}
      />,
    );

    const pe = deltaCell('PE(TTM)');
    // 数字往上（▲），语义却是坏消息（▼ 色）——两者并存才是对的
    expect(pe).toHaveTextContent('▲');
    expect(pe.className).toContain('chart-down');
    expect(pe.className).not.toContain('chart-up');
    expect(pe).toHaveTextContent(copy.compare.tone.down);

    // 同样上涨，看多指标才是好色：同一屏里两种结论并存，说明来源是 tone
    const revenue = deltaCell('营业收入');
    expect(revenue.className).toContain('chart-up');
    expect(revenue.className).not.toContain('chart-down');
    expect(revenue).toHaveTextContent(copy.compare.tone.up);
  });

  it('neutral 色 → 既不是涨色也不是跌色', () => {
    render(
      <CompareView
        loading={false}
        onClose={vi.fn()}
        diff={makeDiff({
          metricsDelta: [
            metricDelta({
              label: '股息率',
              before: 3.1,
              after: 3.1,
              deltaPct: 0,
              direction: 'flat',
              polarity: 'neutral',
              tone: 'neutral',
            }),
          ],
        })}
      />,
    );

    const cell = deltaCell('股息率');
    expect(cell.className).not.toContain('chart-up');
    expect(cell.className).not.toContain('chart-down');
    expect(cell.className).toContain('--text-tertiary');
    expect(cell).toHaveTextContent(copy.compare.tone.neutral);
  });
});

describe('CompareView · 变更块清单', () => {
  it('列出 修改/新增 块，点击回调带 blockIdB 与 sectionId', async () => {
    const user = userEvent.setup();
    const onJumpToBlock = vi.fn();
    render(
      <CompareView
        loading={false}
        onClose={vi.fn()}
        onJumpToBlock={onJumpToBlock}
        diff={makeDiff({
          blocksChanged: [
            { sectionId: 'business', blockType: 'metrics', change: 'modified', blockIdA: 'blk_12', blockIdB: 'blk_18' },
            { sectionId: 'risks', blockType: 'callout', change: 'added', blockIdA: null, blockIdB: 'blk_21' },
            { sectionId: 'valuation', blockType: 'chart', change: 'removed', blockIdA: 'blk_9', blockIdB: null },
          ],
        })}
      />,
    );

    const modified = screen.getByRole('button', { name: /business/ });
    expect(modified).toHaveTextContent(copy.compare.change.modified);
    expect(modified).toHaveTextContent('metrics');

    await user.click(modified);
    expect(onJumpToBlock).toHaveBeenCalledWith('blk_18', 'business');

    // 新增块走新版 id
    await user.click(screen.getByRole('button', { name: /risks/ }));
    expect(onJumpToBlock).toHaveBeenCalledWith('blk_21', 'risks');

    // 删除块在新版里没有 blockIdB → 传 null，让上层只定位章节
    await user.click(screen.getByRole('button', { name: /valuation/ }));
    expect(onJumpToBlock).toHaveBeenCalledWith(null, 'valuation');
    expect(onJumpToBlock).toHaveBeenCalledTimes(3);
  });
});

describe('CompareView · 评级与章节', () => {
  it('评级变化显示 旧→新 的立场中文名，章节增删渲染为标签', () => {
    render(
      <CompareView
        loading={false}
        onClose={vi.fn()}
        aVersion={1}
        bVersion={2}
        diff={makeDiff({
          ratingChanged: { from: { stance: 'buy', target: '2100' }, to: { stance: 'hold', target: '1980' } },
          sectionsAdded: ['风险提示'],
          sectionsRemoved: ['附录'],
          summary: '评级由买入下调为持有',
        })}
      />,
    );

    expect(screen.getByText(copy.report.stance.buy)).toBeInTheDocument();
    expect(screen.getByText(copy.report.stance.hold)).toBeInTheDocument();
    expect(screen.getByText('风险提示')).toBeInTheDocument();
    expect(screen.getByText('附录')).toBeInTheDocument();
    // 版本号来自 props，不是 diff 的 id
    expect(screen.getByText('旧版 1')).toBeInTheDocument();
    expect(screen.getByText('新版 2')).toBeInTheDocument();
  });
});

describe('CompareView · 状态', () => {
  it('空 diff 渲染 no-changes 文案而不是空壳', () => {
    const { rerender } = render(<CompareView diff={makeDiff({ summary: '两个版本没有结构性差异' })} loading={false} onClose={vi.fn()} />);
    expect(screen.getByText(copy.compare.noChanges)).toBeInTheDocument();

    rerender(<CompareView diff={null} loading={false} onClose={vi.fn()} />);
    expect(screen.getByText(copy.compare.noChanges)).toBeInTheDocument();
  });

  it('loading 渲染骨架（role=status），error 渲染 loadFailed + 传入信息', () => {
    const { rerender } = render(<CompareView diff={null} loading onClose={vi.fn()} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByText(copy.compare.noChanges)).not.toBeInTheDocument();

    rerender(<CompareView diff={null} loading={false} error="HTTP 500 服务不可用" onClose={vi.fn()} />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(copy.compare.loadFailed);
    expect(alert).toHaveTextContent('HTTP 500 服务不可用');
  });
});

describe('CompareView · 键盘（AC-36）', () => {
  it('Esc 关闭并把焦点还给触发元素', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    function Harness() {
      const triggerRef = useRef<HTMLButtonElement | null>(null);
      return (
        <div>
          <button type="button" ref={triggerRef}>
            {copy.compare.open}
          </button>
          <CompareView
            diff={makeDiff({ metricsDelta: [metricDelta({ label: '毛利率', before: 65.5, after: 62.1, deltaPct: -5.19, tone: 'down' })] })}
            loading={false}
            onClose={onClose}
            triggerRef={triggerRef}
          />
        </div>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole('button', { name: copy.compare.open });

    // 挂载后焦点被移进面板（键盘用户打开比较后不会落在背后的报告里）
    const region = screen.getByRole('region', { name: copy.compare.title });
    expect(region).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(trigger).toHaveFocus();
  });
});

describe('DiffBadge', () => {
  it('显示结构性变化条数，点击回调一次', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<DiffBadge count={3} onClick={onClick} />);
    const badge = screen.getByRole('button', { name: `${copy.compare.title} · 3 处` });
    expect(badge).toHaveTextContent('3 处');
    await user.click(badge);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('0 处时退回动作文案，且不渲染可点击按钮', () => {
    render(<DiffBadge count={0} />);
    expect(screen.getByText(copy.compare.open)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
