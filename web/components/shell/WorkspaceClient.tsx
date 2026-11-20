'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { isGuest, useAuthActions, useAuthUi, useCan, useGenerationGate, useMe } from '@/lib/auth';
import { copy } from '@/lib/copy';
import { exportFilenameHint, triggerDownload } from '@/lib/download';
import { modifierLabel, useHotkeys } from '@/lib/keyboard';
import { libraryHref } from '@/lib/library';
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
  ChartView,
} from '@/lib/types';

import { useGenerationStream } from '../generation/useGenerationStream';
import { notify } from '../ui/Toast';
import { AppShell } from './AppShell';
import type { RailItem } from './SymbolRail';

import type { ReadingMode } from '../report/ReportPanel';
import dynamic from 'next/dynamic';

/**
 * ★v6 G3：成本面板与预算闸门弹窗都只在需要时下载（AC-35 预算不变）。
 */
const CostPanel = dynamic(() => import('../cost/CostPanel').then((module) => module.CostPanel), {
  ssr: false,
  loading: () => null,
});
const BudgetGateDialog = dynamic(
  () => import('../cost/CostPanel').then((module) => module.BudgetGateDialog),
  { ssr: false, loading: () => null },
);

/** ★v6 G3：402 带回来的字段 —— 够弹窗说清"花了多少 / 还剩多少 / 这次预估多少"。 */
export interface BudgetGateState {
  spentUsd: number;
  budgetUsd: number;
  estimateUsd: number | null;
  /** ★v7（H-7）：402 detail 里的 `note`——"其中 $x 是估算，不是引擎回执"。 */
  note: string | null;
}

