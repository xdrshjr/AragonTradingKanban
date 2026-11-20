import type { ChartDetails, ChartTimeframe } from './types';

/** Highest precision needed by a group of prices, including negative prices. */
export function pricePrecision(
  values: readonly (number | null | undefined)[],
): number {
  let precision = 2;
  for (const value of values) {
    if (value == null || value === 0) continue;
    const absolute = Math.abs(value);
    if (absolute < 0.01) return 6;
    if (absolute < 1) precision = 4;
  }
  return precision;
}

/** Format only for display; calculation and exports keep float precision. */
export function formatPrice(
  value: number | null | undefined,
  precision?: number,
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: precision ?? pricePrecision([value]),
    maximumFractionDigits: precision ?? pricePrecision([value]),
  });
}

/** Preserve an explicit sign for change values without marking zero as up. */
export function formatSigned(
  value: number | null | undefined,
  precision = 2,
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value > 0 ? '+' : ''}${formatPrice(value, precision)}`;
}

/** Percent values already use percentage points: 5 means five percent. */
export function formatPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value)
    ? '—' : `${formatSigned(value)}%`;
}

/** Return the provider's actual volume unit without converting raw values. */
export function volumeUnitLabel(unit: ChartDetails['volumeUnit']): string {
  return { share: '股', lot: '手', unit: '单位', unknown: '源单位' }[unit];
}

function compactNumber(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1e8) return `${formatPrice(value / 1e8, 2)}亿`;
  if (absolute >= 1e4) return `${formatPrice(value / 1e4, 2)}万`;
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/** Abbreviated volume with explicit source units; zero remains visible. */
export function formatVolume(
  value: number | null | undefined,
  unit: ChartDetails['volumeUnit'] | ChartDetails = 'unknown',
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const name = volumeUnitLabel(
    typeof unit === 'string' ? unit : unit.volumeUnit,
  );
  return `${compactNumber(value)}${name}`;
}

/** Missing amount is never reconstructed from close times volume. */
export function formatAmount(
  value: number | null | undefined,
  currency = '',
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${compactNumber(value)}${currency ? ` ${currency}` : ''}`;
}

/** Distinguish daily, weekly and monthly return labels. */
export function timeframeLabel(timeframe: ChartTimeframe): string {
  return { '1d': '日', '1w': '周', '1m': '月' }[timeframe];
}
