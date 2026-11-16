/**
 * Three-column geometry (spec §5.5 / §10.1 / AC-3).
 *
 * The brief's 15% + 40% + 60% sums to 115%, so the spec resolves it as "the rail
 * takes 15% of the viewport, then the remaining 85% splits 40:60" → the visible
 * result is 15 / 34 / 51. `LAYOUT_PRESET` keeps the literal reading available.
 *
 * This module is the ONLY place that computes proportions; it writes back
 * `--rail-w`, `--kline-w` and `--report-w`, and the CSS grid derives from those
 * tokens (★P2-06 — one source of truth, not two).
 */

export type LayoutPreset = 'nested' | 'literal';

export const LAYOUT_PRESET: LayoutPreset = 'nested';
export const STORAGE_KEY = 'aragon.layout.v1';

export const RAIL_MIN = 200;
export const RAIL_MAX = 400;
export const KLINE_MIN = 320;
export const KLINE_MIN_PX = 380;
/** §10.1: the report column never drops below 460px. */
export const REPORT_MIN_PX = 460;

/**
 * Sentinel for "no explicit rail width yet": the rail follows 15vw (clamped)
 * until the user drags it. Without this, a fresh profile would sit at the old
 * 240px default — 16.7% at 1440px, which fails AC-3 outright.
 */
export const RAIL_AUTO = 0;

/** Breakpoints from §10.1. */
export const BP_KLINE_COLLAPSE = 1280;
export const BP_RAIL_COLLAPSE = 960;

export interface LayoutWidths {
  rail: number;
  kline: number;
  report: number;
  total: number;
}

export interface LayoutState {
  /** Rail width in px, or `RAIL_AUTO` to follow 15vw for the current viewport. */
  railPx: number;
  /** Fraction of the remaining width given to the K-line panel (0.25–0.65). */
  klineFraction: number;
  preset: LayoutPreset;
}

export const DEFAULT_LAYOUT: LayoutState = {
  railPx: RAIL_AUTO,
  klineFraction: 0.4,
  preset: LAYOUT_PRESET,
};

export const KLINE_FRACTION_MIN = 0.25;
export const KLINE_FRACTION_MAX = 0.65;

export function clamp(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  return Math.min(Math.max(value, min), max);
}

/** 15% of the viewport, clamped to the rail's usable range (§10.1: 220–320px). */
export function defaultRailPx(viewportWidth: number): number {
  const fifteenPercent = viewportWidth * 0.15;
  return Math.round(clamp(fifteenPercent, RAIL_MIN, RAIL_MAX));
}

/** Resolve `RAIL_AUTO` against a viewport; an explicit width is used as-is. */
export function resolveRailPx(state: LayoutState, viewportWidth: number): number {
  if (state.railPx > RAIL_AUTO) return clamp(state.railPx, RAIL_MIN, RAIL_MAX);
  return defaultRailPx(viewportWidth);
}

/**
 * Compute the three column widths in px for a viewport.
 *
 * `nested`  → rail 15%, then the rest at 40/60 (15 / 34 / 51 of the viewport).
 * `literal` → 15 / 40 / 60 normalised by their sum (13.04 / 34.78 / 52.17).
 *
 * Narrow viewports collapse columns rather than squeezing them (§10.1):
 * `<960` drops the rail to an icon strip, `<1280` moves the chart to a drawer.
 */