/** 写图表口径参数：值为 null 就删除。 */
function setOrDelete(params: URLSearchParams, key: string, value: string | null): void {
  if (value === null) params.delete(key);
  else params.set(key, value);
}

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
  /** ★v6 G2b：`?view=`（`tabs` 为既有行为）。 */
  initialView?: ReadingMode;
  /** ★v6 G2a：`?range=`（`from > to` 或格式不对时由服务端丢弃）。 */
  initialRange?: { from: string; to: string } | null;
  /** ★PCD-3：`?tf=` / `?win=` / `?adj=` —— 图表口径要活过刷新与分享。 */
  initialChartView?: ChartView;
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
  initialView = 'tabs',
  initialRange = null,
  initialChartView,
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
  /** ★v6 G3：成本面板 + 预算闸门弹窗。 */
  const [costOpen, setCostOpen] = useState(false);
  const [budgetGate, setBudgetGate] = useState<BudgetGateState | null>(null);
  /**
   * ★v6 G2b / AC-64：`view` 与 `range` **各自独立**，互不改变对方。
   * 两者都用 `router.replace` 写回 URL —— 来回切阅读模式不该污染后退栈。
   */
  const [view, setViewState] = useState<ReadingMode>(initialView);
  const [range, setRangeState] = useState<{ from: string; to: string } | null>(initialRange);
  /**
   * ★PCD-3：图表口径上升到这里，因为它有两个消费者：K 线面板（渲染）与左栏的选中行（必须与图表同一条序列，否则后复权下左栏与头部相差 514%）。写回 URL 才能活过刷新。
   */
  const [chartView, setChartView] = useState<ChartView>(
    initialChartView ?? { timeframe: '1d', range: '1y', adjust: 'qfq' },
  );

  const selectedReportId = activeReportId ?? initialReportId ?? initialDetail?.report.id ?? null;

  // ★X6（§24.3.9 / §24.4.2）：入口按权限裁剪。服务端才是授权依据——这里只是不把“必然 401/403”的入口摆出来。
  const me = useMe();
  const openAuth = useAuthUi((state) => state.openAuth);
  const { signOut } = useAuthActions(() => router.refresh());
  const gate = useGenerationGate();
  const canSchedule = useCan('schedule.manage');
  const canCost = useCan('cost.read');
  const canAdmin = useCan('admin.overview.read');
  const canDeepAsk = useCan('ask.deep');

  const detailQuery = useQuery<ReportDetail | null>({
    queryKey: ['report', selectedReportId],
    queryFn: () => (selectedReportId ? api.report(selectedReportId) : Promise.resolve(null)),
    enabled: Boolean(selectedReportId),
    // ★v14（U2 / AC-141 的连带修正，见 spec「实施过程发现的方案缺陷」D-8）：
    // `initialData` 的**值是数据、不是函数**，而 `queryKey` 里含 `selectedReportId`
    // ⇒ 每一个新 key 首次挂载都会拿到同一份 RSC 首屏 detail。配合 `staleTime: Infinity`，
    // 从深链切到别的版本时新 key 既"有数据"又"永不陈旧"，界面就停在旧版，而 URL 已经
    // 指向新版 —— 这正是 U-0c 说的"切版本之后看到的还是同一份"。
    // 所以只在那份 detail 真的属于当前 id 时才把它当首屏数据。
    initialData:
      initialDetail && initialDetail.report.id === selectedReportId ? initialDetail : undefined,
    // Keep the displayed version and its assistant together until the next
    // version arrives; never borrow a different instrument's document.
    placeholderData: (previous) => previous?.report.instrumentId === instrument.id
      ? previous : undefined,
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
    // `/schedules` 只给 `schedule.manage`（管理员）：游客 / 普通用户发这个请求只会换来 401/403。
    enabled: capabilities.features.schedule !== false && canSchedule,
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
  const railItems = useRailQuotes(baseRailItems, { selectedId: instrument.id, chart: chartView });

  /**
   * 把 `view` / `range` 写回 URL（`router.replace`，不新增历史条目）。
   *
   * 规则只有一条：**只改自己那一个参数，其余原样保留**。所以切模式不会丢掉
   * `range`，清 brush 也不会把 `view` 打回 `tabs`（AC-64 的两条独立性）。
   */
  const syncUrl = useCallback(
    (next: {
      view?: ReadingMode;
      range?: { from: string; to: string } | null;
      chart?: ChartView;
      /** ★v14：`null` = 删掉该参数（选中的是最新版时）。 */
      report?: string | null;
    }) => {
      const params = new URLSearchParams(window.location.search);
      if (next.view !== undefined) {
        if (next.view === 'tabs') params.delete('view');
        else params.set('view', next.view);
      }
      if (next.range !== undefined) {
        if (next.range === null) params.delete('range');
        else params.set('range', `${next.range.from}..${next.range.to}`);
      }
      if (next.report !== undefined) {
        // ★v14（U2 / §23.3.3）：`report` 与其他参数同一套纪律——只改自己那一个键，
        // 且等于默认（= 最新版）时删掉。于是 F5 / 分享链接看到的还是同一版（U-0c）。
        setOrDelete(params, 'report', next.report);
      }
      if (next.chart !== undefined) {
        // ★PCD-3：三个参数各自独立，且等于默认值时删掉——URL 只记录“与默认不同”的那一部分。
        setOrDelete(params, 'tf', next.chart.timeframe === '1d' ? null : next.chart.timeframe);
        setOrDelete(params, 'win', next.chart.range === '1y' ? null : next.chart.range);
        setOrDelete(params, 'adj', next.chart.adjust === 'qfq' ? null : next.chart.adjust);
      }
      const query = params.toString();
      router.replace(query ? `?${query}` : '?', { scroll: false });
    },
    [router],
  );

  const changeView = useCallback(
    (next: ReadingMode) => {
      setViewState(next);
      syncUrl({ view: next });
    },
    [syncUrl],
  );

  const changeRange = useCallback(
    (next: { from: string; to: string } | null) => {
      setRangeState(next);
      syncUrl({ range: next });
    },
    [syncUrl],
  );

  const changeChartView = useCallback(
    (next: ChartView) => {
      setChartView(next);
      syncUrl({ chart: next });
    },
    [syncUrl],
  );

  const selectInstrument = useCallback(
    (instrumentId: string) => {
      if (instrumentId === instrument.id) return;
      router.push(`/${encodeURIComponent(instrumentId)}`);
    },
    [instrument.id, router],
  );

  /**
   * ★v14（U2 / ★P1-7）：**最新版 id 的权威来源**。
   *
   * 深链时 `initialDetail` 已经被 `?report=` 那一份**替换**（`app/[symbol]/page.tsx`），
   * 所以 `initialDetail.report.id` 不是最新版；`initialReports` 又默认 `includeArchived=false`。
   * 取值顺序写死，且与 `report_service.latest_of()` 同序，不允许在组件里另发明一套：
   * ① `initialReports` 里 `isLatest === true` 的那一行 →
   * ② 否则 `version` 最大且 `provisional === false` 的那一行 →
   * ③ 否则 `version` 最大的那一行 → ④ 都没有则 `null`（不出横幅）。
   */
  const latestReport = useMemo<ReportSummary | null>(() => {
    const rows = reportsQuery.data?.items ?? reports;
    if (rows.length === 0) return null;
    const flagged = rows.find((row) => row.isLatest);
    if (flagged) return flagged;
    const solid = rows.filter((row) => !row.provisional);
    const pool = solid.length > 0 ? solid : rows;
    const head = pool[0];
    if (!head) return null;
    return pool.reduce((best, row) => (row.version > best.version ? row : best), head);
  }, [reports, reportsQuery.data]);
  const latestReportId = latestReport?.id ?? null;

  /**
   * ★v14（U1 / 评审 R-2）：命令面板里的「最近 5 份报告」。
   *
   * **只在面板打开时取**（`enabled`）：面板是低频入口，让它常驻查询等于给每次进工作台
   * 都加一次 `/reports`，而工作台首屏的请求数是有预算的（★E-126 的另一面）。
   * `reportLibrary.available()` 为假时也不取——那说明后端没有这个能力，取也是 404/500。
   */
  const commandPaletteOpen = useUiStore((state) => state.commandPaletteOpen);
  const libraryEnabled = capabilities.features.reportLibrary === true;
  const recentReportsQuery = useQuery({
    queryKey: ['reports', 'library', 'palette', 5],
    queryFn: () => api.reportLibrary({ scope: 'latest', limit: 5, withCounts: false }),
    enabled: libraryEnabled && commandPaletteOpen,
    staleTime: 30_000,
    select: (data) =>
      data.items.map((item) => ({
        id: item.report.id,
        symbol: item.instrument.symbol,
        name: item.instrument.name,
        headline: item.report.headline || item.report.title,
        version: item.report.version,
      })),
  });

  const selectVersion = useCallback(
    (reportId: string) => {
      setActiveReport(reportId);
      // ★v14（U2）：URL 是"当前哪一版"的**唯一**真源（它能被刷新、收藏、分享）。
      // 选中的是最新版时把参数删掉 —— "URL 只记录与默认不同"这条既有纪律。
      syncUrl({ report: latestReportId && reportId === latestReportId ? null : reportId });
      void queryClient.invalidateQueries({ queryKey: ['report', reportId] });
    },
    [latestReportId, queryClient, setActiveReport, syncUrl],
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
      } catch (error) {
        if (error instanceof ApiError && error.code === 'forbidden' && error.detail.reason === 'only_version') {
          notify.info(copy.errors.archiveOnlyVersion);
          return;
        }
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
    // ★v14（U3 / §23.3.4a）：内容与最新版完全一致 ⇒ 没有新增版本。仍然打开那一版
    // （"什么都不发生"是最坏的一种反馈），并说清为什么。
    onDeduped: (reportId, version) => {
      selectVersion(reportId);
      notify.info(
        version !== null
          ? copy.history.deduped.replace('{version}', String(version))
          : copy.history.deduped.replace('{version}', '—'),
      );
    },
    onError: (code, message) => notify.error(`${message}（${code}）`),
    // ★v9（K6 / §21.3.7）：接管不再是静默的 —— 用 `copy.errors.runConflict`
    // （这条文案写了两年，一直没有被任何代码用过）。
    onTakeover: () => notify.info(copy.errors.runConflict),
    // ★v6 G3 / R-25：402 走弹窗而不是红条 —— 主按钮就是「仍然生成」。
    onBudgetBlocked: (detail) => {
      const asNumber = (value: unknown): number => (typeof value === 'number' ? value : 0);
      setBudgetGate({
        spentUsd: asNumber(detail.spentUsd),
        budgetUsd: asNumber(detail.budgetUsd),
        estimateUsd: typeof detail.estimateUsd === 'number' ? detail.estimateUsd : null,
        // ★v7：服务端已经算好了"其中多少是估算"，前端只转述（不自己拼这句话）。
        note: typeof detail.note === 'string' ? detail.note : null,
      });
    },
  });

  const requestGeneration = useCallback(
    (options: { force?: boolean; override?: boolean; baseReportId?: string | null } = {}) => {
      if (generation.isActive) {
        notify.info('已有一次生成在进行中');
        return;
      }
      // ★X6：热键 / 历史横幅的“基于此版本更新”也走同一道前置闸（游客 → 登录框；额度 0 → 额度对话框）。
      gate(() => void generation.start(instrument.id, options), instrument.symbol);
    },
    [gate, generation, instrument.id, instrument.symbol],
  );

  const account = useMemo(() => {
    if (me.authMode === 'open') return undefined;
    if (isGuest(me)) return { onSignIn: () => openAuth('login', { type: 'generic' }) };
    return {
      onOpenAccount: () => router.push('/account'),
      onOpenAdmin: canAdmin ? () => router.push('/admin') : undefined,
      onSignOut: () => void signOut().then(() => notify.info(copy.auth.signedOut)),
    };
  }, [canAdmin, me, openAuth, router, signOut]);

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
    if (state.assistantOpen) {
      state.setAssistantOpen(false);
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
    return () => useUiStore.getState().setAssistantOpen(false);
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
        aragon={capabilities.aragon}
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
          // 深度追问要登录并走额度（`ask.deep`）；游客只剩零成本的引用式检索。
          askDeep: capabilities.features.askDeep === true && canDeepAsk,
          askConversation: capabilities.features.askConversation === true,
          schedule: capabilities.features.schedule !== false && canSchedule,
          search: capabilities.features.fullTextSearch !== false,
          // ★v14（U1 / §23.5.4）：`false` 时**不渲染**「报告库」入口（不留死链）。
          reportLibrary: capabilities.features.reportLibrary === true,
        }}
        history={{
          // ★P1-7：最新版 id 只有一个权威来源（上面那段 `latestReport`），组件不猜。
          latestReportId,
          latestVersion: latestReport?.version ?? null,
          latestGeneratedAt: latestReport?.generatedAt ?? null,
          onBackToLatest: () => {
            if (latestReportId) selectVersion(latestReportId);
          },
          onBaseThisVersion: (reportId: string) => requestGeneration({ baseReportId: reportId }),
        }}
        askDailyLimit={
          typeof capabilities.limits.askDailyLimit === 'number' ? capabilities.limits.askDailyLimit : 30
        }
        askMaxQuestionChars={typeof capabilities.limits.askMaxQuestionChars === 'number'
          ? capabilities.limits.askMaxQuestionChars : 500}
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
        reading={{
          view,
          onViewChange: changeView,
          range,
          onClearRange: () => changeRange(null),
          onBrushRange: changeRange,
        }}
        groundingEnabled={capabilities.features.grounding === true}
        chart={chartView}
        onChartViewChange={changeChartView}
        onOpenCost={() => setCostOpen(true)}
        costEnabled={capabilities.features.costLedger === true && canCost}
        account={account}
        // ★v14（U1 / 评审 R-2）：命令面板的「报告库」入口与「最近 5 份报告」。
        onOpenLibrary={() => router.push('/reports')}
        recentReports={recentReportsQuery.data}
        onOpenReport={(symbol, reportId) => {
          // ★U-0b：打开的是**那一版**（`libraryHref` 100% 带 `?report=`），不是这个标的的最新版。
          setActiveReport(reportId);
          router.push(libraryHref(symbol, reportId));
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

      {costOpen ? <CostPanel open={costOpen} onOpenChange={setCostOpen} /> : null}
      {budgetGate ? (
        <BudgetGateDialog
          open
          spentUsd={budgetGate.spentUsd}
          budgetUsd={budgetGate.budgetUsd}
          estimateUsd={budgetGate.estimateUsd}
          note={budgetGate.note}
          onCancel={() => setBudgetGate(null)}
          onOverride={() => {
            setBudgetGate(null);
            notify.info(copy.cost.overridden);
            requestGeneration({ override: true });
          }}
        />
      ) : null}

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
