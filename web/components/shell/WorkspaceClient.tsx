'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { exportFilenameHint, triggerDownload } from '@/lib/download';
import { modifierLabel, useHotkeys } from '@/lib/keyboard';
import {
  REPORT_GENERATED_AT_META,
  isOffline,
  offlineBadgeText,
  subscribeOnlineStatus,
} from '@/lib/offline';
import { useUiStore } from '@/lib/store';
import { useRailQuotes } from '@/lib/useRailQuotes';
import type {
  Capabilities,
  DiffResult,
  Instrument,
  ReportDetail,
  ReportSummary,
  ScheduleListResponse,
  WatchlistEntry,
} from '@/lib/types';

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
  /** `?report=` — open this version instead of the latest one. */
  initialReportId?: string | null;
  /** `?section=` / `?block=` — deep-link target inside the report. */
  initialSection?: string | null;
  initialBlockId?: string | null;
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
  initialReportId,
  initialSection,
  initialBlockId,
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
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [focusTarget, setFocusTarget] = useState<{ section: string | null; block: string | null }>({
    section: initialSection ?? null,
    block: initialBlockId ?? null,
  });
  const [online, setOnline] = useState(true);
  const [offlineBadge, setOfflineBadge] = useState<string | null>(null);
  const [instrument, setInstrument] = useState<Instrument>(initialInstrument);
  const [reports, setReports] = useState<ReportSummary[]>(initialReports);

  const selectedReportId = activeReportId ?? initialReportId ?? initialDetail?.report.id ?? null;

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

  // A5: the badge must follow the network, not a one-shot server render.
  useEffect(() => {
    const sync = () => {
      setOnline(!isOffline());
      setOfflineBadge(offlineBadgeText());
    };
    sync();
    return subscribeOnlineStatus(sync);
  }, []);

  // A5 / ★V4-P1-9: the badge must prove the age of the version that is actually
  // open. The tag is written here — not in the RSC tree — because switching versions
  // happens without a page load, so a server-rendered tag would keep advertising the
  // previously loaded version. `REPORT_GENERATED_AT_META` is the only source for the
  // attribute name.
  const generatedAt = detailQuery.data?.report.generatedAt ?? null;
  useEffect(() => {
    const existing = document.querySelector<HTMLMetaElement>(
      `meta[name="${REPORT_GENERATED_AT_META}"]`,
    );
    if (!generatedAt) {
      existing?.remove();
    } else {
      const element = existing ?? document.createElement('meta');
      element.setAttribute('name', REPORT_GENERATED_AT_META);
      element.setAttribute('content', generatedAt);
      if (!existing) document.head.appendChild(element);
    }
    // The badge text is derived from that tag, so refresh it in the same tick.
    setOfflineBadge(offlineBadgeText());
  }, [generatedAt, selectedReportId]);

  const items = useMemo(() => watchlistQuery.data?.items ?? [], [watchlistQuery.data]);
  const railInstrumentIds = useMemo(() => new Set(items.map((item) => item.instrument.id)), [items]);
  const reportsForRail = useMemo(() => reportsQuery.data?.items ?? reports, [reportsQuery.data, reports]);

  useEffect(() => {
    if (reportsQuery.data?.items) setReports(reportsQuery.data.items);
  }, [reportsQuery.data]);

  // A4 / §18.4.5：定时刷新是**后台**行为，读者在标列表上就应该看见哪些标的会自动刷新。
  // 自己的 query 而不是复用对话框里的那份：对话框只在打开时挂载（AC-35）。
  const schedulesQuery = useQuery<ScheduleListResponse>({
    queryKey: ['schedules'],
    queryFn: () => api.schedules(),
    enabled: capabilities.features.schedule !== false,
    staleTime: 60_000,
  });

  const scheduledInstrumentIds = useMemo(() => {
    const ids = new Set<string>();
    for (const schedule of schedulesQuery.data?.items ?? []) {
      if (schedule.enabled) ids.add(schedule.instrumentId);
    }
    return ids;
  }, [schedulesQuery.data]);

  const baseRailItems = useMemo<RailItem[]>(() => {
    const seen = new Set<string>();
    const out: RailItem[] = [];
    const push = (candidate: Instrument) => {
      if (seen.has(candidate.id)) return;
      seen.add(candidate.id);
      out.push({
        instrument: candidate,
        hasReport: reportsForRail.some((report) => report.instrumentId === candidate.id),
        scheduled: scheduledInstrumentIds.has(candidate.id),
      });
    };
    push(instrument);
    for (const entry of items) push(entry.instrument);
    return out;
  }, [instrument, items, reportsForRail, scheduledInstrumentIds]);

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

  /**
   * A3: which version to compare against.
   *
   * "Previous" means the next-older version that still exists — a pinned v3 next to
   * a v1 is a legitimate pair, so this must not assume `version - 1`.
   */
  const previousReport = useMemo(() => {
    const ordered = [...reportsForRail].sort((left, right) => right.version - left.version);
    const index = ordered.findIndex((report) => report.id === selectedReportId);
    return index >= 0 ? (ordered[index + 1] ?? null) : (ordered[1] ?? null);
  }, [reportsForRail, selectedReportId]);

  const currentReport = useMemo(
    () => reportsForRail.find((report) => report.id === selectedReportId) ?? null,
    [reportsForRail, selectedReportId],
  );

  const compareRequest = useQuery<DiffResult | null>({
    // The pair is the cache key: switching versions must not show the old diff.
    queryKey: ['diff', previousReport?.id, currentReport?.id],
    queryFn: async () => {
      if (!previousReport || !currentReport) return null;
      return api.diff(previousReport.id, currentReport.id);
    },
    // NOT gated on `compareOpen`: the badge count is what *offers* the compare
    // view, so requiring the panel to be open before fetching made the whole
    // feature unreachable (open -> fetch -> count -> badge -> open ...).
    enabled: Boolean(previousReport && currentReport),
    staleTime: Infinity,
    retry: 0,
  });

  const diffCount = useMemo(() => {
    const diff = compareRequest.data;
    if (!diff) return undefined;
    return (
      diff.metricsDelta.length +
      diff.blocksChanged.length +
      diff.sectionsAdded.length +
      diff.sectionsRemoved.length +
      (diff.ratingChanged ? 1 : 0)
    );
  }, [compareRequest.data]);

  useEffect(() => {
    if (!compareRequest.isError) return;
    const error = compareRequest.error;
    setCompareError(
      error instanceof ApiError ? `${error.message}（${error.code}）` : '版本比较加载失败',
    );
  }, [compareRequest.error, compareRequest.isError]);

  const openCompare = useCallback(() => {
    if (!previousReport || !currentReport) {
      notify.info('这是第一个版本，没有可比对象');
      return;
    }
    setCompareError(null);
    setCompareOpen(true);
  }, [currentReport, previousReport]);

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
    if (compareOpen) {
      setCompareOpen(false);
      return;
    }
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
  }, [compareOpen, generation]);

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
        features={{
          askDeep: capabilities.features.askDeep === true,
          schedule: capabilities.features.schedule !== false,
          search: capabilities.features.fullTextSearch !== false,
        }}
        askDailyLimit={
          typeof capabilities.limits.askDailyLimit === 'number' ? capabilities.limits.askDailyLimit : 30
        }
        compare={{
          diff: compareRequest.data ?? null,
          loading: compareRequest.isLoading,
          error: compareError,
          aVersion: previousReport?.version,
          bVersion: currentReport?.version,
          open: compareOpen,
          onOpen: openCompare,
          onClose: () => setCompareOpen(false),
          diffCount,
        }}
        focusSection={focusTarget.section}
        focusBlockId={focusTarget.block}
        onReportJump={(blockId, sectionId) => {
          // A3: jumping from `CompareView` reuses the one focus path the report
          // already owns, so "click a changed block" cannot silently do nothing.
          setFocusTarget({ section: sectionId, block: blockId });
        }}
        onOpenSearchResult={(target) => {
          // A2 deep link: switch symbol, open the version, then land on the block.
          setFocusTarget({ section: target.sectionId, block: target.blockId });
          setActiveReport(target.reportId);
          router.push(
            `/${encodeURIComponent(target.symbol)}?report=${encodeURIComponent(
              target.reportId,
            )}&section=${encodeURIComponent(target.sectionId)}&block=${encodeURIComponent(target.blockId)}`,
          );
        }}
      />

      {offlineBadge ? (
        <div
          role="status"
          className="pointer-events-none fixed bottom-3 left-1/2 z-40 -translate-x-1/2 rounded-[var(--radius-md)] border border-[var(--warn)]/40 bg-[var(--warn-weak)] px-3 py-1.5 text-[11.5px] text-[var(--warn)] shadow-[var(--shadow-card)]"
        >
          {offlineBadge}
        </div>
      ) : null}
      {!online ? (
        <p className="sr-only" aria-live="polite">
          当前离线，已读报告仍可查看，生成与检索需要联网。
        </p>
      ) : null}
      <p className="sr-only" aria-live="polite">
        快捷键提示：{modifierLabel()}K 打开命令面板
      </p>
    </>
  );
}
