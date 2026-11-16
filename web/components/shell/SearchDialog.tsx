'use client';

/**
 * 统一检索入口（spec §18.3.4 / §18.5.3 / AC-32 / AC-36）。
 *
 * 标的命中（symbol_resolver 评分）与报告正文命中（FTS 块级命中）共用一次
 * `GET /search`，左栏搜索框与 ⌘K 走同一端点——避免两套检索语义。
 *
 * 两处刻意的实现约束：
 * 1. `snippet` 由服务端从**块原文**生成，带 `<mark>` 高亮。这里按标签切成文本节点
 *    再包 `<mark>`，绝不注入原始 HTML（§11.3 / AC-17：XSS 面为零）。
 * 2. 请求带单调序号，迟到的响应直接丢弃；否则边打边看时旧结果会盖掉新结果。
 */

import { Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { marketLabel } from '@/lib/format';
import { modifierLabel } from '@/lib/keyboard';
import type { InstrumentHit, ReportHit, SearchResponse } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Dialog, DialogContent } from '../ui/Dialog';
import { Kbd } from '../ui/Tooltip';

const DEBOUNCE_MS = 180;
const LIMIT = 20;
const LIST_ID = 'search-dialog-listbox';

export interface SearchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectSymbol: (symbol: string) => void;
  /** deep-link target: /{symbol}?report=&section=&block= */
  onSelectHit: (hit: ReportHit) => void;
}

type Row = { kind: 'instrument'; hit: InstrumentHit } | { kind: 'report'; hit: ReportHit };

interface IndexedRow {
  index: number;
  key: string;
  row: Row;
}

function rowDomId(index: number): string {
  return `${LIST_ID}-option-${index}`;
}

/**
 * 服务端在 snippet 里用 `<mark>` 标出命中词。按标签切成文本节点，奇数下标是命中片段；
 * 其余一律当纯文本渲染（React 会转义），因此标签串永远不会变成 DOM 结构。
 */
function SnippetText({ snippet }: { snippet: string }) {
  const parts = snippet.split(/<\/?mark>/i);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <mark
            key={`mark-${index}`}
            className="rounded-[2px] bg-[var(--accent-weak)] px-0.5 text-[var(--accent)]"
          >
            {part}
          </mark>
        ) : (
          <span key={`text-${index}`}>{part}</span>
        ),
      )}
    </>
  );
}

