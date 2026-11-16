'use client';

import { Command } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import { modifierLabel } from '@/lib/keyboard';
import { useUiStore } from '@/lib/store';
import type { ReportSummary } from '@/lib/types';

import { Badge } from '../ui/Badge';
import { Kbd } from '../ui/Tooltip';

export interface CommandPaletteProps {
  instruments: { instrument: { id: string; name: string; symbol: string } }[];
  reports: ReportSummary[];
  onSelectInstrument: (id: string) => void;
  onSelectVersion: (reportId: string) => void;
  onGenerate: () => void;
  onExport: (format: 'html' | 'md', reportId?: string) => void;
  onToggleChart: () => void;
  /** A2: opens the unified search dialog. Omitted when full-text search is off. */
  onOpenSearch?: () => void;
}

interface CommandItem {
  id: string;
  label: string;
  hint?: string;
  group: string;
  run: () => void;
}

/** ⌘K palette (spec §6.3): switch symbol, switch version, generate, export, theme. */
export function CommandPalette({
  instruments,
  reports,
  onSelectInstrument,
  onSelectVersion,
  onGenerate,
  onExport,
  onToggleChart,
  onOpenSearch,
}: CommandPaletteProps) {
  const open = useUiStore((state) => state.commandPaletteOpen);
  const toggle = useUiStore((state) => state.toggleCommandPalette);
  const setTheme = useUiStore((state) => state.setTheme);
  const theme = useUiStore((state) => state.theme);
  const resetLayout = useUiStore((state) => state.resetLayout);
  const [query, setQuery] = useState('');
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const items = useMemo<CommandItem[]>(() => {
    const symbolItems: CommandItem[] = instruments.map(({ instrument }) => ({
      id: `symbol-${instrument.id}`,
      label: instrument.name,
      hint: instrument.symbol,
      group: copy.command.groups.symbols,
      run: () => {
        onSelectInstrument(instrument.id);
        toggle(false);
      },
    }));

    const versionItems: CommandItem[] = reports.slice(0, 8).map((report) => ({
      id: `version-${report.id}`,
      label: `v${report.version}${report.provisional ? ' · 临时版' : ''}`,
      hint: report.headline.slice(0, 24),
      group: copy.command.groups.versions,
      run: () => {
        onSelectVersion(report.id);
        toggle(false);
      },
    }));

    const searchItem: CommandItem[] = onOpenSearch
      ? [
          {
            id: 'search',
            label: copy.command.actions.search,
            hint: copy.search.placeholder,
            group: copy.command.groups.actions,
            run: () => {
              toggle(false);
              onOpenSearch();
            },
          },
        ]
      : [];

    const actionItems: CommandItem[] = [
      ...searchItem,
      {
        id: 'generate',
        label: copy.command.actions.generate,
        group: copy.command.groups.actions,
        run: () => {
          onGenerate();
          toggle(false);
        },
      },
      {
        id: 'export-html',
        label: copy.command.actions.exportHtml,
        group: copy.command.groups.actions,
        run: () => {
          onExport('html');
          toggle(false);
        },
      },
      {
        id: 'export-md',
        label: copy.command.actions.exportMarkdown,
        group: copy.command.groups.actions,
        run: () => {
          onExport('md');
          toggle(false);
        },
      },
      {
        id: 'toggle-chart',
        label: copy.command.actions.toggleChart,
        group: copy.command.groups.actions,
        run: () => {
          onToggleChart();
          toggle(false);
        },
      },
      {
        id: 'reset-layout',
        label: copy.command.actions.resetLayout,
        group: copy.command.groups.actions,
        run: () => {
          resetLayout();
          toggle(false);
        },
      },
      {
        id: 'theme',
        label: `${copy.command.actions.toggleTheme}（当前 ${theme === 'dark' ? '暗色' : theme === 'light' ? '亮色' : '跟随系统'}）`,
        group: copy.command.groups.theme,
        run: () => {
          setTheme(theme === 'dark' ? 'light' : 'dark');
          toggle(false);
        },
      },
    ];

    return [...actionItems, ...versionItems, ...symbolItems];
  }, [
    instruments,
    onExport,
    onGenerate,
    onOpenSearch,
    onSelectInstrument,
    onSelectVersion,
    onToggleChart,
    reports,
    resetLayout,
    setTheme,
    theme,
    toggle,
  ]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(needle) ||
        (item.hint ?? '').toLowerCase().includes(needle) ||
        item.group.toLowerCase().includes(needle),
    );
  }, [items, query]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  if (!open) return null;

  const grouped = filtered.reduce<Record<string, CommandItem[]>>((accumulator, item) => {
    accumulator[item.group] = [...(accumulator[item.group] ?? []), item];
    return accumulator;
  }, {});

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setIndex((value) => Math.min(value + 1, filtered.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setIndex((value) => Math.max(value - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      filtered[index]?.run();
    } else if (event.key === 'Escape') {
      toggle(false);
    }
  };

  let running = -1;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-[var(--bg-overlay)] pt-[12vh] backdrop-blur-[2px]" data-print="hide">
      <button type="button" aria-label="关闭命令面板" className="absolute inset-0" onClick={() => toggle(false)} />
      <div
        role="dialog"
        aria-label={copy.command.placeholder}
        className="relative w-[min(620px,92vw)] overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] shadow-[var(--shadow-pop)]"
      >
        <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
          <Command className="size-3.5 text-[var(--text-tertiary)]" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder={copy.command.placeholder}
            aria-label={copy.command.placeholder}
            className="flex-1 bg-transparent text-[13px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
          />
          <Kbd>{modifierLabel()}K</Kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="px-3 py-6 text-center text-[11.5px] text-[var(--text-tertiary)]">{copy.command.empty}</p>
          ) : (
            Object.entries(grouped).map(([group, groupItems]) => (
              <div key={group} className="mb-1">
                <p className="px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">{group}</p>
                <ul>
                  {groupItems.map((item) => {
                    running += 1;
                    const active = running === index;
                    return (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={item.run}
                          onMouseEnter={() => setIndex(filtered.indexOf(item))}
                          className={cn(
                            'flex w-full items-center justify-between gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-left',
                            active ? 'bg-[var(--accent-weak)]' : 'hover:bg-[var(--bg-hover)]',
                          )}
                        >
                          <span className="min-w-0 truncate text-[12.5px] text-[var(--text-primary)]">{item.label}</span>
                          {item.hint ? (
                            <span className="mono shrink-0 truncate text-[10.5px] text-[var(--text-tertiary)]">
                              {item.hint}
                            </span>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>

        <div className="flex items-center justify-between border-t border-[var(--border-subtle)] px-3 py-1.5">
          <span className="text-[10px] text-[var(--text-tertiary)]">{copy.command.hint}</span>
          <Badge tone="neutral">{filtered.length} 项</Badge>
        </div>
      </div>
    </div>
  );
}