export function computeWidths(viewportWidth: number, state: LayoutState = DEFAULT_LAYOUT): LayoutWidths {
  const width = Math.max(0, viewportWidth);

  if (width < BP_RAIL_COLLAPSE) {
    const rail = 56;
    return { rail, kline: 0, report: Math.max(0, width - rail), total: width };
  }

  const rail = state.preset === 'literal' ? (width * 15) / 115 : resolveRailPx(state, width);
  const remaining = Math.max(0, width - rail);

  if (width < BP_KLINE_COLLAPSE) {
    // Chart lives in a drawer; the report owns the whole right side.
    return { rail, kline: 0, report: remaining, total: width };
  }

  if (state.preset === 'literal') {
    return {
      rail: (width * 15) / 115,
      kline: (width * 40) / 115,
      report: (width * 60) / 115,
      total: width,
    };
  }

  const klineRaw = remaining * state.klineFraction;
  const reportRaw = remaining - klineRaw;
  // Respect the minimums by borrowing from the larger neighbour, never from rail.
  let kline = klineRaw;
  let report = reportRaw;
  if (kline < KLINE_MIN_PX) {
    kline = Math.min(KLINE_MIN_PX, remaining);
    report = remaining - kline;
  }
  if (report < REPORT_MIN_PX && remaining > REPORT_MIN_PX) {
    report = REPORT_MIN_PX;
    kline = remaining - report;
  }
  return { rail, kline, report, total: width };
}

/** Percentages rounded to 2 decimals — what the splitter announces via aria-valuenow. */
export function widthsToPercentages(widths: LayoutWidths): LayoutWidths {
  const total = widths.total || 1;
  return {
    rail: Number(((widths.rail / total) * 100).toFixed(2)),
    kline: Number(((widths.kline / total) * 100).toFixed(2)),
    report: Number(((widths.report / total) * 100).toFixed(2)),
    total: 100,
  };
}

/** CSS custom properties for the grid. Written to `documentElement`. */
export function toCssVariables(widths: LayoutWidths): Record<string, string> {
  return {
    '--rail-w': `${Math.round(widths.rail)}px`,
    '--kline-w': `${Math.round(widths.kline)}px`,
    '--report-w': `${Math.round(widths.report)}px`,
  };
}

export function isKlineCollapsed(viewportWidth: number): boolean {
  return viewportWidth < BP_KLINE_COLLAPSE;
}

export function isRailCollapsed(viewportWidth: number): boolean {
  return viewportWidth < BP_RAIL_COLLAPSE;
}

/* ── persistence ─────────────────────────────────────────────────────────── */

export function loadLayout(): LayoutState {
  if (typeof window === 'undefined') return DEFAULT_LAYOUT;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_LAYOUT;
    const parsed = JSON.parse(raw) as Partial<LayoutState>;
    const storedRail = Number(parsed.railPx ?? RAIL_AUTO);
    return {
      // 0 / NaN / anything below RAIL_MIN means "never dragged" → stay on 15vw.
      railPx: Number.isFinite(storedRail) && storedRail >= RAIL_MIN ? clamp(storedRail, RAIL_MIN, RAIL_MAX) : RAIL_AUTO,
      klineFraction: clamp(
        Number(parsed.klineFraction ?? DEFAULT_LAYOUT.klineFraction),
        KLINE_FRACTION_MIN,
        KLINE_FRACTION_MAX,
      ),
      preset: parsed.preset === 'literal' ? 'literal' : 'nested',
    };
  } catch {
    return DEFAULT_LAYOUT;
  }
}

export function saveLayout(state: LayoutState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* private mode: persistence is a convenience, not a requirement */
  }
}

/**
 * One drag step: move the rail edge by `deltaX` px, clamped.
 *
 * A layout that has never been dragged (`RAIL_AUTO`) starts from the 15vw
 * default for the current viewport, so the first drag does not jump.
 */
export function dragRail(state: LayoutState, deltaX: number, viewportWidth = 1440): LayoutState {
  const base = resolveRailPx(state, viewportWidth);
  return { ...state, railPx: clamp(base + deltaX, RAIL_MIN, RAIL_MAX) };
}

/** One drag step on the chart/report divider: adjust the fraction, clamped. */
export function dragKline(state: LayoutState, deltaPx: number, viewportWidth: number): LayoutState {
  const remaining = Math.max(1, viewportWidth - resolveRailPx(state, viewportWidth));
  const next = clamp(state.klineFraction + deltaPx / remaining, KLINE_FRACTION_MIN, KLINE_FRACTION_MAX);
  return { ...state, klineFraction: Number(next.toFixed(4)) };
}
