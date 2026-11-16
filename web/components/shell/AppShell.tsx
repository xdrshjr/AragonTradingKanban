'use client';

import dynamic from 'next/dynamic';
import { useQuery } from '@tanstack/react-query';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import {
  isKlineCollapsed,
  isRailCollapsed,
  toCssVariables,
  computeWidths,
} from '@/lib/layout';
import { useUiStore } from '@/lib/store';
import type { Instrument, ReportDetail, ReportSummary, WatchlistEntry } from '@/lib/types';

import { PricePanel } from '../chart/PricePanel';
import { GenerationOverlay } from '../generation/GenerationOverlay';
import type { UseGenerationStreamResult } from '../generation/useGenerationStream';
import { ReportPanel } from '../report/ReportPanel';
import { IconButton } from '../ui/Button';
import { Splitter, useSplitterHandlers } from '../ui/Splitter';
import { RunStatusBadge } from './RunStatusBadge';
import { SymbolRail, type RailItem } from './SymbolRail';

/**
 * ★A2/A3/A4 / AC-35: the three low-frequency panels are **not** first-load code.
 *
 * They are settings-or-occasional surfaces, so their JS is fetched the first time a
 * reader asks for them — the only way the report page stays inside the 240 kB budget
 * while still shipping three new panels (§18.3.6).
 */
const CompareView = dynamic(() => import('../report/CompareView').then((module) => module.CompareView), {
  ssr: false,
});
const SearchDialog = dynamic(() => import('./SearchDialog').then((module) => module.SearchDialog), {
  ssr: false,
});
const ScheduleDialog = dynamic(
  () => import('../schedule/ScheduleDialog').then((module) => module.ScheduleDialog),
  { ssr: false },
);
/**
 * ★v6：命令面板同样只在 ⌘K 时出现，所以它也不该在首屏 chunk 里。
 *
 * v4 漏掉了它（它是唯一一个从 v1 就存在、却一直静态打包的浮层），首屏为此多付了
 * 2–3 kB；这一轮把「浮层一律懒加载」这条口径补齐，而不是把预算调大。
 */
const CommandPalette = dynamic(() => import('./CommandPalette').then((module) => module.CommandPalette), {
  ssr: false,
});

export interface AppShellProps {
  instruments: RailItem[];
  watchlist: WatchlistEntry[];
  activeInstrument: Instrument | null;
  reports: ReportSummary[];
  reportDetail: ReportDetail | null;
  agentMode: 'real' | 'mock';
  degradedReason?: string | null;
  onSelectInstrument: (instrumentId: string) => void;
  onSelectVersion: (reportId: string) => void;
  onTogglePin?: (reportId: string, pinned: boolean) => void;
  onToggleArchive?: (reportId: string, archived: boolean) => void;
  onDelete?: (reportId: string) => void;
  onExport: (format: 'html' | 'md', reportId?: string) => void;
  /** Owned by `WorkspaceClient` so the hotkeys and the header share one run. */
  generation: UseGenerationStreamResult;
  onToggleWatchlist?: (instrumentId: string, inWatchlist: boolean) => void;
  /** `capabilities.features` — every A1/A3/A4 entry is gated on a measurement. */
  features?: { askDeep?: boolean; schedule?: boolean; search?: boolean };
  askDailyLimit?: number;
  /** version comparison state, owned by `WorkspaceClient` (an A3 route state). */
  compare?: {
    diff: import('@/lib/types').DiffResult | null;
    loading: boolean;
    error?: string | null;
    aVersion?: number;
    bVersion?: number;
    open: boolean;
    onOpen: () => void;
    onClose: () => void;
    diffCount?: number;
  };
  /** deep-link params (`?section=&block=`) threaded down to the report panel. */
  focusSection?: string | null;
  focusBlockId?: string | null;
  /** `CompareView` jump target: owned by `WorkspaceClient`, which owns the focus state. */
  onReportJump?: (blockId: string, sectionId: string) => void;
  /**
   * ★v6 G2：`view` 与 `range` 由 `WorkspaceClient` 拥有（它写 URL），这里只做转发。
   * `onBrushRange` 从 K 线一侧上行，`ReportPanel` 一侧消费 —— 两条路径共用一个 state，
   * 因为「框选」和「联动条清除」改的是同一个 URL 参数（AC-64）。
   */
  reading?: {
    view: 'tabs' | 'full';
    onViewChange: (view: 'tabs' | 'full') => void;
    range: { from: string; to: string } | null;
    onClearRange: () => void;
    onBrushRange: (range: { from: string; to: string } | null) => void;
  };
  /** ★v6 G1：`capabilities.features.grounding`。 */
  groundingEnabled?: boolean;
  /** ★v6 G3：成本面板入口（命令面板的「成本…」）。 */
  onOpenCost?: () => void;
  costEnabled?: boolean;
  onOpenSearchResult?: (target: { symbol: string; reportId: string; sectionId: string; blockId: string }) => void;
  children?: React.ReactNode;
}

