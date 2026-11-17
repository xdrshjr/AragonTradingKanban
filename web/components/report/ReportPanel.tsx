'use client';

import { ArrowUpRight, Clock, Download, ListTree, MessageCircleQuestion, RefreshCw } from 'lucide-react';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { KEY_METRICS_BLOCK_ID, blockIdOf, parseBlockId, sectionIdOf } from '@/lib/blocks';
import { copy } from '@/lib/copy';
import { costLabel, dateTime, relativeTime } from '@/lib/format';
import { missingCounts } from '@/lib/grounding';
import { useUiStore } from '@/lib/store';
import type { ReportDetail, ReportSummary, SourceRef } from '@/lib/types';

import { Badge, Tag } from '../ui/Badge';
import { IconButton } from '../ui/Button';
import { EmptyReportState } from './EmptyReportState';
import { DiffBadge } from './DiffBadge';
import { ReportRenderer } from './ReportRenderer';
import { SourceDrawer } from './SourceDrawer';
import { VersionSwitcher } from './VersionSwitcher';
import { MetricGrid } from './blocks/MetricsBlock';
import { GenerateButton } from '../generation/GenerateButton';

/**
 * ★A1 / AC-35: the ask drawer never enters the first-load chunk. Readers who never
 * ask never download it (§18.3.6).
 */
/** ★A5：顶层容器 id —— 与后端 `numeric_claims.extract_claims()` 里的 block_id 逐字一致。 */
const HEADLINE_BLOCK_ID = 'headline';
const SUMMARY_BLOCK_ID = 'summary';
const RISK_LIST_BLOCK_ID = 'riskList';

const AskPanel = dynamic(() => import('../ask/AskPanel').then((module) => module.AskPanel), {
  ssr: false,
  loading: () => null,
});

/**
 * ★v6 G2b / AC-52: the full-text container is a **separate async chunk** too. It
 * carries the outline and the progress line, none of which a Tab reader needs —
 * so `/[symbol]` first load stays inside the 240 kB budget.
 */
const ReadingView = dynamic(() => import('./ReadingView').then((module) => module.ReadingView), {
  ssr: false,
  loading: () => null,
});

/**
 * ★v6 G2a: the range link bar only exists when the URL carries `?range=`.
 * Lazy for the same reason as everything else here: an unused entry is weight.
 */
const RangeHint = dynamic(() => import('./RangeHint').then((module) => module.RangeHint), {
  ssr: false,
  loading: () => null,
});

/**
 * ★v6 G1：可信度徽标只在**这份报告真的有 `quality.grounding`** 时才需要，
 * 所以它是一个 async chunk —— 没有核验记录的报告（早期版本、临时版、mock）
 * 连这几 KB 都不下载。AC-35 的首屏预算因此不变（AC-52 同理）。
 */
const SalvageNotice = dynamic(() => import('./SalvageNotice').then((module) => module.SalvageNotice), {
  ssr: false,
});
const TrustBadge = dynamic(() => import('./TrustBadge').then((module) => module.TrustBadge), {
  ssr: false,
  loading: () => null,
});

/** `?view=` — `tabs` is the pre-v5 behaviour and stays the default (no regression). */
export type ReadingMode = 'tabs' | 'full';

