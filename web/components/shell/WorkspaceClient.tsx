'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '@/lib/api';
import { exportFilenameHint, triggerDownload } from '@/lib/download';
import { modifierLabel, useHotkeys } from '@/lib/keyboard';
import { useUiStore } from '@/lib/store';
import { useRailQuotes } from '@/lib/useRailQuotes';
import type { Capabilities, Instrument, ReportDetail, ReportSummary, WatchlistEntry } from '@/lib/types';

import { useGenerationStream } from '../generation/useGenerationStream';
import { notify } from '../ui/Toast';
import { AppShell } from './AppShell';
import type { RailItem } from './SymbolRail';

export interface WorkspaceClientProps {
  initialInstrument: Instrument;
  initialReports: ReportSummary[];
  initialDetail: ReportDetail | null;
  initialWatchlist: WatchlistEntry[];
  capabilities: Capabilities;
}

/**
 * Client half of the workspace. The server component already fetched the
 * instrument + latest report for a no-network first paint (§3.1 step 4); this
 * component owns switching, generation and mutation against the query cache.
 */
export function WorkspaceClient({
  initialInstrument,
  initialReports,
  initialDetail,
  initialWatchlist,
  capabilities,
}: WorkspaceClientProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const activeReportId = useUiStore((state) => state.activeReportId);
  const setActiveReport = useUiStore((state) => state.setActiveReport);
  const toggleCommandPalette = useUiStore((state) => state.toggleCommandPalette);
  const toggleChartDrawer = useUiStore((state) => state.setKlineDrawer);
  const openSourceDrawer = useUiStore((state) => state.openSourceDrawer);
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const hydrateTheme = useUiStore((state) => state.hydrateTheme);
  const [instrument, setInstrument] = useState<Instrument>(initialInstrument);
  const [reports, setReports] = useState<ReportSummary[]>(initialReports);

  const selectedReportId = activeReportId ?? initialDetail?.report.id ?? null;

  const detailQuery = useQuery<ReportDetail | null>({
    queryKey: ['report', selectedReportId],
    queryFn: () => (selectedReportId ? api.report(selectedReportId) : Promise.resolve(null)),
    enabled: Boolean(selectedReportId),
    initialData: initialDetail ?? undefined,
    staleTime: Infinity,
  });

  const reportsQuery = useQuery({
    queryKey: ['reports', instrument.id],
    queryFn: () => api.reports(instrument.id),
    initialData: { items: initialReports, total: initialReports.length },
    staleTime: 15_000,
  });

  const watchlistQuery = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => api.watchlist(),
    initialData: { items: initialWatchlist },
    staleTime: 30_000,
  });

  const items = useMemo(() => watchlistQuery.data?.items ?? [], [watchlistQuery.data]);
  const railInstrumentIds = useMemo(() => new Set(items.map((item) => item.instrument.id)), [items]);
  const reportsForRail = useMemo(() => reportsQuery.data?.items ?? reports, [reportsQuery.data, reports]);

  useEffect(() => {
    if (reportsQuery.data?.items) setReports(reportsQuery.data.items);
  }, [reportsQuery.data]);

  const baseRailItems = useMemo<RailItem[]>(() => {
    const seen = new Set<string>();
    const out: RailItem[] = [];
    const push = (candidate: Instrument) => {
      if (seen.has(candidate.id)) return;
      seen.add(candidate.id);
      out.push({
        instrument: candidate,
        hasReport: reportsForRail.some((report) => report.instrumentId === candidate.id),
      });
    };
    push(instrument);
    for (const entry of items) push(entry.instrument);
    return out;
  }, [instrument, items, reportsForRail]);

  // §6.3 wants 现价 + 涨跌 on every rail row; the quotes are fetched here rather
  // than plumbed through the page, so the rail stays self-sufficient.
  const railItems = useRailQuotes(baseRailItems);

  const selectInstrument = useCallback(
    (instrumentId: string) => {
      if (instrumentId === instrument.id) return;
      router.push(`/${encodeURIComponent(instrumentId)}`);
    },
    [instrument.id, router],
  );

  const selectVersion = useCallback(
    (reportId: string) => {
      setActiveReport(reportId);
      void queryClient.invalidateQueries({ queryKey: ['report', reportId] });
    },
    [queryClient, setActiveReport],
  );

  const toggleWatchlist = useCallback(
    async (instrumentId: string, inWatchlist: boolean) => {
      try {
        if (inWatchlist) {
          await api.removeWatchlist(instrumentId);
          queryClient.setQueryData<{ items: WatchlistEntry[] }>(['watchlist'], (previous) => ({
            items: (previous?.items ?? []).filter((entry) => entry.instrument.id !== instrumentId),
          }));
        } else {
          const response = await api.addWatchlist(instrumentId);
          queryClient.setQueryData(['watchlist'], response);
        }
      } catch {
        notify.error('自选列表更新失败');
      }
    },
    [queryClient],
  );

  const handleExport = useCallback(
    (format: 'html' | 'md', reportId?: string) => {
      // An explicit id (a row in the version switcher) must win over "whatever is
      // open" — otherwise the button downloads a different version than it names.
      const target = reportId
        ? (reportsForRail.find((report) => report.id === reportId) ??
          (detailQuery.data?.report.id === reportId ? detailQuery.data.report : undefined))
        : (detailQuery.data?.report ?? reportsForRail.find((report) => report.id === selectedReportId));
      if (!target) {
        notify.warn('还没有可导出的报告');
        return;
      }
      triggerDownload(api.exportUrl(target.id, format), exportFilenameHint(target.title, target.version, format));
      notify.success(format === 'html' ? '正在下载 HTML' : '正在下载 Markdown');
    },
    [detailQuery.data, reportsForRail, selectedReportId],
  );

  const togglePin = useCallback(
    async (reportId: string, pinned: boolean) => {
      try {
        await api.patchReport(reportId, { pinned });
        await queryClient.invalidateQueries({ queryKey: ['reports', instrument.id] });
      } catch {
        notify.error('置顶失败');
      }
    },
    [instrument.id, queryClient],
  );

  const toggleArchive = useCallback(
    async (reportId: string, archived: boolean) => {
      try {
        await api.patchReport(reportId, { archived });
        await queryClient.invalidateQueries({ queryKey: ['reports', instrument.id] });
        notify.success(archived ? '已归档' : '已取消归档');
      } catch {
        notify.error('归档失败');
      }
    },
    [instrument.id, queryClient],
  );

  const deleteVersion = useCallback(
    async (reportId: string) => {
      try {
        await api.deleteReport(reportId);
        await queryClient.invalidateQueries({ queryKey: ['reports', instrument.id] });
        notify.success('已删除该版本');
      } catch {
        notify.error('删除失败');
      }
    },
    [instrument.id, queryClient],
  );

  const cycleTheme = useCallback(() => {
    // `setTheme` persists to localStorage *and* applies the palette — one writer.
    setTheme(theme === 'dark' ? 'light' : 'dark');
  }, [setTheme, theme]);

  // The generation stream lives here, not in `AppShell`: the hotkeys, the rail
  // and the report header all trigger the same run, so there is one owner.
  const generation = useGenerationStream({
    onDone: (reportId) => selectVersion(reportId),
    onError: (code, message) => notify.error(`${message}（${code}）`),
  });

  const requestGeneration = useCallback(
    (options: { force?: boolean } = {}) => {
      if (generation.isActive) {
        notify.info('已有一次生成在进行中');
        return;
      }
      void generation.start(instrument.id, options);
    },
    [generation, instrument.id],
  );

  /** `Esc`: close the topmost layer, never more than one per press. */
  const closeTopLayer = useCallback(() => {
    const state = useUiStore.getState();
    if (state.sourceDrawer.open) {
      state.closeSourceDrawer();
      return;
    }
    if (state.commandPaletteOpen) {
      state.toggleCommandPalette(false);
      return;
    }
    if (state.klineDrawerOpen) {
      state.setKlineDrawer(false);
      return;
    }
    if (!generation.isActive) generation.reset();
  }, [generation]);

  useEffect(() => {
    hydrateTheme();
  }, [hydrateTheme]);

  useHotkeys({
    onCommandPalette: () => toggleCommandPalette(true),
    onCycleTheme: cycleTheme,
    onToggleChart: () => toggleChartDrawer(true),
    onNextSymbol: () => {
      const index = railItems.findIndex((item) => item.instrument.id === instrument.id);
      const next = railItems[Math.min(index + 1, railItems.length - 1)];
      if (next) selectInstrument(next.instrument.id);
    },
    onPrevSymbol: () => {
      const index = railItems.findIndex((item) => item.instrument.id === instrument.id);
      const previous = railItems[Math.max(index - 1, 0)];
      if (previous) selectInstrument(previous.instrument.id);
    },
    // The remaining advertised shortcuts must actually do something (§11.1 /
    // AC-16): generating, opening the sources drawer and closing a layer.
    onGenerate: () => requestGeneration(),
    onRegenerate: () => requestGeneration({ force: true }),
    onOpenSources: () => openSourceDrawer(),
    onCloseOverlay: closeTopLayer,
    onToggleHelp: () => toggleCommandPalette(true),
  });

  useEffect(() => {
    setInstrument(initialInstrument);
    setReports(initialReports);
    setActiveReport(initialDetail?.report.id ?? null);
  }, [initialDetail?.report.id, initialInstrument, initialReports, setActiveReport]);

  return (
    <>
      <a
        href="#report-body"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-[var(--accent)] focus:px-2 focus:py-1 focus:text-[var(--accent-text)]"
      >
        跳到报告正文
      </a>
      <AppShell
        instruments={railItems}
        watchlist={items}
        activeInstrument={instrument}
        reports={reportsForRail}
        reportDetail={detailQuery.data ?? null}
        agentMode={capabilities.agentMode}
        degradedReason={capabilities.aragon.degradedReason ?? null}
        onSelectInstrument={selectInstrument}
        onSelectVersion={selectVersion}
        onTogglePin={togglePin}
        onToggleArchive={toggleArchive}
        onDelete={deleteVersion}
        onExport={handleExport}
        generation={generation}
        onToggleWatchlist={(instrumentId, inWatchlist) =>
          void toggleWatchlist(instrumentId, railInstrumentIds.has(instrumentId) || inWatchlist)
        }
      />
      <p className="sr-only" aria-live="polite">
        快捷键提示：{modifierLabel()}K 打开命令面板
      </p>
    </>
  );
}
