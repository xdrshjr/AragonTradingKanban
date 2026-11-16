import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_INDICATORS,
  loadIndicators,
  saveIndicators,
} from '@/components/chart/IndicatorToggles';
import { ReportPanel } from '@/components/report/ReportPanel';
import { SymbolRail } from '@/components/shell/SymbolRail';
import { SourceDrawer } from '@/components/report/SourceDrawer';
import { useUiStore } from '@/lib/store';
import type { ReportDetail, ReportSummary, SourceRef } from '@/lib/types';

/**
 * Interaction regressions found in review. Each case pins a behaviour the spec
 * asks for by name (§6.3 indicator persistence, §6.3 BOLL, §11.1 drawer Esc,
 * §11.1/AC-16 tab keyboard, AC-7 sources).
 */

const SOURCES: SourceRef[] = [
  { id: 'S1', title: '行情与估值快照', publisher: 'akshare', publishedAt: '2026-02-10', url: 'https://example.com/1' },
  { id: 'S2', title: '2025 年三季报', publisher: '上交所', publishedAt: '2025-10-28', url: null },
];

const SUMMARY: ReportSummary = {
  id: 'report-1',
  instrumentId: 'CN:600519.SH',
  version: 3,
  status: 'ready',
  title: '贵州茅台 投研报告',
  headline: '趋势偏强，估值处于中性区间',
  stance: 'buy',
  asOf: '2026-02-10T00:00:00Z',
  generatedAt: '2026-02-10T00:00:00Z',
  isLatest: true,
  pinned: false,
  archived: false,
  provisional: false,
  groundedRatio: 0.94,
  quality: { groundedRatio: 0.94, missingSections: [], prunedBlocks: 0, ungroundedCount: 3, source: 'aragon' },
  provider: 'openai',
  model: 'deepseek-flash',
  costUsd: null,
  costKnown: false,
  tokensIn: 100,
  tokensOut: 200,
  durationMs: 42000,
  docBytes: 4096,
};

const DETAIL: ReportDetail = {
  report: SUMMARY,
  doc: {
    schema: 'aragon.research.report/v1',
    instrument: { symbol: '600519.SH', name: '贵州茅台', market: 'CN', currency: 'CNY' },
    asOf: '2026-02-10T00:00:00Z',
    headline: '趋势偏强，估值处于中性区间',
    rating: { stance: 'buy', conviction: 3, horizon: '6-12m' },
    summary: '示例摘要',
    keyMetrics: [],
    sections: [
      { id: 'thesis', title: '核心逻辑', blocks: [{ type: 'paragraph', text: '核心段落' }] },
      { id: 'fundamentals', title: '基本面', blocks: [{ type: 'paragraph', text: '基本面段落' }] },
    ],
    risks: [],
    sources: SOURCES,
    disclaimer: '仅供研究参考。',
  },
};

beforeEach(() => {
  window.localStorage.clear();
  useUiStore.setState({
    sourceDrawer: { open: false, activeSourceId: null },
    commandPaletteOpen: false,
    klineDrawerOpen: false,
    overlayCollapsed: false,
  });
});

describe('indicator toggles', () => {
  it('starts from the documented defaults', () => {
    expect(loadIndicators()).toEqual(DEFAULT_INDICATORS);
    expect(DEFAULT_INDICATORS.boll).toBe(false);
  });

  it('round-trips through localStorage so a reload keeps the choice', () => {
    saveIndicators({ ...DEFAULT_INDICATORS, boll: true, volume: false });
    const restored = loadIndicators();
    expect(restored.boll).toBe(true);
    expect(restored.volume).toBe(false);
  });

  it('ignores a corrupt stored value instead of throwing', () => {
    window.localStorage.setItem('aragon.indicators.v1', '{not json');
    expect(loadIndicators()).toEqual(DEFAULT_INDICATORS);
  });
});

describe('source drawer', () => {
  it('closes on Escape', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ sourceDrawer: { open: true, activeSourceId: 'S1' } });
    render(<SourceDrawer sources={SOURCES} />);
    expect(screen.getByRole('dialog', { name: '来源' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(useUiStore.getState().sourceDrawer.open).toBe(false);
  });

  it('switching the active source goes through the store action', async () => {
    const user = userEvent.setup();
    useUiStore.setState({ sourceDrawer: { open: true, activeSourceId: 'S1' } });
    render(<SourceDrawer sources={SOURCES} />);

    await user.click(screen.getByRole('button', { name: /2025 年三季报/ }));
    expect(useUiStore.getState().sourceDrawer.activeSourceId).toBe('S2');
  });
});

