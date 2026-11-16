import { describe, expect, it } from 'vitest';

import {
  DEFAULT_LAYOUT,
  RAIL_AUTO,
  computeWidths,
  dragKline,
  dragRail,
  isKlineCollapsed,
  isRailCollapsed,
  toCssVariables,
  widthsToPercentages,
} from '@/lib/layout';

/**
 * AC-3: at 1440×900 the three columns must be 15 / 34 / 51 (spec §10.1).
 * Everything else here guards the collapse rules and the clamps that keep a
 * dragged layout usable.
 */
describe('layout proportions', () => {
  it('gives 15 / 34 / 51 of the viewport at 1440px', () => {
    const widths = computeWidths(1440, { ...DEFAULT_LAYOUT, railPx: 216 });
    const percentages = widthsToPercentages(widths);
    expect(percentages.rail).toBeCloseTo(15, 0);
    expect(percentages.kline).toBeCloseTo(34, 0);
    expect(percentages.report).toBeCloseTo(51, 0);
    // The three columns must consume exactly the viewport.
    expect(widths.rail + widths.kline + widths.report).toBeCloseTo(1440, 5);
  });

  it('gives 15 / 34 / 51 on a fresh profile with no drag history (AC-3)', () => {
    // The bug this pins: the default rail used to be a hard 240px, which is
    // 16.7% at 1440px — outside AC-3's ±1 point — because `defaultRailPx` was
    // never called. A never-dragged layout must follow 15vw.
    expect(DEFAULT_LAYOUT.railPx).toBe(RAIL_AUTO);
    const percentages = widthsToPercentages(computeWidths(1440));
    expect(percentages.rail).toBeCloseTo(15, 0);
    expect(percentages.kline).toBeCloseTo(34, 0);
    expect(percentages.report).toBeCloseTo(51, 0);
  });

  it('keeps the rail at 15vw within its clamp', () => {
    const wide = computeWidths(2560, { ...DEFAULT_LAYOUT, railPx: 320 });
    expect(wide.rail).toBe(320);
    const narrow = computeWidths(1024, { ...DEFAULT_LAYOUT, railPx: 200 });
    expect(narrow.rail).toBe(200);
  });

  it('splits the remaining width 40:60 rather than the viewport', () => {
    const widths = computeWidths(1440, { ...DEFAULT_LAYOUT, railPx: 216 });
    const remaining = 1440 - 216;
    expect(widths.kline).toBeCloseTo(remaining * 0.4, 5);
    expect(widths.report).toBeCloseTo(remaining * 0.6, 5);
  });

  it('supports the literal 15/40/60 preset normalised to 13.04/34.78/52.17', () => {
    const widths = computeWidths(1440, { ...DEFAULT_LAYOUT, preset: 'literal' });
    const percentages = widthsToPercentages(widths);
    expect(percentages.rail).toBeCloseTo(13.04, 1);
    expect(percentages.kline).toBeCloseTo(34.78, 1);
    expect(percentages.report).toBeCloseTo(52.17, 1);
  });

  it('collapses the chart below 1280px and the rail below 960px', () => {
    expect(isKlineCollapsed(1279)).toBe(true);
    expect(isKlineCollapsed(1280)).toBe(false);
    expect(isRailCollapsed(959)).toBe(true);
    expect(isRailCollapsed(960)).toBe(false);

    const collapsedChart = computeWidths(1200, DEFAULT_LAYOUT);
    expect(collapsedChart.kline).toBe(0);
    expect(collapsedChart.report).toBe(1200 - collapsedChart.rail);

    const collapsedRail = computeWidths(800, DEFAULT_LAYOUT);
    expect(collapsedRail.rail).toBe(56);
    expect(collapsedRail.report).toBe(744);
  });

  it('respects the minimum widths before handing space back', () => {
    // A fraction that would squeeze the report below 430px must be corrected.
    const widths = computeWidths(1000, { ...DEFAULT_LAYOUT, railPx: 200, klineFraction: 0.65 });
    expect(widths.report).toBeGreaterThanOrEqual(430);
  });

  it('emits the CSS custom properties the grid consumes', () => {
    const widths = computeWidths(1440, { ...DEFAULT_LAYOUT, railPx: 216 });
    const css = toCssVariables(widths);
    expect(css['--rail-w']).toBe('216px');
    expect(css['--kline-w']).toBe(`${Math.round((1440 - 216) * 0.4)}px`);
    expect(css['--report-w']).toBe(`${Math.round((1440 - 216) * 0.6)}px`);
  });
});

describe('splitter drag arithmetic', () => {
  it('starts the first drag from the 15vw default', () => {
    expect(dragRail(DEFAULT_LAYOUT, 16, 1440).railPx).toBe(232);
    expect(dragRail(DEFAULT_LAYOUT, -16, 1440).railPx).toBe(200);
  });

  it('clamps the rail between 200 and 400 px', () => {
    expect(dragRail(DEFAULT_LAYOUT, -1000).railPx).toBe(200);
    expect(dragRail(DEFAULT_LAYOUT, 1000).railPx).toBe(400);
    expect(dragRail({ ...DEFAULT_LAYOUT, railPx: 240 }, 16).railPx).toBe(256);
  });

  it('clamps the chart fraction between 0.25 and 0.65', () => {
    const viewport = 1440;
    expect(dragKline(DEFAULT_LAYOUT, -1000, viewport).klineFraction).toBe(0.25);
    expect(dragKline(DEFAULT_LAYOUT, 1000, viewport).klineFraction).toBe(0.65);
  });

  it('persisted state round-trips through the clamp', () => {
    const state = { railPx: 310, klineFraction: 0.5, preset: 'nested' as const };
    const widths = computeWidths(1600, state);
    expect(widths.rail).toBe(310);
  });
});
