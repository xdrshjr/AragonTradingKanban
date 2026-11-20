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
  /** ★v6 G3: opens the cost panel. Omitted when `features.costLedger` is false. */
  onOpenCost?: () => void;
  /**
   * ★v14（U1）：打开报告库。`features.reportLibrary === false` 时**不传** ⇒
   * 这一项不出现（无悬挂分支，与 `onOpenCost` 完全同形）。
   */
  onOpenLibrary?: () => void;
  /**
   * ★v14 评审（R-2）：打开**某一版**报告（跨标的）。`recentReports` 的每一行都靠它。
   *
   * 不复用 `onSelectInstrument`：那一个只换标的、打开的是**最新版**，而"最近生成"里 v12 与
   * v13 并列时，点 v12 却看到 v13 正是 U-0b 的原话。也不复用 `onSelectVersion`：那个只在本
   * 标的内部切版本（它写不进路径里的标的）。所以这里给的是 `(symbol, reportId)`。
   */
  onOpenReport?: (symbol: string, reportId: string) => void;
  /**
   * ★X6（§24.3.15e）：账号类命令。与 `onOpenLibrary` 同一个“不传即不出现”的模式——
   * 游客只会看到「登录或注册」，普通用户看到「账号与额度 / 退出登录」，管理员另有「管理后台」。
   */
  onSignIn?: () => void;
  onOpenAccount?: () => void;
  onOpenAdmin?: () => void;
  onSignOut?: () => void;
  /** ★v14：最近 5 份报告（来自 `reportLibrary({limit:5})`），命令面板里的动态项。 */
  recentReports?: { id: string; symbol: string; name: string; headline: string; version: number }[];
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
  onOpenCost,
  onOpenLibrary,
  onOpenReport,
  recentReports = [],
  onSignIn,
  onOpenAccount,
  onOpenAdmin,
  onSignOut,
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

    // ★v14（U1）：报告库入口 + 最近 5 份报告。报告库是"我做过什么"的唯一入口，
    // 所以它属于命令面板；`recentReports` 让"刚才那份"两次按键就能回到。
    const libraryItems: CommandItem[] = onOpenLibrary
      ? [
          {
            id: 'open-library',
            label: copy.library.open,
            hint: copy.library.subtitle,
            group: copy.command.groups.actions,
            run: () => {
              toggle(false);
              onOpenLibrary();
            },
          },
        ]
      : [];

    const recentItems: CommandItem[] = onOpenReport
      ? recentReports.slice(0, 5).map((report) => ({
          id: `recent-${report.id}`,
          label: `${report.name || report.symbol} · v${report.version}`,
          hint: report.headline.slice(0, 24),
          group: copy.command.groups.versions,
          run: () => {
            // ★v14 评审（R-2）：打开的是**那一版**，不是"这个标的最新版"（U-0b）。
            onOpenReport(report.symbol, report.id);
            toggle(false);
          },
        }))
      : [];

    const accountItems: CommandItem[] = [
      ...(onSignIn
        ? [{ id: 'sign-in', label: copy.auth.command.signIn, group: copy.command.groups.actions, run: () => { toggle(false); onSignIn(); } }]
        : []),
      ...(onOpenAccount
        ? [{ id: 'account', label: copy.auth.command.account, group: copy.command.groups.actions, run: () => { toggle(false); onOpenAccount(); } }]
        : []),
      ...(onOpenAdmin
        ? [{ id: 'admin', label: copy.auth.command.admin, group: copy.command.groups.actions, run: () => { toggle(false); onOpenAdmin(); } }]
        : []),
      ...(onSignOut
        ? [{ id: 'sign-out', label: copy.auth.command.signOut, group: copy.command.groups.actions, run: () => { toggle(false); onSignOut(); } }]
        : []),
    ];

    const actionItems: CommandItem[] = [
      ...searchItem,
      ...libraryItems,
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
      // ★v6 G3：成本是『我这一年花了多少钱』的入口，所以它属于命令面板而不是某个角落。
      // `onOpenCost` 缺席 = `features.costLedger === false`，此时**不出现**该命令（无悬挂分支）。
      ...(onOpenCost
        ? [
            {
              id: 'cost',
              label: copy.cost.open,
              hint: copy.cost.title,
              group: copy.command.groups.actions,
              run: () => {
                toggle(false);
                onOpenCost();
              },
            },
          ]
        : []),
      {
        id: 'toggle-chart',
        label: copy.command.actions.toggleChart,
        group: copy.command.groups.actions,
        run: () => {
          onToggleChart();
          toggle(false);
        },
      },
      ...accountItems,
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

    return [...actionItems, ...recentItems, ...versionItems, ...symbolItems];
  }, [
    instruments,
    onExport,
    onGenerate,
    onOpenAccount,
    onOpenAdmin,
    onOpenCost,
    onOpenLibrary,
    onOpenReport,
    onOpenSearch,
    onSignIn,
    onSignOut,
    onSelectInstrument,
    onSelectVersion,
    onToggleChart,
    recentReports,
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