describe('report panel', () => {
  it('switches section tabs with the arrow keys (§11.1 / AC-16)', async () => {
    const user = userEvent.setup();
    render(
      <ReportPanel
        instrumentName="贵州茅台"
        reports={[SUMMARY]}
        detail={DETAIL}
        onGenerate={() => undefined}
        onSelectVersion={() => undefined}
        onExport={() => undefined}
      />,
    );

    const tabs = screen.getByRole('tablist');
    const all = within(tabs).getByRole('tab', { name: '全部' });
    all.focus();
    await user.keyboard('{ArrowRight}');

    const thesis = within(screen.getByRole('tablist')).getByRole('tab', { name: '核心逻辑' });
    expect(thesis).toHaveAttribute('aria-selected', 'true');
    expect(thesis).toHaveFocus();
    // The visible body must be the matching tabpanel, not just a restyled div.
    expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'report-section-thesis');
  });


  it('★I-53：点顶层 `keyMetrics` 样本不能把正文变空白'
    , async () => {
      // `keyMetrics` 不是 `<sectionId>#<index>`。旧实现把 `sectionIdOf('keyMetrics')`
      // 原样当成章节去 `setActiveSection`，于是 `visibleSections` 过滤成空数组
      // —— 读者点一下徽标里的样本，整篇报告就消失了。
      const user = userEvent.setup();
      const withSample: ReportDetail = {
        report: {
          ...SUMMARY,
          quality: {
            ...SUMMARY.quality,
            grounding: {
              factClaims: 6,
              factMissing: 1,
              totalMissing: 1,
              projectionClaims: 0,
              derivedClaims: 0,
              identifierClaims: 0,
              skipped: null,
              samples: [
                {
                  blockId: 'keyMetrics',
                  path: 'keyMetrics[0].value',
                  raw: '1672.00',
                  text: '最新价 1672.00 元',
                },
              ],
            },
          },
        },
        doc: {
          ...DETAIL.doc,
          keyMetrics: [{ label: '最新价', value: '1672.00' }],
        },
      };

      render(
        <ReportPanel
          instrumentName="贵州茅台"
          reports={[withSample.report]}
          detail={withSample}
          onGenerate={() => undefined}
          onSelectVersion={() => undefined}
          onExport={() => undefined}
        />,
      );

      // 先切到一个真实章节，这才是会被“空白化”的那种状态。
      await user.click(within(screen.getByRole('tablist')).getByRole('tab', { name: '核心逻辑' }));
      expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'report-section-thesis');

      await user.click(screen.getByTestId('trust-badge'));
      await user.click(screen.getByRole('link', { name: /1672\.00/ }));

      // 章节 Tab 不能被一个不是章节的 id 改写；正文必须还在。
      const panel = screen.getByRole('tabpanel');
      expect(panel).toHaveAttribute('id', 'report-section-thesis');
      expect(panel.textContent ?? '').toContain('核心段落');
      // 门面指标卡自己也必须是一个可定位的目标。
      expect(document.querySelector('[data-block-id="keyMetrics"]')).not.toBeNull();
    });

  it('exposes the report body anchor the skip link points at', () => {
    const { container } = render(
      <ReportPanel
        instrumentName="贵州茅台"
        reports={[SUMMARY]}
        detail={DETAIL}
        onGenerate={() => undefined}
        onSelectVersion={() => undefined}
        onExport={() => undefined}
      />,
    );
    expect(container.querySelector('#report-body')).not.toBeNull();
  });

  it('states how many numbers could not be grounded (§16 verdict 4)', () => {
    const degraded: ReportDetail = {
      report: { ...SUMMARY, status: 'degraded', quality: { ...SUMMARY.quality, groundedRatio: 0.72 } },
      doc: DETAIL.doc,
    };
    render(
      <ReportPanel
        instrumentName="贵州茅台"
        reports={[degraded.report]}
        detail={degraded}
        onGenerate={() => undefined}
        onSelectVersion={() => undefined}
        onExport={() => undefined}
      />,
    );
    expect(screen.getByTitle(/3 处数字未能在快照中找到来源/)).toBeInTheDocument();
  });

  it('opens the sources drawer from the footer link (AC-7)', async () => {
    const user = userEvent.setup();
    render(
      <ReportPanel
        instrumentName="贵州茅台"
        reports={[SUMMARY]}
        detail={DETAIL}
        onGenerate={() => undefined}
        onSelectVersion={() => undefined}
        onExport={() => undefined}
      />,
    );
    await user.click(screen.getByRole('button', { name: /来源（2）/ }));
    expect(useUiStore.getState().sourceDrawer).toEqual({ open: true, activeSourceId: 'S1' });
  });

  it('exports the report the reader asked for', async () => {
    const user = userEvent.setup();
    const onExport = vi.fn();
    render(
      <ReportPanel
        instrumentName="贵州茅台"
        reports={[SUMMARY]}
        detail={DETAIL}
        onGenerate={() => undefined}
        onSelectVersion={() => undefined}
        onExport={onExport}
      />,
    );
    await user.click(screen.getByRole('button', { name: /导出/ }));
    // The header button names the open report, so a stale `detail` cannot
    // silently export a different version than the one on screen.
    expect(onExport).toHaveBeenCalledWith('html', 'report-1');
  });
});


/* ── A4：标列表上的「有定时刷新」角标（§18.4.5）────────────────────────────── */

describe('symbol rail · 定时刷新角标', () => {
  const railItem = (id: string, scheduled?: boolean) => ({
    instrument: {
      id,
      symbol: id,
      name: id,
      market: 'CN' as const,
      currency: 'CNY',
      exchange: null,
      sector: null,
      tz: 'Asia/Shanghai',
      createdAt: '2026-02-10T00:00:00Z',
      updatedAt: '2026-02-10T00:00:00Z',
    },
    scheduled,
  });

  it('只给有启用中定时的标的画时钟', async () => {
    render(
      <SymbolRail
        instruments={[railItem('CN:600519.SH', true), railItem('CN:000858.SZ', false)]}
        watchlist={[]}
        selectedId={null}
        onSelect={() => {}}
        onResolved={() => {}}
      />,
    );

    const badges = await screen.findAllByLabelText('有定时刷新');
    // 两个标的、只有一个带角标：多画一个就是噪声，少画一个就是"看不出来"。
    expect(badges).toHaveLength(1);
  });
});
