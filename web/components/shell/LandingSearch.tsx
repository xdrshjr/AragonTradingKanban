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
      />
    </>
  );
}

const EMPTY_REPORTS: ReportSummary[] = [];
