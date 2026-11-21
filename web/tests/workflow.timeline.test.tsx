import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { WorkflowTimeline } from '@/components/generation/WorkflowTimeline';
import type { WorkflowProgressView } from '@/components/generation/useGenerationStream';
import { WorkflowMeta } from '@/components/report/WorkflowMeta';
import type { QualityWorkflow } from '@/lib/types';

function view(overrides: Partial<WorkflowProgressView> = {}): WorkflowProgressView {
  return {
    templateName: '标准五段式研究',
    version: 3,
    fallback: false,
    total: 3,
    doneCount: 1,
    items: [
      { key: 'S1', kind: 'step', title: '核对事实底座', level: 1, upstream: [], state: 'done', note: null },
      { key: 'S2', kind: 'step', title: '技术面', level: 2, upstream: ['S1'], state: 'running', note: null },
      { key: 'G1', kind: 'gate', title: '估值前核对', level: 3, upstream: ['S2'], state: 'pending', note: null },
    ],
    ...overrides,
  };
}

describe('WorkflowTimeline（AC-W32）', () => {
  it('✓ 完成 / ● 进行中 / ○ 待定，并显示 done/total', () => {
    render(<WorkflowTimeline workflow={view()} />);
    expect(screen.getByTestId('wf-item-S1')).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('wf-item-S2')).toHaveAttribute('data-state', 'running');
    expect(screen.getByTestId('wf-item-G1')).toHaveAttribute('data-state', 'pending');
    expect(screen.getByText('1/3')).toBeInTheDocument();
    // 状态不只靠颜色：每一行都有文字标签。
    expect(within(screen.getByTestId('wf-item-S2')).getByText('进行中')).toBeInTheDocument();
    expect(within(screen.getByTestId('wf-item-G1')).getByText('待定')).toBeInTheDocument();
  });

  it('检查点以"检查点："前缀与分隔线样式呈现', () => {
    render(<WorkflowTimeline workflow={view()} />);
    expect(within(screen.getByTestId('wf-item-G1')).getByText(/检查点：/)).toBeInTheDocument();
  });

  it('无标记到达时全部待定，且从不出现"失败"字样', () => {
    const pending = view({
      doneCount: 0,
      items: view().items.map((item) => ({ ...item, state: 'pending' as const })),
    });
    render(<WorkflowTimeline workflow={pending} />);
    expect(screen.getAllByText('待定')).toHaveLength(3);
    expect(screen.queryByText(/失败/)).toBeNull();
  });

  it('回退时标题为"标准流程"', () => {
    render(<WorkflowTimeline workflow={view({ fallback: true })} />);
    expect(screen.getByTestId('workflow-timeline').textContent).toContain('标准流程');
    expect(screen.getByTestId('workflow-timeline').textContent).not.toContain('v3');
  });
});

const QUALITY: QualityWorkflow = {
  templateName: '标准五段式研究',
  version: 3,
  source: 'published',
  adherence: 'partial',
  steps: { total: 6, started: 6, done: 4, skipped: 1 },
  gates: { total: 2, done: 1 },
};

describe('WorkflowMeta（报告元信息）', () => {
  it('显示「按『名称 vN』研究」，悬浮给出 AI 汇报了几步', () => {
    render(<WorkflowMeta workflow={QUALITY} />);
    const node = screen.getByTestId('workflow-meta');
    expect(node.textContent).toContain('按『标准五段式研究 v3』研究');
    expect(node).toHaveAttribute('title', 'AI 汇报了 6/8 步');
  });

  it('旧报告（无 quality.workflow）不渲染', () => {
    const { container } = render(<WorkflowMeta workflow={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('adherence=none 或回退内置用弱色，不用警示色', () => {
    render(<WorkflowMeta workflow={{ ...QUALITY, adherence: 'none', steps: null, gates: null }} />);
    const node = screen.getByTestId('workflow-meta');
    expect(node.className).toContain('text-tertiary');
    expect(node.className).not.toMatch(/warn|danger/);
    expect(node).toHaveAttribute('title', 'AI 未汇报步骤进度');
  });

  it('回退到内置流程时显示"标准流程"且不带版本', () => {
    render(
      <WorkflowMeta workflow={{ ...QUALITY, source: 'builtin-code', fallbackReason: 'compile_failed', version: null }} />,
    );
    expect(screen.getByTestId('workflow-meta').textContent).toContain('按『标准流程』研究');
  });
});
