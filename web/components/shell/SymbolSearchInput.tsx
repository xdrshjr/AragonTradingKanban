'use client';

import { Search } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { marketLabel } from '@/lib/format';
import type { Instrument, SymbolSearchHit } from '@/lib/types';

import { Input } from '../ui/Input';

const DEBOUNCE_MS = 300;

export interface SymbolSearchInputProps {
  onResolved: (instrument: Instrument) => void;
  placeholder?: string;
  autoFocus?: boolean;
  size?: 'sm' | 'md';
  initialValue?: string;
}

/**
 * Typeahead + resolve (spec §3.1 / US-1).
 *
 * `aria-activedescendant` keeps focus in the input while the arrow keys move a
 * virtual selection, which is the pattern screen readers expect for a combobox.
 */
export function SymbolSearchInput({
  onResolved,
  placeholder = copy.landing.placeholder,
  autoFocus = false,
  size = 'sm',
  initialValue = '',
}: SymbolSearchInputProps) {
  const [value, setValue] = useState(initialValue);
  const [hits, setHits] = useState<SymbolSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listId = 'symbol-search-listbox';

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, []);

  const search = useCallback((query: string) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (query.trim().length < 1) {
      setHits([]);
      setOpen(false);
      return;
    }
    timerRef.current = setTimeout(async () => {
      try {
        const response = await api.searchSymbols(query, 8);
        setHits(response.hits);
        setOpen(response.hits.length > 0);
        setActiveIndex(response.hits.length > 0 ? 0 : -1);
      } catch {
        setHits([]);
      }
    }, DEBOUNCE_MS);
  }, []);

  const resolve = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed) return;
      setBusy(true);
      setError(null);
      try {
        const resolved = await api.resolveSymbol(trimmed);
        onResolved(resolved.instrument);
        setOpen(false);
        setValue('');
        setHits([]);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '无法解析该标的');
        setOpen(false);
      } finally {
        setBusy(false);
      }
    },
    [onResolved],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!open && hits.length > 0) {
        setOpen(true);
        return;
      }
      setActiveIndex((index) => Math.min(index + 1, hits.length - 1));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const hit = hits[activeIndex];
      if (open && hit) {
        void resolve(hit.symbol);
      } else {
        void resolve(value);
      }
      return;
    }
    if (event.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <Input
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeIndex >= 0 && open ? `${listId}-${activeIndex}` : undefined}
        aria-label={copy.rail.search}
        value={value}
        autoFocus={autoFocus}
        inputSize={size}
        invalid={Boolean(error)}
        placeholder={placeholder}
        leadingIcon={busy ? <Spinner /> : <Search className="size-3" />}
        onClear={() => {
          setValue('');
          setHits([]);
          setOpen(false);
          setError(null);
        }}
        onChange={(event) => {
          const next = event.target.value;
          setValue(next);
          setError(null);
          search(next);
        }}
        onKeyDown={onKeyDown}
        onFocus={() => {
          if (hits.length > 0) setOpen(true);
        }}
      />

      {open && hits.length > 0 ? (
        <ul
          id={listId}
          role="listbox"
          aria-label={copy.rail.search}
          className="absolute left-0 right-0 top-[calc(100%+4px)] z-40 max-h-[280px] overflow-y-auto rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]"
        >
          {hits.map((hit, index) => (
            <li
              key={hit.symbol}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                void resolve(hit.symbol);
              }}
              className={cn(
                'flex cursor-pointer items-center justify-between gap-2 rounded-[var(--radius-sm)] px-2 py-1.5',
                index === activeIndex ? 'bg-[var(--bg-hover)]' : '',
              )}
            >
              <span className="min-w-0">
                <span className="block truncate text-[12px] text-[var(--text-primary)]">{hit.name}</span>
                <span className="mono block text-[10px] text-[var(--text-tertiary)]">{hit.symbol}</span>
              </span>
              <span className="shrink-0 text-[10px] text-[var(--text-tertiary)]">
                {marketLabel(hit.market)}
                {hit.existing ? ' · 已有' : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {error ? <p className="mt-1 px-0.5 text-[10.5px] text-[var(--danger)]">{error}</p> : null}
    </div>
  );
}

function Spinner() {
  return <span className="inline-block size-3 animate-spin rounded-full border border-current border-t-transparent" />;
}
