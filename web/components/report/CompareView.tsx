'use client';

import { ArrowRight, CircleCheck, GitCompareArrows, TriangleAlert, X } from 'lucide-react';
import { useCallback, useEffect, useId, useRef } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { deltaArrow, num } from '@/lib/format';
import type { BlockChange, DiffResult, MetricDelta, MetricToneDirection, Stance } from '@/lib/types';

import { Badge, type BadgeTone } from '../ui/Badge';
import { IconButton } from '../ui/Button';
import { Skeleton } from '../ui/Skeleton';

export interface CompareViewProps {
  diff: DiffResult | null;
  loading: boolean;
  error?: string | null;
  aVersion?: number;
  bVersion?: number;
  /** clicking a changed block jumps the report to that block (new side) */
  onJumpToBlock?: (blockId: string | null, sectionId: string) => void;
  onClose: () => void;
  triggerRef?: React.RefObject<HTMLElement | null>;
}

/* ── 语义色：唯一来源是服务端给出的 `tone` ───────────────────────────────── */

/**
 * AC-42：颜色只能来自 `delta.tone`（服务端 `direction × polarity` 的结果）。
 * 这里**不允许**看 `deltaPct` 的正负或 `direction`——「PE 上升是坏事」这条知识
 * 只存在于后端一处，前端再用数字符号猜一次就必然某天猜反。
 */
const TONE_TEXT: Record<MetricToneDirection, string> = {
  up: 'text-[var(--chart-up)]',
  down: 'text-[var(--chart-down)]',
  neutral: 'text-[var(--text-tertiary)]',
};

/** 涨跌箭头只看数字符号（这是"多少"），颜色与措辞只看 `tone`（这是"好坏"）。 */
const CHANGE_LABEL: Record<BlockChange['change'], string> = copy.compare.change;

const CHANGE_TONE: Record<BlockChange['change'], BadgeTone> = {
  added: 'accent',
  removed: 'danger',
  modified: 'info',
};

/**
 * `statusChanged` 传的是后端枚举字符串（ready / degraded / provisional / superseded）。
 * 这里只做展示映射，未知值原样输出——不猜、不吞。
 */
const STATUS_LABEL: Record<string, string> = {
  ready: '正常',
  degraded: '降级',
  provisional: '临时版',
  superseded: '已被取代',
};

function statusLabel(status: string | null | undefined): string {
  if (!status) return '—';
  return STATUS_LABEL[status] ?? status;
}

function stanceTone(stance: Stance): BadgeTone {
  if (stance === 'buy') return 'up';
  if (stance === 'sell') return 'down';
  return 'neutral';
}

/** 「结构性差异」的判据：任何一条变化数组非空即成差异；`summary` 只是文案，不参与判定。 */
function hasStructuralChanges(diff: DiffResult): boolean {
  return Boolean(
    diff.statusChanged ||
      diff.ratingChanged ||
      diff.metricsDelta.length > 0 ||
      diff.sectionsAdded.length > 0 ||
      diff.sectionsRemoved.length > 0 ||
      diff.blocksChanged.length > 0,
  );
}

/**
 * 版本比较（A3，spec §18.5.4 / §18.3.5 / AC-34 / AC-42）。
 *
 * 为什么是"结构 diff + 列表"而不是双栏文本对照：中文段落的字符级 diff 在读者眼里
 * 几乎不可读（改一个字会长出一整片高亮），所以采用「变更摘要 + 指标表 + 变更块清单
 * + 点击定位」，诚实且够用（§18.3.5 的取舍，写在这里免得下游自行发挥）。
 *
 * 由 `next/dynamic` 懒挂载：本模块顶层没有任何副作用代码。
 */