export interface ReportPanelProps {
  instrumentName: string;
  timezone?: string;
  reports: ReportSummary[];
  detail: ReportDetail | null;
  generating?: boolean;
  generateDisabled?: boolean;
  onGenerate: () => void;
  onSelectVersion: (reportId: string) => void;
  onTogglePin?: (reportId: string, pinned: boolean) => void;
  onToggleArchive?: (reportId: string, archived: boolean) => void;
  onDelete?: (reportId: string) => void;
  onExport: (format: 'html' | 'md', reportId?: string) => void;
  /** `capabilities.features.askDeep` — false in mock mode, so `deep` is hidden. */
  deepAvailable?: boolean;
  /** `capabilities.limits.askDailyLimit` */
  askDailyLimit?: number;
  /** structural changes vs the previous version; undefined hides the badge */
  diffCount?: number;
  onCompare?: () => void;
  /** `capabilities.features.schedule` — false hides the 定时刷新 entry entirely. */
  scheduleEnabled?: boolean;
  onOpenSchedule?: () => void;
  /** deep link: open on this section and scroll to this block */
  initialSection?: string | null;
  initialBlockId?: string | null;
  /** AC-36: `CompareView` returns focus here when it closes. */
  compareTriggerRef?: React.RefObject<HTMLButtonElement | null>;
  /**
   * ★v6 G2b：`?view=` 的当前值。默认 `tabs` —— 既有行为一行不改（不回归）。
   * 由 `WorkspaceClient` 从 URL 读，并用 `router.replace` 写回（不污染历史）。
   */
  view?: ReadingMode;
  onViewChange?: (view: ReadingMode) => void;
  /** ★v6 G2a：`?range=2025-03-01..2025-04-15`；与 `view` **正交**（AC-64）。 */
  range?: { from: string; to: string } | null;
  onClearRange?: () => void;
  /**
   * ★v6 G1：`capabilities.features.grounding`。`false` 时**整个可信度层不渲染**
   * （徽标、点、脚注都不出现），而不是渲染一个空壳（§19.2 的"E 层无悬挂分支"）。
   */
  groundingEnabled?: boolean;
}

/**
 * The reading surface (spec §3.3 / §10). Everything a reader needs to move
 * between chapters, versions and sources lives in the sticky header; the body is
 * the document itself and nothing else.
 */
