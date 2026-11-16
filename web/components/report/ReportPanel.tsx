'use client';

import { ArrowUpRight, Clock, Download, ListTree, MessageCircleQuestion, RefreshCw } from 'lucide-react';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { blockIdOf } from '@/lib/blocks';
import { copy } from '@/lib/copy';
import { costLabel, dateTime, relativeTime } from '@/lib/format';
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
const AskPanel = dynamic(() => import('../ask/AskPanel').then((module) => module.AskPanel), {
  ssr: false,
  loading: () => null,
});

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
}: ReportPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const tabsRef = useRef<HTMLDivElement | null>(null);
  const askTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [activeSection, setActiveSection] = useState<string>('all');
  const [showToc, setShowToc] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askMounted, setAskMounted] = useState(false);
  const [highlightBlockId, setHighlightBlockId] = useState<string | null>(null);
  const openSourceDrawer = useUiStore((state) => state.openSourceDrawer);
  const sources = useMemo<SourceRef[]>(() => detail?.doc.sources ?? [], [detail]);

  useEffect(() => {
    if (askOpen) setAskMounted(true);
  }, [askOpen]);

  const doc = detail?.doc ?? null;
  const summary = detail?.report ?? null;

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
                  ? ` · ${summary.quality.ungroundedCount} 处数字未能在快照中找到来源`
                  : ''
              }`}
            >
              {copy.report.degraded}
            </Badge>
          ) : null}
          {summary?.quality.source === 'mock' ? <Badge tone="info">{copy.report.mock}</Badge> : null}

          <div className="ml-auto flex items-center gap-1">
            <span className="hidden text-[10.5px] text-[var(--text-tertiary)] sm:inline">
              {copy.report.generatedAt} {relativeTime(summary?.generatedAt)}
              {summary ? ` · ${costLabel(summary.costUsd, summary.costKnown)}` : ''}
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

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <article
          id="report-body"
          tabIndex={-1}
          className="mx-auto max-w-[820px] px-5 py-4 outline-none"
          data-print="full"
        >
          <div className="mb-4">
            <h1 className="text-[19px] font-semibold leading-snug tracking-tight text-[var(--text-primary)]">
              {doc.headline}
            </h1>
            <p className="mt-1 text-[11px] text-[var(--text-tertiary)]">
              {copy.report.asOf} {dateTime(doc.asOf, timezone)}
              {summary?.model ? ` · ${summary.model}` : ''}
              {summary?.durationMs ? ` · ${(summary.durationMs / 1000).toFixed(0)}s` : ''}
            </p>
            <p className="mt-2 text-[14px] leading-[1.75] text-[var(--text-secondary)]">{doc.summary}</p>
            {doc.keyMetrics.length > 0 ? (
              <div className="mt-3">
                <MetricGrid items={doc.keyMetrics} compact />
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
                  />
              </section>
            ))}
          </div>

          {doc.risks.length > 0 && (activeSection === 'all' || activeSection === 'risks') ? (
            <section className="mt-6">
              <h2 className="mb-2 border-b border-[var(--border-subtle)] pb-1 text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
                {copy.report.riskList}
              </h2>
              <ul className="space-y-1.5">
                {doc.risks.map((risk, index) => (
                  <li
                    key={index}
                    className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--border-subtle)] px-2.5 py-1.5"
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