export function CompareView({
  diff,
  loading,
  error,
  aVersion,
  bVersion,
  onJumpToBlock,
  onClose,
  triggerRef,
}: CompareViewProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const regionId = useId();
  const summaryId = `${regionId}-summary`;
  const titleId = `${regionId}-title`;

  /**
   * 关闭时必须把焦点还给触发元素（AC-36）。CompareView 只在打开时挂载，所以用
   * 不了 `useReturnFocus` 的"从开到关"分支——它的触发元素由 `triggerRef` 传入
   * （`ReportPanel` 里的 `DiffBadge`），关闭时先还焦点再通知父层卸载。
   */
  const handleClose = useCallback(() => {
    const trigger = triggerRef?.current;
    if (trigger && typeof trigger.focus === 'function') trigger.focus();
    onClose();
  }, [onClose, triggerRef]);

  // Esc 关闭：挂在 document 上而不是根节点上，面板内容还没渲染完（loading）时也能退。
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      handleClose();
    };
    document.addEventListener('keydown', listener);
    return () => document.removeEventListener('keydown', listener);
  }, [handleClose]);

  // 进入即聚焦面板（键盘用户 Tab 进来不会落在背后的报告里）
  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  const empty = !loading && !error && (!diff || !hasStructuralChanges(diff));
  const summary = diff?.summary?.trim() ?? '';

  return (
    <section
      ref={rootRef}
      role="region"
      aria-label={copy.compare.title}
      aria-describedby={summary ? summaryId : undefined}
      aria-busy={loading || undefined}
      tabIndex={-1}
      data-print="hide"
      className={cn(
        'flex h-full min-h-0 min-w-0 flex-col overflow-hidden outline-none',
        'bg-[var(--bg-surface-1)] text-[var(--text-primary)]',
      )}
    >
      <header className="flex shrink-0 items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-1.5">
        <GitCompareArrows className="size-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
        <h2 id={titleId} className="text-[12.5px] font-semibold tracking-tight text-[var(--text-primary)]">
          {copy.compare.title}
        </h2>
        <VersionChip version={aVersion} side="left" />
        {aVersion !== undefined || bVersion !== undefined ? (
          <ArrowRight className="size-3 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
        ) : null}
        <VersionChip version={bVersion} side="right" />
        <IconButton label={copy.compare.close} className="ml-auto" onClick={handleClose}>
          <X className="size-3.5" />
        </IconButton>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {loading ? <LoadingRows /> : null}

        {!loading && error ? (
          <div role="alert" className="rounded-[var(--radius-md)] border border-[var(--danger)]/35 bg-[var(--danger-weak)] px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-[var(--danger)]">
              <TriangleAlert className="size-3.5" aria-hidden="true" />
              {copy.compare.loadFailed}
            </p>
            <p className="mt-1 break-words text-[11.5px] leading-relaxed text-[var(--text-secondary)]">{error}</p>
          </div>
        ) : null}

        {empty ? (
          <div className="flex flex-col items-center gap-2 rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-4 py-8 text-center shadow-[var(--shadow-card)]">
            <CircleCheck className="size-5 text-[var(--text-tertiary)]" aria-hidden="true" />
            <p className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">{copy.compare.noChanges}</p>
          </div>
        ) : null}

        {!loading && !error && diff && !empty ? (
          <div className="space-y-4">
            {summary ? (
              <p id={summaryId} className="text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
                {summary}
              </p>
            ) : null}

            {diff.ratingChanged ? (
              <Card title={copy.compare.ratingChanged}>
                <div className="flex flex-wrap items-center gap-2">
                  <StancePill stance={diff.ratingChanged.from.stance} />
                  <ArrowRight className="size-3 text-[var(--text-tertiary)]" aria-hidden="true" />
                  <StancePill stance={diff.ratingChanged.to.stance} />
                  {/* copy.compare 里没有"目标价"这一项，避免为此改公共文案表，就地写死 */}
                  <span className="mono ml-1 text-[11px] text-[var(--text-tertiary)]">
                    目标价 {diff.ratingChanged.from.target ?? '—'}
                    <span aria-hidden="true"> → </span>
                    {diff.ratingChanged.to.target ?? '—'}
                  </span>
                </div>
              </Card>
            ) : null}

            {diff.statusChanged ? (
              <Card title={copy.compare.statusChanged}>
                <p className="text-[12px] text-[var(--text-secondary)]">
                  {statusLabel(diff.statusChanged.from)}
                  <span aria-hidden="true" className="px-1.5 text-[var(--text-tertiary)]">
                    →
                  </span>
                  {statusLabel(diff.statusChanged.to)}
                </p>
              </Card>
            ) : null}

            {diff.metricsDelta.length > 0 ? (
              <Card title={copy.compare.metricsDelta} padded={false}>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-left">
                    <caption className="sr-only">{copy.compare.metricsDelta}</caption>
                    <thead>
                      <tr className="bg-[var(--bg-surface-2)] text-[10.5px] font-medium text-[var(--text-tertiary)]">
                        <th scope="col" className="px-2.5 py-1.5 text-left font-medium">
                          指标
                        </th>
                        <th scope="col" className="px-2.5 py-1.5 text-right font-medium">
                          旧版
                        </th>
                        <th scope="col" className="px-2.5 py-1.5 text-right font-medium">
                          新版
                        </th>
                        <th scope="col" className="px-2.5 py-1.5 text-right font-medium">
                          变化
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {diff.metricsDelta.map((metric) => (
                        <MetricDeltaRow key={`${metric.label}-${metric.tone}`} metric={metric} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            ) : null}

            {diff.sectionsAdded.length > 0 ? (
              <Card title={copy.compare.sectionsAdded}>
                <div className="flex flex-wrap gap-1">
                  {diff.sectionsAdded.map((section) => (
                    <Badge key={`added-${section}`} tone="accent">
                      <span aria-hidden="true">＋</span>
                      {section}
                    </Badge>
                  ))}
                </div>
              </Card>
            ) : null}

            {diff.sectionsRemoved.length > 0 ? (
              <Card title={copy.compare.sectionsRemoved}>
                <div className="flex flex-wrap gap-1">
                  {diff.sectionsRemoved.map((section) => (
                    <Badge key={`removed-${section}`} tone="neutral">
                      <span aria-hidden="true">−</span>
                      {section}
                    </Badge>
                  ))}
                </div>
              </Card>
            ) : null}

            {diff.blocksChanged.length > 0 ? (
              <Card
                title={copy.compare.blocksChanged}
                aside={fill(copy.compare.blocksCount, { count: diff.blocksChanged.length })}
                padded={false}
              >
                <ul className="divide-y divide-[var(--border-subtle)]">
                  {diff.blocksChanged.map((change, index) => (
                    <li key={`${change.sectionId}-${change.blockType}-${change.change}-${index}`}>
                      <BlockChangeRow change={change} onJumpToBlock={onJumpToBlock} />
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

/* ── pieces ─────────────────────────────────────────────────────────────── */

function VersionChip({ version, side }: { version?: number; side: 'left' | 'right' }) {
  if (version === undefined) return null;
  const template = side === 'left' ? copy.compare.left : copy.compare.right;
  return (
    <span className="mono shrink-0 rounded-[var(--radius-sm)] border border-[var(--border-subtle)] px-1.5 py-px text-[10.5px] text-[var(--text-tertiary)]">
      {fill(template, { version })}
    </span>
  );
}

function Card({
  title,
  aside,
  padded = true,
  children,
}: {
  title: string;
  aside?: string;
  padded?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface-1)] shadow-[var(--shadow-card)]">
      <div className="flex items-center gap-2 px-2.5 pb-1 pt-2">
        <h3 className="text-[11px] font-medium text-[var(--text-tertiary)]">{title}</h3>
        {aside ? <span className="num ml-auto text-[10.5px] text-[var(--text-tertiary)]">{aside}</span> : null}
      </div>
      <div className={cn(padded && 'px-2.5 pb-2.5')}>{children}</div>
    </section>
  );
}

function StancePill({ stance }: { stance?: Stance | null }) {
  if (!stance) return <span className="text-[12px] text-[var(--text-tertiary)]">—</span>;
  return <Badge tone={stanceTone(stance)}>{copy.report.stance[stance]}</Badge>;
}

function MetricDeltaRow({ metric }: { metric: MetricDelta }) {
  const tone = metric.tone;
  return (
    <tr className="border-t border-[var(--border-subtle)]">
      <th scope="row" className="px-2.5 py-1.5 text-left text-[12px] font-normal text-[var(--text-secondary)]">
        {metric.label}
      </th>
      <td className="num px-2.5 py-1.5 text-right text-[12px] text-[var(--text-tertiary)]">{num(metric.before)}</td>
      <td className="num px-2.5 py-1.5 text-right text-[12px] text-[var(--text-primary)]">{num(metric.after)}</td>
      {/* 颜色来自 tone；箭头来自数字符号；措辞来自 tone——三者并列，不靠颜色单独表意 */}
      <td className={cn('num px-2.5 py-1.5 text-right text-[12px] font-medium', TONE_TEXT[tone])} data-tone={tone}>
        <span>{deltaArrow(metric.deltaPct)}</span>
        <span className="ml-1 text-[10.5px] font-normal">{copy.compare.tone[tone]}</span>
      </td>
    </tr>
  );
}

function BlockChangeRow({
  change,
  onJumpToBlock,
}: {
  change: BlockChange;
  onJumpToBlock?: (blockId: string | null, sectionId: string) => void;
}) {
  const body = (
    <>
      <Badge tone={CHANGE_TONE[change.change]}>{CHANGE_LABEL[change.change]}</Badge>
      <span className="mono truncate text-[11px] text-[var(--text-secondary)]">{change.sectionId}</span>
      <span className="truncate text-[11px] text-[var(--text-tertiary)]">{change.blockType}</span>
      {onJumpToBlock ? (
        <ArrowRight
          className="ml-auto size-3 shrink-0 text-[var(--text-tertiary)] opacity-0 transition-opacity duration-[var(--dur-fast)] group-hover:opacity-100 group-focus-visible:opacity-100"
          aria-hidden="true"
        />
      ) : null}
    </>
  );

  // 被删除的块在新版里没有 blockIdB，此时传 null：让上层只做"跳到该章节"
  if (!onJumpToBlock) {
    return <div className="flex items-center gap-2 px-2.5 py-1.5">{body}</div>;
  }

  return (
    <button
      type="button"
      onClick={() => onJumpToBlock(change.blockIdB ?? null, change.sectionId)}
      className="group flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors duration-[var(--dur-fast)] hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-active)]"
    >
      {body}
    </button>
  );
}

function LoadingRows() {
  return (
    <div role="status" aria-live="polite" className="space-y-4">
      <span className="sr-only">正在加载版本比较…</span>
      <Skeleton className="h-3.5 w-3/4" />
      <div className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border-subtle)] p-2.5">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="flex items-center gap-3">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="ml-auto h-3 w-12" />
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}