export function ReportPanel({
  instrumentName,
  timezone,
  reports,
  detail,
  generating,
  generateDisabled,
  onGenerate,
  onSelectVersion,
  onTogglePin,
  onToggleArchive,
  onDelete,
  onExport,
  deepAvailable = false,
  askDailyLimit = 30,
  diffCount,
  onCompare,
  scheduleEnabled = false,
  onOpenSchedule,
  initialSection,
  initialBlockId,
  compareTriggerRef,
  view = 'tabs',
  onViewChange,
  range = null,
  onClearRange,
  groundingEnabled = true,
}: ReportPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const tabsRef = useRef<HTMLDivElement | null>(null);
  const askTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [activeSection, setActiveSection] = useState<string>('all');
  const [showToc, setShowToc] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askMounted, setAskMounted] = useState(false);
  const [highlightBlockId, setHighlightBlockId] = useState<string | null>(null);
  const modeButtonRef = useRef<HTMLButtonElement | null>(null);
  const openSourceDrawer = useUiStore((state) => state.openSourceDrawer);
  const sources = useMemo<SourceRef[]>(() => detail?.doc.sources ?? [], [detail]);
  const doc = detail?.doc ?? null;
  const summary = detail?.report ?? null;
  /**
   * ★v6 G1：可信度层的数据源**只**是当前版本的 `report.quality`。
   * 禁止读 `<meta>`（I-31：那是 RSC 快照，切换版本后会指向另一版）——
   * 所以"徽标说 48/51、页面却是另一个版本"这个坑在这里被结构性地堵住。
   */
  const grounding = groundingEnabled ? (summary?.quality.grounding ?? null) : null;

  useEffect(() => {
    if (askOpen) setAskMounted(true);
  }, [askOpen]);

  const sections = useMemo(() => {
    if (!doc) return [];
    return doc.sections.map((section) => ({ id: section.id, title: section.title, blocks: section.blocks }));
  }, [doc]);


  /**
   * Block-level jump targets (★V4-P1-2, AC-41).
   *
   * `blockIds` must stay aligned with the **original** block list, so filtering a
   * redundant heading keeps the derived `sectionId#index` honest. The ids come from
   * `lib/blocks.ts`, which mirrors the backend's `block_ids()`; they are never
   * re-derived here.
   *
   * A heading whose text equals its section title is dropped: the panel already
   * prints that title as the section's own `<h2>`, so the block would render twice.
   */
  const sectionsWithBlocks = useMemo(
    () =>
      sections.map((section) => ({
        id: section.id,
        title: section.title,
        items: section.blocks
          .map((block, index) => ({ block, blockId: blockIdOf(section.id, index) }))
          .filter(({ block }) => !(block.type === 'heading' && block.text.trim() === section.title.trim())),
      })),
    [sections],
  );

  // Reset the section tab when the report changes.
  useEffect(() => {
    setActiveSection('all');
    scrollRef.current?.scrollTo({ top: 0 });
  }, [detail?.report.id]);

  const visibleSections =
    activeSection === 'all'
      ? sectionsWithBlocks
      : sectionsWithBlocks.filter((section) => section.id === activeSection);

  const stance = doc?.rating.stance ?? 'watch';
  const stanceTone = stance === 'buy' ? 'up' : stance === 'sell' ? 'down' : 'neutral';

  const jumpToBlock = useCallback(
    (blockId: string | null, sectionId: string) => {
      // Section tabs collapse everything else, so open the matching tab first.
      if (sectionId && activeSection !== 'all' && activeSection !== sectionId) {
        setActiveSection(sectionId);
      }
      setHighlightBlockId(blockId);
      if (!blockId) return;
      // Wait for the tab switch to paint before scrolling.
      requestAnimationFrame(() => {
        const target = scrollRef.current?.querySelector<HTMLElement>(
          `[data-block-id="${CSS.escape(blockId)}"]`,
        );
        target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
    },
    [activeSection],
  );

  /**
   * ★v6 G1：徽标里点一条样本 → 跳到那个块。全文视图没有"章节 Tab"的概念，所以
   * 只设高亮 + 让容器滚；Tab 视图沿用既有 `jumpToBlock`（会先切 Tab）。
   */
  const jumpToBlockById = useCallback(
    (blockId: string) => {
      // ★v6 G1：`keyMetrics` 这类**不是** `<sectionId>#<index>` 的定位（后端给顶层
      // `doc.keyMetrics[]` 的未接地样本用的就是这个 blockId）不能走 `jumpToBlock`：
      // 它的 `sectionIdOf()` 会原样返回 'keyMetrics'，于是 `setActiveSection('keyMetrics')`
      // 把 `visibleSections` 过滤成空数组 —— 正文整篇消失，而且没有任何 Tab 是选中态。
      // 这类定位一律「在页头指标卡上高亮 + 滚到它」，Tab 状态一动不动。
      if (!parseBlockId(blockId)) {
        setHighlightBlockId(blockId);
        requestAnimationFrame(() => {
          const target = scrollRef.current?.querySelector<HTMLElement>(
            `[data-block-id="${CSS.escape(blockId)}"]`,
          );
          target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
        return;
      }
      const sectionId = sectionIdOf(blockId);
      jumpToBlock(blockId, sectionId);
    },
    [jumpToBlock],
  );

  // The highlight is a flash, not a state: clear it so a later jump is visible.
  useEffect(() => {
    if (!highlightBlockId) return;
    const node = scrollRef.current?.querySelector<HTMLElement>(
      `[data-block-id="${CSS.escape(highlightBlockId)}"]`,
    );
    node?.setAttribute('data-highlighted', 'true');
    const timer = setTimeout(() => node?.removeAttribute('data-highlighted'), 2400);
    return () => clearTimeout(timer);
  }, [highlightBlockId, activeSection]);

  // Deep link (`?section=&block=`): open the right tab and jump once, on arrival.
  useEffect(() => {
    if (!initialSection && !initialBlockId) return;
    if (initialSection) setActiveSection(initialSection);
    if (!initialBlockId) return;
    setHighlightBlockId(initialBlockId);
    const timer = setTimeout(() => {
      scrollRef.current
        ?.querySelector<HTMLElement>(`[data-block-id="${CSS.escape(initialBlockId)}"]`)
        ?.scrollIntoView({ block: 'center' });
    }, 60);
    return () => clearTimeout(timer);
  }, [initialBlockId, initialSection]);

  const handleExportMenu = useCallback(
    (format: 'html' | 'md', reportId?: string) => {
      onExport(format, reportId);
    },
    [onExport],
  );

  /**
   * §11.1 / AC-16: the section tabs are reachable and switchable with the
   * keyboard alone — ←/→ cycle, Home/End jump, and focus follows the selection.
   */
  const handleTabKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const order = ['all', ...sections.map((section) => section.id)];
      const index = Math.max(0, order.indexOf(activeSection));
      let next = index;
      switch (event.key) {
        case 'ArrowRight':
          next = (index + 1) % order.length;
          break;
        case 'ArrowLeft':
          next = (index - 1 + order.length) % order.length;
          break;
        case 'Home':
          next = 0;
          break;
        case 'End':
          next = order.length - 1;
          break;
        default:
          return;
      }
      event.preventDefault();
      const target = order[next];
      if (!target) return;
      setActiveSection(target);
      tabsRef.current?.querySelector<HTMLElement>(`#report-tab-${CSS.escape(target)}`)?.focus();
    },
    [activeSection, sections],
  );

  /**
   * When every section is shown, the panel already prints `section.title` as an
   * `<h2>`. A model (and, before this fix, the mock generator) may also start the
   * section with a heading of the same text, which rendered the title twice.
   *
   * The filter now lives in `sectionsWithBlocks`, where the block ids are derived —
   * so the ids stay aligned with the **document**, not with the filtered list.
   */
  if (!doc) {
    return (
      <section aria-label="投研报告" className="flex h-full min-w-0 flex-col bg-[var(--bg-canvas)]">
        <EmptyReportState
          instrumentName={instrumentName}
          onGenerate={onGenerate}
          generating={generating}
          disabled={generateDisabled}
        />
      </section>
    );
  }

  return (
    <section
      aria-label="投研报告"
      data-print="full"
      className="relative flex h-full min-w-0 flex-col overflow-hidden bg-[var(--bg-canvas)]"
    >
      <header className="shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-canvas)]/95 backdrop-blur">
        <div className="flex items-center gap-2 px-3 py-1.5">
          <Badge tone={stanceTone === 'up' ? 'up' : stanceTone === 'down' ? 'down' : 'neutral'}>
            {copy.report.stance[stance]}
          </Badge>
          {doc.rating.conviction ? <Tag title="信心">信心 {doc.rating.conviction}/5</Tag> : null}
          {doc.rating.targetPrice ? <Tag title="目标价">目标 {doc.rating.targetPrice.toFixed(2)}</Tag> : null}
          {doc.rating.upsidePct !== null && doc.rating.upsidePct !== undefined ? (
            <Tag title="上行空间">{doc.rating.upsidePct > 0 ? '+' : ''}{doc.rating.upsidePct.toFixed(1)}%</Tag>
          ) : null}
          {doc.provisional ? <Badge tone="warn">{copy.report.provisional}</Badge> : null}
          {summary?.status === 'degraded' ? (
            <Badge
              tone="warn"
              title={`接地率 ${((summary.quality.groundedRatio ?? 0) * 100).toFixed(0)}%${
                summary.quality.ungroundedCount
                  ? ` · ${missingCounts(summary.quality.grounding).distinct || summary.quality.ungroundedCount} 个不同数值（共 ${summary.quality.ungroundedCount} 次引用）未能在快照中找到来源`
                  : ''
              }`}
            >
              {copy.report.degraded}
            </Badge>
          ) : null}
          {summary?.quality.source === 'mock' ? <Badge tone="info">{copy.report.mock}</Badge> : null}
          {/* ★v6 G1：`grounding == null`（无数据 / 早期报告 / 能力关闭）时组件自己返回 null。 */}
          {summary ? (
            <TrustBadge quality={summary.quality.grounding ?? null} reportId={summary.id} onJump={jumpToBlockById} />
          ) : null}
          {/* ★v9（K5）：抢救可见。`used === false` 或老报告没有这个键 ⇒ 什么都不渲染。 */}
          {summary ? <SalvageNotice salvage={summary.quality.salvage ?? null} /> : null}

          <div className="ml-auto flex items-center gap-1">
            {/* ★v6 G2b：模式切换。`router.replace` 只改 `view`，不动 `range`（AC-64）。 */}
            <ReadingModeSwitch
              value={view}
              onChange={onViewChange}
              disabled={generating}
              buttonRef={modeButtonRef}
            />
            <span className="hidden text-[10.5px] text-[var(--text-tertiary)] sm:inline">
              {copy.report.generatedAt} {relativeTime(summary?.generatedAt)}
              {summary ? ` · ${costLabel(summary.costUsd, summary.costKnown, summary.quality.cost)}` : ''}
            </span>
            <VersionSwitcher
              reports={reports}
              activeId={summary?.id ?? null}
              onSelect={onSelectVersion}
              onPin={onTogglePin}
              onArchive={onToggleArchive}
              onDelete={onDelete}
              onExport={handleExportMenu}
              disabled={generating}
            />
            {scheduleEnabled && onOpenSchedule ? (
              <IconButton label={copy.schedule.open} onClick={onOpenSchedule}>
                <Clock className="size-3.5" />
              </IconButton>
            ) : null}
            {diffCount !== undefined && diffCount > 0 && onCompare ? (
              <DiffBadge count={diffCount} onClick={onCompare} triggerRef={compareTriggerRef} />
            ) : null}
            <IconButton label={copy.report.exportHtml} onClick={() => handleExportMenu('html', summary?.id)}>
              <Download className="size-3.5" />
            </IconButton>
            {/* A1: the ask entry. Lazy panel; this button is its focus-return target. */}
            <IconButton
              ref={askTriggerRef}
              label={copy.ask.open}
              aria-expanded={askOpen}
              onClick={() => setAskOpen((value) => !value)}
            >
              <MessageCircleQuestion className="size-3.5" />
            </IconButton>
            <GenerateButton
              onGenerate={onGenerate}
              generating={generating}
              disabled={generateDisabled}
              size="sm"
              variant="secondary"
            >
              <RefreshCw className="size-3" aria-hidden="true" />
              {generating ? copy.report.generating : copy.report.regenerate}
            </GenerateButton>
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 pb-1.5">
          <div
            role="tablist"
            ref={tabsRef}
            aria-label={copy.report.tabs}
            onKeyDown={handleTabKeyDown}
            className="flex min-w-0 items-center gap-0.5 overflow-x-auto"
          >
            <SectionTab
              id="all"
              active={activeSection === 'all'}
              onSelect={setActiveSection}
              controls="report-section-all"
            >
              全部
            </SectionTab>
            {sections.map((section) => (
              <SectionTab
                key={section.id}
                id={section.id}
                active={activeSection === section.id}
                onSelect={setActiveSection}
                controls={`report-section-${section.id}`}
              >
                {section.title}
              </SectionTab>
            ))}
          </div>
          <IconButton
            label={copy.report.toc}
            className="ml-auto"
            onClick={() => setShowToc((value) => !value)}
            aria-expanded={showToc}
          >
            <ListTree className="size-3.5" />
          </IconButton>
        </div>

        {showToc ? (
          <nav aria-label={copy.report.toc} className="border-t border-[var(--border-subtle)] px-3 py-1.5">
            <ol className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
              {sections.map((section) => (
                <li key={section.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setActiveSection(section.id);
                      setShowToc(false);
                    }}
                    className="text-[var(--text-tertiary)] transition-colors hover:text-[var(--accent)]"
                  >
                    {section.title}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
        ) : null}
      </header>

      {/* ★v6 G2a：联动条在**两种视图里位置相同** —— 报告头下方、章节内容之上。
          读者切换阅读模式时，"这段行情关联哪几段"不该跟着换位置（§19.5.7）。 */}
      {range && summary ? (
        <RangeHint
          reportId={summary.id}
          from={range.from}
          to={range.to}
          onClear={() => onClearRange?.()}
          onJumpToBlock={jumpToBlockById}
          className="shrink-0"
        />
      ) : null}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {view === 'full' && summary ? (
          /* ★v6 G2b：换容器不换渲染器 —— 全文视图复用同一个 `ReportRenderer`（O-7）。 */
          <ReadingView
            reportId={summary.id}
            doc={doc}
            activeSectionId={initialSection ?? undefined}
            highlightBlockId={highlightBlockId}
            onSectionChange={(sectionId) => setActiveSection(sectionId)}
            onJumpToBlock={jumpToBlockById}
            onHighlightClear={() => setHighlightBlockId(null)}
            returnFocusRef={modeButtonRef}
            quality={grounding}
          />
        ) : (
        <article
          id="report-body"
          tabIndex={-1}
          className="mx-auto max-w-[820px] px-5 py-4 outline-none"
          data-print="full"
        >
          <div className="mb-4">
            {/* ★A5：`headline` / `summary` 是顶层字段，不是块。它们用**不含 `#`** 的容器 id
                接入「未接地样本可点」，由 `jumpToBlockById` 的安全分支处理（高亮 + 滚动，
                不切章节 Tab）—— 写成 `summary#0` 会让 `parseBlockId()` 把章节切成 `summary`，
                正文整篇消失（R-E7）。 */}
            <h1
              data-block-id={HEADLINE_BLOCK_ID}
              className="scroll-mt-4 text-[19px] font-semibold leading-snug tracking-tight text-[var(--text-primary)] transition-colors data-[highlighted]:bg-[var(--warn-weak)]"
            >
              {doc.headline}
            </h1>
            <p className="mt-1 text-[11px] text-[var(--text-tertiary)]">
              {copy.report.asOf} {dateTime(doc.asOf, timezone)}
              {summary?.model ? ` · ${summary.model}` : ''}
              {summary?.durationMs ? ` · ${(summary.durationMs / 1000).toFixed(0)}s` : ''}
            </p>
            <p
              data-block-id={SUMMARY_BLOCK_ID}
              className="mt-2 scroll-mt-4 text-[14px] leading-[1.75] text-[var(--text-secondary)] transition-colors data-[highlighted]:bg-[var(--warn-weak)]"
            >
              {doc.summary}
            </p>
            {/* `data-block-id` + `quality` + `blockId`：后端把顶层 `doc.keyMetrics[]`
                的数字挂在这个 blockId 下（`numeric_claims`），所以门面卡既能显示未接地圆点/脚注，
                也能被徽标里的样本点中。 */}
            {doc.keyMetrics.length > 0 ? (
              <div
                className="mt-3 scroll-mt-4 rounded-[var(--radius-sm)] transition-colors data-[highlighted]:bg-[var(--warn-weak)]"
                data-block-id={KEY_METRICS_BLOCK_ID}
              >
                <MetricGrid
                  items={doc.keyMetrics}
                  compact
                  quality={grounding}
                  blockId={KEY_METRICS_BLOCK_ID}
                />
              </div>
            ) : null}
          </div>

          <div
            role={activeSection === 'all' ? undefined : 'tabpanel'}
            id={activeSection === 'all' ? undefined : `report-section-${activeSection}`}
            aria-labelledby={activeSection === 'all' ? undefined : `report-tab-${activeSection}`}
            tabIndex={activeSection === 'all' ? undefined : 0}
            className="space-y-6 outline-none"
          >
            {visibleSections.map((section) => (
              <section key={section.id} id={`sec-${section.id}`} className="scroll-mt-4">
                {activeSection === 'all' ? (
                  <h2 className="mb-2 border-b border-[var(--border-subtle)] pb-1 text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
                    {section.title}
                  </h2>
                ) : null}
                                  <ReportRenderer
                    blocks={section.items.map((item) => item.block)}
                    blockIds={section.items.map((item) => item.blockId)}
                    sources={sources}
                    quality={grounding}
                    onJumpToBlock={jumpToBlockById}
                  />
              </section>
            ))}
          </div>

          {doc.risks.length > 0 && (activeSection === 'all' || activeSection === 'risks') ? (
            <section className="mt-6" data-block-id={RISK_LIST_BLOCK_ID}>
              <h2 className="mb-2 border-b border-[var(--border-subtle)] pb-1 text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
                {copy.report.riskList}
              </h2>
              <ul className="space-y-1.5">
                {doc.risks.map((risk, index) => (
                  <li
                    key={index}
                    data-block-id={`${RISK_LIST_BLOCK_ID}[${index}]`}
                    className="flex scroll-mt-4 items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--border-subtle)] px-2.5 py-1.5 transition-colors data-[highlighted]:bg-[var(--warn-weak)]"
                  >
                    <Badge
                      tone={risk.severity === 'high' ? 'danger' : risk.severity === 'medium' ? 'warn' : 'neutral'}
                    >
                      {copy.report.severity[risk.severity]}
                    </Badge>
                    <span className="min-w-0">
                      <span className="block text-[12.5px] font-medium text-[var(--text-primary)]">{risk.title}</span>
                      <span className="block text-[11.5px] leading-snug text-[var(--text-tertiary)]">
                        触发：{risk.trigger}
                        {risk.mitigant ? ` · 应对：${risk.mitigant}` : ''}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <footer className="mt-6 border-t border-[var(--border-subtle)] pt-3">
            <p className="text-[10.5px] leading-relaxed text-[var(--text-tertiary)]">{doc.disclaimer}</p>
            {sources.length > 0 ? (
              <button
                type="button"
                onClick={() => openSourceDrawer(sources[0]!.id)}
                className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-[var(--accent)] hover:underline"
              >
                {copy.report.sources}（{sources.length}）<ArrowUpRight className="size-3" />
              </button>
            ) : null}
          </footer>
          </article>
        )}
      </div>

      {/* Mounted on first open: the chunk must not be fetched just because a
          report is on screen (AC-35 is a runtime promise, not only a build one). */}
      {summary && askMounted ? (
        <AskPanel
          reportId={summary.id}
          open={askOpen}
          onOpenChange={setAskOpen}
          deepAvailable={deepAvailable}
          dailyLimit={askDailyLimit}
          onJumpToBlock={jumpToBlock}
          onJumpToSource={openSourceDrawer}
          triggerRef={askTriggerRef}
        />
      ) : null}

      <SourceDrawer sources={sources} />
    </section>
  );
}

/**
 * ★v6 G2b：`章节 | 全文` 分段控件（与 `VersionSwitcher` 同行）。
 *
 * 用 `role="tablist"` 是因为它就是"同一内容的两种视图"，而 `?view=` 的写入由上层用
 * `router.replace` 完成 —— 来回切模式不该污染浏览器后退栈（§19.3.4 的裁决）。
 */
function ReadingModeSwitch({
  value,
  onChange,
  disabled,
  buttonRef,
}: {
  value: ReadingMode;
  onChange?: (view: ReadingMode) => void;
  disabled?: boolean;
  buttonRef?: React.RefObject<HTMLButtonElement | null>;
}) {
  if (!onChange) return null;
  return (
    <div
      role="tablist"
      aria-label={copy.reading.mode}
      className="flex items-center gap-0.5 rounded-[var(--radius-sm)] bg-[var(--bg-surface-2)] p-0.5"
    >
      {(['tabs', 'full'] as const).map((mode) => {
        const active = value === mode;
        return (
          <button
            key={mode}
            ref={mode === 'full' ? buttonRef : undefined}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={disabled}
            onClick={() => onChange(mode)}
            className={cn(
              'rounded-[calc(var(--radius-sm)-2px)] px-1.5 py-0.5 text-[10.5px] font-medium transition-colors disabled:opacity-50',
              active
                ? 'bg-[var(--bg-canvas)] text-[var(--text-primary)] shadow-[var(--shadow-sm)]'
                : 'text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]',
            )}
          >
            {mode === 'tabs' ? copy.reading.tabs : copy.reading.full}
          </button>
        );
      })}
    </div>
  );
}

function SectionTab({
  id,
  active,
  onSelect,
  controls,
  children,
}: {
  id: string;
  active: boolean;
  onSelect: (id: string) => void;
  controls?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      id={`report-tab-${id}`}
      aria-selected={active}
      aria-controls={controls}
      tabIndex={active ? 0 : -1}
      onClick={() => onSelect(id)}
      className={cn(
        'relative shrink-0 select-none rounded-[var(--radius-sm)] px-2 py-1 text-[11.5px] font-medium transition-colors',
        active
          ? 'bg-[var(--bg-surface-2)] text-[var(--text-primary)]'
          : 'text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-secondary)]',
      )}
    >
      {children}
      {active ? (
        <span
          aria-hidden="true"
          className="absolute inset-x-1.5 -bottom-px h-[2px] rounded-full bg-[var(--accent)]"
        />
      ) : null}
    </button>
  );
}
