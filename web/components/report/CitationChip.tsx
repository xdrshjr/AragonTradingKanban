'use client';

import { useCallback, useMemo } from 'react';

import { cn } from '@/lib/cn';
import { useUiStore } from '@/lib/store';
import type { SourceRef } from '@/lib/types';

/**
 * Citation chips and inline date jumps (spec §3.3 / AC-7).
 *
 * `[S1]` / `[S1,S2]` are turned into buttons that open the source drawer, and any
 * `YYYY-MM-DD` becomes a button that focuses the K-line on that range. Text is
 * still plain text — this is tokenisation, not HTML injection (§11.3).
 */

const CITATION_RE = /\[(S\d+(?:\s*,\s*S\d+)*)\]/g;
const DATE_RE = /\b(\d{4}-\d{2}-\d{2})\b/g;

export interface CitationChipProps {
  sourceId: string;
  source?: SourceRef;
  className?: string;
}

export function CitationChip({ sourceId, source, className }: CitationChipProps) {
  const openSourceDrawer = useUiStore((state) => state.openSourceDrawer);
  const label = source ? `${sourceId} · ${source.title}` : sourceId;
  return (
    <button
      type="button"
      onClick={() => openSourceDrawer(sourceId)}
      title={label}
      aria-label={`查看来源 ${label}`}
      className={cn(
        'mx-0.5 inline-flex h-[17px] min-w-[17px] translate-y-[-1px] items-center justify-center',
        'rounded border border-[var(--accent)]/30 bg-[var(--accent-weak)] px-1',
        'font-mono text-[10px] font-semibold leading-none text-[var(--accent)]',
        'align-middle transition-colors hover:border-[var(--accent)]/70 hover:bg-[var(--accent)]/20',
        className,
      )}
    >
      {sourceId}
    </button>
  );
}

export interface DateChipProps {
  date: string;
  className?: string;
}

export function DateChip({ date, className }: DateChipProps) {
  const focusChartRange = useUiStore((state) => state.focusChartRange);
  const setKlineDrawer = useUiStore((state) => state.setKlineDrawer);

  const handleClick = useCallback(() => {
    // ±3 trading days feels right: enough context to see the bar in situ.
    const start = new Date(`${date}T00:00:00Z`);
    if (Number.isNaN(start.getTime())) return;
    const from = new Date(start);
    from.setUTCDate(from.getUTCDate() - 5);
    const to = new Date(start);
    to.setUTCDate(to.getUTCDate() + 5);
    const iso = (value: Date) => value.toISOString().slice(0, 10);
    focusChartRange(iso(from), iso(to));
    setKlineDrawer(true);
  }, [date, focusChartRange, setKlineDrawer]);

  return (
    <button
      type="button"
      onClick={handleClick}
      title={`在 K 线中定位 ${date}`}
      className={cn(
        'num mx-0.5 rounded-[3px] px-0.5 text-[var(--text-secondary)] underline decoration-dotted',
        'decoration-[var(--border-strong)] underline-offset-2 transition-colors hover:text-[var(--accent)]',
        className,
      )}
    >
      {date}
    </button>
  );
}

export interface RichTextProps {
  text: string;
  sources?: SourceRef[];
  className?: string;
}

/**
 * Renders prose with citation and date tokens. Everything else stays a text node,
 * which is what makes XSS structurally impossible here.
 */
export function RichText({ text, sources = [], className }: RichTextProps) {
  const byId = useMemo(() => new Map(sources.map((source) => [source.id, source])), [sources]);

  const nodes = useMemo(() => {
    const parts: React.ReactNode[] = [];
    // One pass over the text, matching either token kind at each position.
    const pattern = new RegExp(`${CITATION_RE.source}|${DATE_RE.source}`, 'g');
    let cursor = 0;
    let key = 0;
    let match: RegExpExecArray | null;

    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) {
        parts.push(text.slice(cursor, match.index));
      }
      const raw = match[0];
      if (raw.startsWith('[')) {
        const ids = raw.slice(1, -1).split(',').map((item) => item.trim());
        ids.forEach((id, index) => {
          if (index > 0) parts.push(<span key={`sep-${key++}`}> </span>);
          parts.push(<CitationChip key={`cite-${key++}`} sourceId={id} source={byId.get(id)} />);
        });
      } else {
        parts.push(<DateChip key={`date-${key++}`} date={raw} />);
      }
      cursor = match.index + raw.length;
    }
    if (cursor < text.length) parts.push(text.slice(cursor));
    return parts;
  }, [text, byId]);

  return <span className={className}>{nodes}</span>;
}
