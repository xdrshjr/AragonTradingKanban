'use client';

import { Archive, ChevronDown, Download, Pin, PinOff, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { costLabel, dateTime, relativeTime, versionLabel } from '@/lib/format';
import { missingCounts } from '@/lib/grounding';
import type { ReportSummary } from '@/lib/types';

import { Badge, Tag } from '../ui/Badge';
import { IconButton } from '../ui/Button';

/**
 * 版本列表里那条质量判词的悬浮文案（★A6）。
 *
 * 一道纪律：**跨口径不给可比百分比**。判词是永久落库的，而判据会变 ——
 * 把两代规则算出来的 `90%` 并排放着，等于宣称它们可比（实测同一份输入：
 * 旧口径 323/141，新口径 264/24）。所以旧载荷只说「早期版本」，不说数字。
 */
function groundingTitle(report: ReportSummary): string {
  const grounding = report.quality.grounding;
  if (!grounding) {
    // 连判词都没有：只有 `groundedRatio` 这一个旧字段，它不是本代口径。
    return `${copy.trust.rulesUnknown} · ${copy.trust.legacy}`;
  }
  if (!grounding.rules) {
    return `${copy.trust.rulesUnknown} · ${copy.trust.legacy}`;
  }
  const counts = missingCounts(grounding);
  const rules = fill(copy.trust.rules, { rules: grounding.rules });
  return `${rules} · ${fill(copy.trust.counts, { distinct: counts.distinct, references: counts.references })}`;
}

/**
 * 这一版的判词是不是**另一代口径**算的（★R3-4）。
 *
 * 列表里每一行都挂一个 `claims-v3` 标签没有信息量（那一列已经挤着 最新/临时/降级/示例），
 * 而且面板的悬浮里已经说了口径。只有"这个版本与当前口径不同"才是读者必须看见的那一条 ——
 * 因为**只有它会导致并排比较出错**。参照口径取列表里第一份有判词的报告（列表按新到旧）。
 */
function isForeignRules(report: ReportSummary, currentRules: string | null): boolean {
  if (!report.quality.grounding) return false; // 没有判词 → 由"降级/示例"徽标负责，不重复喊
  return (report.quality.grounding.rules ?? null) !== currentRules;
}

export interface VersionSwitcherProps {
  reports: ReportSummary[];
  activeId: string | null;
  onSelect: (reportId: string) => void;
  onPin?: (reportId: string, pinned: boolean) => void;
  onArchive?: (reportId: string, archived: boolean) => void;
  onDelete?: (reportId: string) => void;
  onExport?: (format: 'html' | 'md', reportId?: string) => void;
  disabled?: boolean;
}

/**
 * Version dropdown (spec §6.3). Shows version, age, model and the quality flags
 * that actually matter when choosing what to read: 临时版 / 降级 / 示例.
 */
export function VersionSwitcher({
  reports,
  activeId,
  onSelect,
  onPin,
  onArchive,
  onDelete,
  onExport,
  disabled,
}: VersionSwitcherProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const active = reports.find((report) => report.id === activeId) ?? reports[0] ?? null;
  // ★R3-4：当前口径 = 列表里第一份带判词的报告的口径（列表按新到旧）。
  const currentRules =
    reports.find((report) => report.quality.grounding?.rules)?.quality.grounding?.rules ?? null;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (reports.length === 0) return null;

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        className={cn(
          'flex h-7 items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-strong)]',
          'bg-[var(--bg-surface-1)] px-2 text-[12px] text-[var(--text-secondary)] transition-colors',
          'hover:bg-[var(--bg-surface-2)] hover:text-[var(--text-primary)] disabled:opacity-50',
        )}
      >
        <span className="mono text-[11px]">
          {active ? versionLabel(active.version, active.provisional) : '—'}
        </span>
        {active?.isLatest ? <Badge tone="accent">最新</Badge> : null}
        <ChevronDown className="size-3" aria-hidden="true" />
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="报告版本"
          className="absolute right-0 top-8 z-20 max-h-[380px] w-[320px] overflow-y-auto rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]"
        >
          {reports.map((report) => {
            const selected = report.id === activeId;
            return (
              <div
                key={report.id}
                className={cn(
                  'group relative rounded-[var(--radius-sm)] px-2 py-1.5 transition-colors',
                  selected ? 'bg-[var(--accent-weak)]' : 'hover:bg-[var(--bg-hover)]',
                )}
              >
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    onSelect(report.id);
                    setOpen(false);
                  }}
                  className="block w-full text-left"
                >
                  <span className="flex items-center gap-1.5">
                    <span className="mono text-[11px] font-semibold text-[var(--text-primary)]">
                      {versionLabel(report.version, report.provisional)}
                    </span>
                    {report.isLatest ? <Badge tone="accent">最新</Badge> : null}
                    {report.provisional ? <Badge tone="warn">临时</Badge> : null}
                    {report.status === 'degraded' ? (
                      <Badge tone="warn" title={groundingTitle(report)}>
                        降级
                      </Badge>
                    ) : null}
                    {/* ★A6 / ★R3-4：口径标签。只在**与本列表当前口径不同**时出现：
                        一致时它是噪音（这一列已有 最新/临时/降级/示例），不同时它是
                        读者唯一必须知道的事 —— 这两个百分比不可比。
                        这里读 `quality.grounding` 而不是状态列：`superseded` 会把 `status`
                        覆盖掉（`report_service.py:129`），于是旧版的质量判词在列表里
                        凭空消失 —— 那正是 N2 点名的"状态列双语义"。 */}
                    {isForeignRules(report, currentRules) ? (
                      <Tag title={groundingTitle(report)}>
                        {report.quality.grounding?.rules ?? copy.trust.rulesUnknown}
                      </Tag>
                    ) : null}
                    {report.quality.source === 'mock' ? <Badge tone="info">示例</Badge> : null}
                    {report.pinned ? <Pin className="size-2.5 text-[var(--accent)]" aria-label="已置顶" /> : null}
                  </span>
                  <span className="mt-0.5 block truncate text-[11.5px] text-[var(--text-secondary)]">
                    {report.headline || report.title}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-[var(--text-tertiary)]">
                    <span title={dateTime(report.generatedAt)}>{relativeTime(report.generatedAt)}</span>
                    {report.model ? <span className="mono">{report.model}</span> : null}
                    <span>{costLabel(report.costUsd, report.costKnown)}</span>
                  </span>
                </button>

                <div className="absolute right-1 top-1 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                  {onPin ? (
                    <IconButton
                      label={report.pinned ? copy.report.unpin : copy.report.pin}
                      className="!size-6"
                      onClick={() => onPin(report.id, !report.pinned)}
                    >
                      {report.pinned ? <PinOff className="size-3" /> : <Pin className="size-3" />}
                    </IconButton>
                  ) : null}
                  {onArchive ? (
                    <IconButton
                      label={report.archived ? copy.report.unarchive : copy.report.archive}
                      className="!size-6"
                      onClick={() => onArchive(report.id, !report.archived)}
                    >
                      <Archive className="size-3" />
                    </IconButton>
                  ) : null}
                  {onExport ? (
                    <IconButton
                      label={`导出 ${report.title || `v${report.version}`} 的 HTML`}
                      className="!size-6"
                      onClick={() => onExport('html', report.id)}
                    >
                      <Download className="size-3" />
                    </IconButton>
                  ) : null}
                  {onDelete && !report.isLatest ? (
                    <IconButton label={copy.report.deleteVersion} className="!size-6" onClick={() => onDelete(report.id)}>
                      <Trash2 className="size-3 text-[var(--danger)]" />
                    </IconButton>
                  ) : null}
                </div>
              </div>
            );
          })}
          <div className="mt-1 border-t border-[var(--border-subtle)] px-2 pb-1 pt-1.5">
            <Tag>版本会永久保留最近 20 版（置顶版不限）</Tag>
          </div>
        </div>
      ) : null}
    </div>
  );
}
