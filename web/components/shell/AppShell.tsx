'use client';

import { useQuery } from '@tanstack/react-query';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

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
import { CommandPalette } from './CommandPalette';
import { RunStatusBadge } from './RunStatusBadge';
import { SymbolRail, type RailItem } from './SymbolRail';

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
}: AppShellProps) {
  const layout = useUiStore((state) => state.layout);
  const hydrateLayout = useUiStore((state) => state.hydrateLayout);
  const { resetLayout, handleRailDelta, handleChartDelta } = useSplitterHandlers();
  const [viewportWidth, setViewportWidth] = useState(1440);
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
      />
    </div>
  );
}

export { cn };
