/**
 * UI state only — never business data (§10.2). Layout, selection, theme and the
 * source drawer live here because they are view concerns; server data belongs to
 * TanStack Query so there is exactly one cache.
 */

'use client';

import { create } from 'zustand';

import {
  DEFAULT_LAYOUT,
  type LayoutState,
  loadLayout,
  saveLayout,
  type LayoutPreset,
} from './layout';
import { applyTheme, readTheme, writeTheme } from './theme';

export type ThemeMode = 'dark' | 'light' | 'system';

interface SourceDrawerState {
  open: boolean;
  activeSourceId: string | null;
}

interface UiState {
  selectedInstrumentId: string | null;
  activeReportId: string | null;
  layout: LayoutState;
  /** User pressed "collapse rail" — independent of the responsive breakpoint. */
  railCollapsedByUser: boolean;
  theme: ThemeMode;
  resolvedTheme: 'dark' | 'light';
  sourceDrawer: SourceDrawerState;
  commandPaletteOpen: boolean;
  klineDrawerOpen: boolean;
  /** The generation card is minimised to its header row. */
  overlayCollapsed: boolean;
  chartFocus: { from: string; to: string } | null;
  setSelectedInstrument: (id: string | null) => void;
  setActiveReport: (id: string | null) => void;
  setLayout: (layout: LayoutState) => void;
  patchLayout: (patch: Partial<LayoutState>) => void;
  setPreset: (preset: LayoutPreset) => void;
  resetLayout: () => void;
  hydrateLayout: () => void;
  toggleRailCollapsed: (collapsed?: boolean) => void;
  setTheme: (theme: ThemeMode) => void;
  hydrateTheme: () => void;
  setResolvedTheme: (theme: 'dark' | 'light') => void;
  openSourceDrawer: (sourceId?: string | null) => void;
  closeSourceDrawer: () => void;
  toggleCommandPalette: (open?: boolean) => void;
  setOverlayCollapsed: (collapsed: boolean) => void;
  setKlineDrawer: (open: boolean) => void;
  focusChartRange: (from: string, to: string) => void;
  clearChartFocus: () => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  selectedInstrumentId: null,
  activeReportId: null,
  layout: DEFAULT_LAYOUT,
  railCollapsedByUser: false,
  theme: 'dark',
  resolvedTheme: 'dark',
  sourceDrawer: { open: false, activeSourceId: null },
  commandPaletteOpen: false,
  klineDrawerOpen: false,
  overlayCollapsed: false,
  chartFocus: null,

  setSelectedInstrument: (id) => set({ selectedInstrumentId: id }),
  setActiveReport: (id) => set({ activeReportId: id }),

  setLayout: (layout) => {
    set({ layout });
    saveLayout(layout);
  },
  patchLayout: (patch) => {
    const next = { ...get().layout, ...patch };
    set({ layout: next });
    saveLayout(next);
  },
  setPreset: (preset) => {
    const next = { ...get().layout, preset };
    set({ layout: next });
    saveLayout(next);
  },
  resetLayout: () => {
    set({ layout: DEFAULT_LAYOUT });
    saveLayout(DEFAULT_LAYOUT);
  },
  hydrateLayout: () => set({ layout: loadLayout() }),
  toggleRailCollapsed: (collapsed) =>
    set((state) => ({ railCollapsedByUser: collapsed ?? !state.railCollapsedByUser })),

  // Theme has exactly one writer: this action persists *and* applies, so the
  // bootstrap script, the palette and the hotkey can never disagree (§6.3).
  setTheme: (theme) => set({ theme, resolvedTheme: writeTheme(theme) }),
  hydrateTheme: () => {
    const theme = readTheme();
    set({ theme, resolvedTheme: applyTheme(theme) });
  },
  setResolvedTheme: (resolvedTheme) => set({ resolvedTheme }),

  openSourceDrawer: (sourceId = null) =>
    set({ sourceDrawer: { open: true, activeSourceId: sourceId } }),
  closeSourceDrawer: () => set({ sourceDrawer: { open: false, activeSourceId: null } }),

  toggleCommandPalette: (open) =>
    set((state) => ({ commandPaletteOpen: open ?? !state.commandPaletteOpen })),
  setOverlayCollapsed: (overlayCollapsed) => set({ overlayCollapsed }),
  setKlineDrawer: (klineDrawerOpen) => set({ klineDrawerOpen }),

  focusChartRange: (from, to) => set({ chartFocus: { from, to } }),
  clearChartFocus: () => set({ chartFocus: null }),
}));

/** SSR-safe selector for the widths a component needs. */
export const selectLayout = (state: UiState): LayoutState => state.layout;