/**
 * Three-column workspace (spec §5.5 / §10.1).
 *
 * The column proportions come from `lib/layout.ts` and are written to CSS
 * custom properties — the grid below consumes those tokens and nothing else
 * (★P2-06: exactly one source of truth for the ratio).
 */
export function AppShell({
  instruments,
  watchlist,
  activeInstrument,
  reports,
  reportDetail,
  agentMode,
  degradedReason,
  onSelectInstrument,
  onSelectVersion,
  onTogglePin,
  onToggleArchive,
  onDelete,
  onExport,
  onToggleWatchlist,
  generation,
  features,
  askDailyLimit = 30,
  compare,
  focusSection,
  focusBlockId,
  onReportJump,
  onOpenSearchResult,
  reading,
  groundingEnabled = false,
  onOpenCost,
  costEnabled = false,
}: AppShellProps) {
  const layout = useUiStore((state) => state.layout);
  const hydrateLayout = useUiStore((state) => state.hydrateLayout);
  const { resetLayout, handleRailDelta, handleChartDelta } = useSplitterHandlers();
  const [viewportWidth, setViewportWidth] = useState(1440);
  const [searchOpen, setSearchOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  // A `next/dynamic` chunk is downloaded when the component *renders*, so mounting
  // these dialogs eagerly would defeat the split. They are mounted on first open and
  // then kept mounted, so focus return and close animations keep working.
  const [searchMounted, setSearchMounted] = useState(false);
  const [scheduleMounted, setScheduleMounted] = useState(false);

  useEffect(() => {
    if (searchOpen) setSearchMounted(true);
  }, [searchOpen]);

  useEffect(() => {
    if (scheduleOpen) setScheduleMounted(true);
  }, [scheduleOpen]);
  const compareTriggerRef = useRef<HTMLButtonElement | null>(null);
  // Filled by `ReportPanel` on demand: comparing jumps into the document, and the
  // panel owns the scroll container (R-22 keeps diff logic out of `ReportPanel`).
  // The compare panel is a `next/dynamic` child, so the handler is kept in a ref:
  // a new identity on every render must not re-mount it.
  const onReportJumpRef = useRef(onReportJump);
  useEffect(() => {
    onReportJumpRef.current = onReportJump;
  }, [onReportJump]);
  const klineDrawerOpen = useUiStore((state) => state.klineDrawerOpen);
  const setKlineDrawer = useUiStore((state) => state.setKlineDrawer);
  const setSelectedInstrument = useUiStore((state) => state.setSelectedInstrument);
  const selectedId = activeInstrument?.id ?? null;

  useEffect(() => {
    hydrateLayout();
  }, [hydrateLayout]);

  useEffect(() => {
    const sync = () => setViewportWidth(window.innerWidth);
    sync();
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  }, []);

  const widths = useMemo(() => computeWidths(viewportWidth, layout), [viewportWidth, layout]);
  const percentages = useMemo(
    () => ({
      rail: widths.total ? (widths.rail / widths.total) * 100 : 0,
      kline: widths.total ? (widths.kline / widths.total) * 100 : 0,
    }),
    [widths],
  );

  useEffect(() => {
    const root = document.documentElement;
    for (const [key, value] of Object.entries(toCssVariables(widths))) {
      root.style.setProperty(key, value);
    }
  }, [widths]);

  useEffect(() => {
    if (selectedId) setSelectedInstrument(selectedId);
  }, [selectedId, setSelectedInstrument]);

  const klineCollapsed = isKlineCollapsed(viewportWidth);
  const railCollapsed = isRailCollapsed(viewportWidth);
  // The user-driven collapse is its own state: keying it off `railPx === 200`
  // made the affordance one-way (there was nothing to press to come back).
  const collapsedByUser = useUiStore((state) => state.railCollapsedByUser);
  const toggleRailCollapsed = useUiStore((state) => state.toggleRailCollapsed);

  const startGeneration = useCallback(
    (instrumentId: string) => {
      if (generation.isActive) return;
      void generation.start(instrumentId);
    },
    [generation],
  );

  const quoteQuery = useQuery({
    queryKey: ['quote', selectedId],
    queryFn: () => api.quote(selectedId!),
    enabled: Boolean(selectedId),
    staleTime: 30_000,
    retry: 1,
  });

  const activeLabel = activeInstrument?.name ?? copy.brand;

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header
        className="flex h-[var(--header-h)] shrink-0 items-center gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-3"
        data-print="hide"
      >
        <div className="flex items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--accent)] text-[12px] font-bold text-[var(--accent-text)]">
            A
          </span>
          <span className="flex items-baseline gap-1.5">
            <span className="text-[13px] font-semibold tracking-tight text-[var(--text-primary)]">{copy.brand}</span>
            <span className="text-[11px] text-[var(--text-tertiary)]">{copy.brandSuffix}</span>
          </span>
        </div>

        <span aria-hidden="true" className="h-4 w-px bg-[var(--border-subtle)]" />
        <span className="truncate text-[12px] text-[var(--text-secondary)]">{activeLabel}</span>

        <div className="ml-auto flex items-center gap-2">
          <RunStatusBadge generation={generation} agentMode={agentMode} degradedReason={degradedReason} />
          {klineCollapsed ? (
            <IconButton
              label={klineDrawerOpen ? copy.chart.closeDrawer : copy.chart.openDrawer}
              onClick={() => setKlineDrawer(!klineDrawerOpen)}
            >
              {klineDrawerOpen ? <PanelLeftClose className="size-3.5" /> : <PanelLeftOpen className="size-3.5" />}
            </IconButton>
          ) : null}
        </div>
      </header>

      <main className="flex min-h-0 flex-1">
        <div style={{ width: widths.rail }} className="shrink-0">
          <SymbolRail
            instruments={instruments}
            watchlist={watchlist}
            selectedId={selectedId}
            collapsed={railCollapsed || collapsedByUser}
            onSelect={onSelectInstrument}
            onResolved={(instrument) => onSelectInstrument(instrument.id)}
            onToggleWatchlist={onToggleWatchlist}
            onGenerate={startGeneration}
            onToggleCollapsed={() => toggleRailCollapsed()}
          />
        </div>

        {!railCollapsed ? (
          <Splitter
            label={copy.a11y.dragRail}
            value={percentages.rail}
            min={10}
            max={30}
            onDelta={handleRailDelta}
            onReset={resetLayout}
          />
        ) : null}

        {!klineCollapsed && widths.kline > 0 && activeInstrument ? (
          <>
            <div style={{ width: widths.kline }} className="min-w-0 shrink-0">
              <PricePanel
                instrumentId={activeInstrument.id}
                symbol={activeInstrument.symbol}
                name={activeInstrument.name}
                currency={activeInstrument.currency}
                timezone={activeInstrument.tz}
                lastPrice={quoteQuery.data?.last}
                changePct={quoteQuery.data?.changePct}
                brushRange={reading?.range ?? null}
                onBrushRange={reading?.onBrushRange}
              />
            </div>
            <Splitter
              label={copy.a11y.dragChart}
              value={percentages.kline}
              min={25}
              max={65}
              onDelta={handleChartDelta}
              onReset={resetLayout}
            />
          </>
        ) : null}

        <div className="min-w-0 flex-1">
          <ReportPanel
            instrumentName={activeInstrument?.name ?? ''}
            timezone={activeInstrument?.tz}
            reports={reports}
            detail={reportDetail}
            generating={generation.isActive}
            generateDisabled={!activeInstrument}
            onGenerate={() => {
              if (activeInstrument) startGeneration(activeInstrument.id);
            }}
            onSelectVersion={onSelectVersion}
            onTogglePin={onTogglePin}
            onToggleArchive={onToggleArchive}
            onDelete={onDelete}
            onExport={onExport}
            deepAvailable={features?.askDeep ?? false}
            askDailyLimit={askDailyLimit}
            diffCount={compare?.diffCount}
            onCompare={compare ? compare.onOpen : undefined}
            initialSection={focusSection}
            initialBlockId={focusBlockId}
            compareTriggerRef={compareTriggerRef}
            scheduleEnabled={features?.schedule === true}
            onOpenSchedule={() => setScheduleOpen(true)}
            view={reading?.view ?? 'tabs'}
            onViewChange={reading?.onViewChange}
            range={reading?.range ?? null}
            onClearRange={reading?.onClearRange}
            groundingEnabled={groundingEnabled}
          />
        </div>

        {klineCollapsed && klineDrawerOpen && activeInstrument ? (
          <div className="fixed inset-y-[var(--header-h)] right-0 z-30 w-[min(560px,92vw)] border-l border-[var(--border-strong)] bg-[var(--bg-canvas)] shadow-[var(--shadow-pop)]">
            <PricePanel
              instrumentId={activeInstrument.id}
              symbol={activeInstrument.symbol}
              name={activeInstrument.name}
              currency={activeInstrument.currency}
              timezone={activeInstrument.tz}
              lastPrice={quoteQuery.data?.last}
              changePct={quoteQuery.data?.changePct}
              onCloseDrawer={() => setKlineDrawer(false)}
            />
          </div>
        ) : null}
      </main>

      <GenerationOverlay
        state={generation.state}
        agentMode={agentMode}
        onCancel={() => void generation.cancel()}
        onDismiss={generation.reset}
        onRetry={() => {
          if (activeInstrument) startGeneration(activeInstrument.id);
        }}
        onOpenReport={(reportId) => onSelectVersion(reportId)}
      />

      <CommandPalette
        instruments={instruments}
        reports={reports}
        onSelectInstrument={onSelectInstrument}
        onSelectVersion={onSelectVersion}
        onGenerate={() => {
          if (activeInstrument) startGeneration(activeInstrument.id);
        }}
        onExport={onExport}
        onToggleChart={() => setKlineDrawer(!klineDrawerOpen)}
        onOpenSearch={features?.search === false ? undefined : () => setSearchOpen(true)}
        onOpenCost={costEnabled ? onOpenCost : undefined}
      />

      {/* A2: one search surface for the rail and ⌘K; mounted on demand. */}
      {features?.search === false || !searchMounted ? null : (
        <SearchDialog
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelectSymbol={(symbol) => {
          setSearchOpen(false);
          onSelectInstrument(symbol);
        }}
        onSelectHit={(hit) => {
          setSearchOpen(false);
          if (onOpenSearchResult) {
            onOpenSearchResult({
              symbol: hit.symbol,
              reportId: hit.reportId,
              sectionId: hit.sectionId,
              blockId: hit.blockId,
            });
            return;
          }
          onSelectInstrument(hit.symbol);
        }}
      />
      )}

      {/* A4: a low-frequency setting, so it is a dialog, not a page. */}
      {features?.schedule === true && activeInstrument && scheduleMounted ? (
        <ScheduleDialog
          open={scheduleOpen}
          onOpenChange={setScheduleOpen}
          instrumentId={activeInstrument.id}
          symbol={activeInstrument.symbol}
          enabled
        />
      ) : null}

      {/* A3: route state (`?compare=`), so it is a panel rather than a page. */}
      {compare?.open ? (
        <CompareView
        diff={compare.diff}
        loading={compare.loading}
        error={compare.error ?? null}
        aVersion={compare.aVersion}
        bVersion={compare.bVersion}
        onJumpToBlock={(blockId, sectionId) => {
          compare.onClose();
          if (blockId) onReportJumpRef.current?.(blockId, sectionId);
        }}
        onClose={compare.onClose}
        triggerRef={compareTriggerRef}
      />
        ) : null}
    </div>
  );
}

export { cn };
