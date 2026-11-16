'use client';

import { Archive, ChevronDown, Download, Pin, PinOff, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { costLabel, dateTime, relativeTime, versionLabel } from '@/lib/format';
import type { ReportSummary } from '@/lib/types';

import { Badge, Tag } from '../ui/Badge';
import { IconButton } from '../ui/Button';

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
                      <Badge tone="warn" title={`接地率 ${(report.quality.groundedRatio * 100).toFixed(0)}%`}>
                        降级
                      </Badge>
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
