'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect } from 'react';

import { modifierLabel, useHotkeys } from '@/lib/keyboard';
import { useUiStore } from '@/lib/store';
import type { ReportSummary } from '@/lib/types';

import { Kbd } from '../ui/Tooltip';
import { CommandPalette } from './CommandPalette';
import { SymbolSearchInput } from './SymbolSearchInput';

/** Landing page search: resolve, then push straight into the workspace (§3.1). */
export function LandingSearch() {
  const router = useRouter();
  const toggleCommandPalette = useUiStore((state) => state.toggleCommandPalette);

  const handleResolved = useCallback(
    (instrument: { id: string }) => {
      router.push(`/${encodeURIComponent(instrument.id)}`);
    },
    [router],
  );

  useHotkeys({
    onCommandPalette: () => toggleCommandPalette(true),
  });

  useEffect(() => {
    // Nothing to restore: the input is intentionally empty on every visit.
  }, []);

  return (
    <>
      <div className="rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--bg-surface-1)] p-2 shadow-[var(--shadow-card)]">
        <SymbolSearchInput onResolved={handleResolved} autoFocus size="md" />
        <div className="mt-1.5 flex items-center justify-center gap-2 text-[10.5px] text-[var(--text-tertiary)]">
          <span className="flex items-center gap-1">
            <Kbd>{modifierLabel()}K</Kbd> 命令面板
          </span>
          <span aria-hidden="true">·</span>
          <span className="flex items-center gap-1">
            <Kbd>↵</Kbd> 进入工作台
          </span>
        </div>
      </div>

      {/*
        The landing page advertises ⌘K, so the palette has to exist here too —
        it used to be mounted only inside the workspace, where the hint is absent.
      */}
      <CommandPalette
        instruments={[]}
        reports={EMPTY_REPORTS}
        onSelectInstrument={(instrumentId) => router.push(`/${encodeURIComponent(instrumentId)}`)}
        onSelectVersion={() => undefined}
        onGenerate={() => undefined}
        onExport={() => undefined}
        onToggleChart={() => undefined}
        // ★v14（U1 / 评审 R-2）：首页广告了 ⌘K，所以这里的「报告库」入口也要真的能打开它
        // （入口有、点了没反应，比没有入口更糟）。最近 5 份报告不在这里取：首页已经用
        // `RecentReports` 展示过一次，面板再发一次请求是纯浪费。
        onOpenLibrary={() => router.push('/reports')}
      />
    </>
  );
}

const EMPTY_REPORTS: ReportSummary[] = [];
