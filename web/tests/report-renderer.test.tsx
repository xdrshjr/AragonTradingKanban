import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ReportRenderer, UnknownBlock } from '@/components/report/ReportRenderer';
import { useUiStore } from '@/lib/store';
import type { Block, SourceRef } from '@/lib/types';

const SOURCES: SourceRef[] = [
  { id: 'S1', title: '行情与估值快照', publisher: 'akshare', publishedAt: '2026-02-10', url: 'https://example.com/s1' },
  { id: 'S2', title: '2025 年三季报', publisher: '上交所', publishedAt: '2025-10-28', url: null },
];

/**
 * AC-6: all 12 block types render, and an unknown one degrades without taking the
 * page down (spec §5.2).
 */
describe('ReportRenderer', () => {
  it('renders a paragraph with an inline citation chip', async () => {
    const blocks: Block[] = [{ type: 'paragraph', text: '营收同比 +15.2%，主要来自直营渠道改革 [S1]。' }];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText(/15.2%/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查看来源 S1/ })).toBeInTheDocument();
  });

  it('renders heading levels 2/3/4', () => {
    const blocks: Block[] = [
      { type: 'heading', level: 2, text: '核心逻辑' },
      { type: 'heading', level: 3, text: '收入拆解' },
      { type: 'heading', level: 4, text: '跟踪节奏' },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByRole('heading', { level: 2, name: '核心逻辑' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: '收入拆解' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 4, name: '跟踪节奏' })).toBeInTheDocument();
  });

  it('renders bullets with source refs', () => {
    const blocks: Block[] = [
      { type: 'bullets', items: [{ text: '直营占比 46%', sourceRefs: ['S2'] }, { text: '批价企稳' }] },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('直营占比 46%')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /查看来源 S2/ })).toBeInTheDocument();
  });

  it('renders all three callout tones', () => {
    const blocks: Block[] = [
      { type: 'callout', tone: 'info', title: '说明标题', text: '说明内容' },
      { type: 'callout', tone: 'warn', title: '警告标题', text: '警告内容' },
      { type: 'callout', tone: 'insight', title: '洞察标题', text: '洞察内容' },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('说明标题')).toBeInTheDocument();
    expect(screen.getByText('警告标题')).toBeInTheDocument();
    expect(screen.getByText('洞察标题')).toBeInTheDocument();
  });

  it('renders a metrics block with delta arrows instead of colour alone', () => {
    const blocks: Block[] = [
      {
        type: 'metrics',
        items: [
          { label: 'PE(TTM)', value: '24.1×', deltaPct: -6.2, tone: 'pos', sourceRefs: ['S1'] },
          { label: '营收同比', value: '+15.2%', deltaPct: 2.4, tone: 'pos' },
        ],
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('24.1×')).toBeInTheDocument();
    expect(screen.getByText(/▼ 6\.2%/)).toBeInTheDocument();
    expect(screen.getByText(/▲ 2\.4%/)).toBeInTheDocument();
  });

  it('renders a table with right-aligned numeric columns', () => {
    const blocks: Block[] = [
      {
        type: 'table',
        caption: '收入与利润',
        columns: [
          { key: 'period', label: '期间', align: 'left' },
          { key: 'revenue', label: '营收', align: 'right' },
        ],
        rows: [{ period: '2025Q3', revenue: '373.5' }],
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('收入与利润')).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '373.5' })).toBeInTheDocument();
  });

  it('renders a line chart as inline SVG with an accessible label', () => {
    const blocks: Block[] = [
      {
        type: 'chart',
        chart: {
          kind: 'line',
          title: '近 5 日收盘',
          xAxis: { type: 'category', labels: ['a', 'b', 'c', 'd', 'e'] },
          series: [{ name: '收盘价', data: [1, 2, 3, 2, 4] }],
          unit: 'CNY',
          note: null,
        },
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByRole('img', { name: /近 5 日收盘/ })).toBeInTheDocument();
    expect(screen.getByText('近 5 日收盘')).toBeInTheDocument();
  });

  it('degrades an unsupported chart kind to a data table', () => {
    const blocks: Block[] = [
      {
        type: 'chart',
        chart: {
          kind: 'waterfall',
          title: '瀑布图',
          xAxis: { type: 'category', labels: ['a', 'b'] },
          series: [{ name: '变化', data: [1, 2] }],
        },
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('瀑布图')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '变化' })).toBeInTheDocument();
  });

  it('renders a quote with its attribution', () => {
    const blocks: Block[] = [
      { type: 'quote', text: '公司将继续推进渠道改革。', attribution: '业绩说明会', sourceRefs: ['S2'] },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('公司将继续推进渠道改革。')).toBeInTheDocument();
    expect(screen.getByText(/业绩说明会/)).toBeInTheDocument();
  });

  it('renders a timeline whose ISO dates can jump to the chart', async () => {
    const focusChartRange = vi.fn();
    useUiStore.setState({ focusChartRange, klineDrawerOpen: false });

    const blocks: Block[] = [
      {
        type: 'timeline',
        events: [
          { date: '2025-08-14', title: '中期分红落地', detail: '每股派现 23.96 元' },
          { date: '2025Q3', title: '三季报', detail: null },
        ],
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('中期分红落地')).toBeInTheDocument();

    await userEvent.click(screen.getByTitle(/在 K 线中定位 2025-08-14/));
    expect(focusChartRange).toHaveBeenCalledTimes(1);
  });

  it('renders comparables as peer cards', () => {
    const blocks: Block[] = [
      {
        type: 'comparables',
        peers: [
          { name: '五粮液', symbol: '000858.SZ', pe: 18.2, pb: 4.1, growthPct: 8.1, note: null },
          { name: '泸州老窖', symbol: '000568.SZ', pe: null, pb: null, growthPct: null },
        ],
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('五粮液')).toBeInTheDocument();
    expect(screen.getByText('18.2×')).toBeInTheDocument();
  });

  it('renders a scenario block with probabilities', () => {
    const blocks: Block[] = [
      {
        type: 'scenario',
        scenarios: [
          { name: 'bear', targetPrice: 1420, probability: 0.25, assumptions: '批价跌破 1900' },
          { name: 'base', targetPrice: 1980, probability: 0.55, assumptions: '直营占比 48%' },
          { name: 'bull', targetPrice: 2340, probability: 0.2, assumptions: '提价落地' },
        ],
      },
    ];
    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('悲观')).toBeInTheDocument();
    expect(screen.getByText('1,980.00')).toBeInTheDocument();
    expect(screen.getByText('55%')).toBeInTheDocument();
  });

  it('renders a divider', () => {
    const { container } = render(<ReportRenderer blocks={[{ type: 'divider' }]} sources={SOURCES} />);
    expect(container.querySelector('hr')).not.toBeNull();
  });

  it('degrades an unknown block type without crashing the page', () => {
    const blocks = [
      { type: 'paragraph', text: '正常段落' },
      { type: 'hologram', text: '未来的块类型' },
    ] as unknown as Block[];

    render(<ReportRenderer blocks={blocks} sources={SOURCES} />);
    expect(screen.getByText('正常段落')).toBeInTheDocument();
    expect(screen.getByTestId('unknown-block')).toBeInTheDocument();
    expect(screen.getByText('hologram')).toBeInTheDocument();
  });

  it('UnknownBlock can be rendered on its own', () => {
    render(<UnknownBlock type="mystery" />);
    expect(screen.getByTestId('unknown-block')).toHaveTextContent('mystery');
  });

  it('renders an empty block list without error', () => {
    const { container } = render(<ReportRenderer blocks={[]} sources={[]} />);
    expect(container.querySelector('.report-prose')).not.toBeNull();
  });
});