export function SearchDialog({ open, onOpenChange, onSelectSymbol, onSelectHit }: SearchDialogProps) {
  const [query, setQuery] = useState('');
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const requestSeq = useRef(0);

  const trimmed = query.trim();

  // 每次打开都从干净状态开始：上次的命中和高亮不该跟着弹窗一起回来。
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setResponse(null);
    setError(null);
    setLoading(false);
    setActiveIndex(0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (!trimmed) {
      requestSeq.current += 1;
      setResponse(null);
      setError(null);
      setLoading(false);
      return;
    }

    const seq = (requestSeq.current += 1);
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .search(trimmed, LIMIT)
        .then((next) => {
          if (seq !== requestSeq.current) return; // 慢响应不许覆盖新结果
          setResponse(next);
          setError(null);
        })
        .catch((cause: unknown) => {
          if (seq !== requestSeq.current) return;
          setResponse(null);
          setError(cause instanceof ApiError ? cause.message : copy.search.error);
        })
        .finally(() => {
          if (seq !== requestSeq.current) return;
          setLoading(false);
        });
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [open, trimmed]);

  const rows = useMemo<IndexedRow[]>(() => {
    if (!response) return [];
    const list: IndexedRow[] = [];
    response.instruments.forEach((hit) => {
      list.push({ index: list.length, key: `ins:${hit.symbol}`, row: { kind: 'instrument', hit } });
    });
    response.reports.forEach((hit, position) => {
      list.push({
        index: list.length,
        key: `rep:${hit.reportId}:${hit.sectionId}:${hit.blockId || position}`,
        row: { kind: 'report', hit },
      });
    });
    return list;
  }, [response]);

  useEffect(() => {
    setActiveIndex(0);
  }, [rows]);

  const activate = useCallback(
    (row: Row | undefined) => {
      if (!row) return;
      if (row.kind === 'instrument') onSelectSymbol(row.hit.symbol);
      else onSelectHit(row.hit);
      onOpenChange(false);
    },
    [onOpenChange, onSelectHit, onSelectSymbol],
  );

  // ↑↓ 在两个分组之间连续移动（是同一个扁平列表，不是分组的局部移动）。
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((value) => Math.min(value + 1, Math.max(rows.length - 1, 0)));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((value) => Math.max(value - 1, 0));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      activate(rows[activeIndex]?.row);
    }
    // Esc 交给 Radix Dialog：它负责关闭并把焦点还给触发者（AC-36）。
  };

  const renderRow = (entry: IndexedRow) => {
    const active = entry.index === activeIndex;
    const shared = cn(
      'w-full cursor-pointer rounded-[var(--radius-sm)] px-2 py-1.5 text-left transition-colors',
      active ? 'bg-[var(--accent-weak)]' : 'hover:bg-[var(--bg-hover)]',
    );

    if (entry.row.kind === 'instrument') {
      const hit = entry.row.hit;
      return (
        <div
          key={entry.key}
          id={rowDomId(entry.index)}
          role="option"
          aria-selected={active}
          data-active={active ? 'true' : undefined}
          onMouseEnter={() => setActiveIndex(entry.index)}
          onClick={() => activate(entry.row)}
          className={cn(shared, 'flex items-center justify-between gap-2')}
        >
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="min-w-0 truncate text-[12.5px] text-[var(--text-primary)]">{hit.name}</span>
            <span className="mono shrink-0 text-[10.5px] text-[var(--text-tertiary)]">{hit.symbol}</span>
          </span>
          <span className="shrink-0 text-[10.5px] text-[var(--text-tertiary)]">{marketLabel(hit.market)}</span>
        </div>
      );
    }

    const hit = entry.row.hit;
    return (
      <div
        key={entry.key}
        id={rowDomId(entry.index)}
        role="option"
        aria-selected={active}
        data-active={active ? 'true' : undefined}
        onMouseEnter={() => setActiveIndex(entry.index)}
        onClick={() => activate(entry.row)}
        className={cn(shared, 'block')}
      >
        <span className="flex items-baseline gap-1.5">
          <span className="mono shrink-0 text-[10.5px] text-[var(--text-tertiary)]">{hit.symbol}</span>
          <span className="min-w-0 truncate text-[12.5px] text-[var(--text-primary)]">{hit.title}</span>
        </span>
        <span className="mt-0.5 flex items-baseline gap-1.5 text-[11px] leading-snug text-[var(--text-tertiary)]">
          <span className="shrink-0 text-[var(--accent)]">{hit.sectionTitle}</span>
          <span className="min-w-0 truncate">
            <SnippetText snippet={hit.snippet} />
          </span>
        </span>
      </div>
    );
  };

  const instrumentRows = rows.filter((entry) => entry.row.kind === 'instrument');
  const reportRows = rows.filter((entry) => entry.row.kind === 'report');

  const body = (() => {
    if (!trimmed) {
      return <p className="px-3 py-6 text-center text-[11.5px] text-[var(--text-tertiary)]">{copy.search.emptyQuery}</p>;
    }
    if (error) {
      return (
        <p role="alert" className="px-3 py-6 text-center text-[11.5px] text-[var(--danger)]">
          {copy.search.error}
          <span className="mt-1 block text-[10.5px] text-[var(--text-tertiary)]">{error}</span>
        </p>
      );
    }
    if (loading && !response) {
      return <p className="px-3 py-6 text-center text-[11.5px] text-[var(--text-tertiary)]">{copy.search.searching}</p>;
    }
    if (rows.length === 0) {
      // copy 里没有单独的「无结果」串，复用标的组的措辞（避免在组件里硬编码中文）。
      return (
        <p className="px-3 py-6 text-center text-[11.5px] text-[var(--text-tertiary)]">{copy.search.noInstruments}</p>
      );
    }
    return (
      <div role="listbox" id={LIST_ID} aria-label={copy.search.title}>
        {instrumentRows.length > 0 ? (
          <div role="group" aria-label={copy.search.instruments} className="mb-1">
            <p aria-hidden="true" className="px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
              {copy.search.instruments}
            </p>
            {instrumentRows.map(renderRow)}
          </div>
        ) : null}
        {reportRows.length > 0 ? (
          <div role="group" aria-label={copy.search.reports} className="mb-1">
            <p aria-hidden="true" className="px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
              {copy.search.reports}
            </p>
            {reportRows.map(renderRow)}
          </div>
        ) : null}
      </div>
    );
  })();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={copy.search.title}
        description={copy.search.placeholder}
        hideClose
        className="w-[min(620px,92vw)] overflow-hidden p-0"
      >
        <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
          <Search className="size-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
          <input
            role="combobox"
            aria-expanded={rows.length > 0}
            aria-controls={LIST_ID}
            aria-autocomplete="list"
            aria-activedescendant={rows.length > 0 && rows[activeIndex] ? rowDomId(activeIndex) : undefined}
            aria-label={copy.search.placeholder}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={copy.search.placeholder}
            className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
          />
          <Kbd>{modifierLabel()}K</Kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-1">{body}</div>

        <div className="flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] px-3 py-1.5">
          <span className="min-w-0 truncate text-[10px] text-[var(--text-tertiary)]">
            {response?.degraded ? copy.search.degraded : copy.search.hint}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            {response && !loading ? (
              <span className="mono text-[10px] text-[var(--text-tertiary)]">
                {fill(copy.search.tookMs, { ms: response.tookMs })}
              </span>
            ) : null}
            <Badge tone={response?.degraded ? 'warn' : 'neutral'}>{rows.length} 项</Badge>
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
