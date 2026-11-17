/**
 * Formatting helpers. Chinese financial UI conventions: 亿/万 units, ▲▼ instead of
 * colour alone (WCAG 1.4.1), and `Intl` for anything locale-dependent.
 */

import { costSourceLabel } from './copy';

const CN = 'zh-CN';

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toLocaleString(CN, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function price(value: number | null | undefined, digits = 2): string {
  return num(value, digits);
}

export function pct(value: number | null | undefined, digits = 1, withSign = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = withSign && value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** `1_234_567_890` → `12.35 亿`. Keeps the magnitude readable at a glance. */
export function compact(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const absolute = Math.abs(value);
  if (absolute >= 1e12) return `${(value / 1e12).toFixed(digits)} 万亿`;
  if (absolute >= 1e8) return `${(value / 1e8).toFixed(digits)} 亿`;
  if (absolute >= 1e4) return `${(value / 1e4).toFixed(digits)} 万`;
  return value.toLocaleString(CN);
}

const moneySymbols: Record<string, string> = { CNY: '¥', HKD: 'HK$', USD: '$' };

export function money(value: number | null | undefined, currency = 'CNY', digits = 2): string {
  if (value === null || value === undefined) return '—';
  return `${moneySymbols[currency] ?? ''}${num(value, digits)}`;
}

/**
 * Cost line for run badges: unknown cost must read as unknown, never `$0.00`.
 *
 * ★v8 评审（H-6 的落地点）：报告的版本行也走这三态。`quality.cost` 缺席是**早期报告**
 * 的合法状态（§19.6.3 不回填），那时退回 `costUsd/costKnown` 两态；一旦有
 * `quality.cost`，就以它为准——否则一份 `estimated` 的报告会在界面上被读成回执。
 */
export function costLabel(
  costUsd: number | null | undefined,
  costKnown: boolean,
  cost?: { source?: string | null; reportedUsd?: number | null; estimatedUsd?: number | null } | null,
): string {
  if (cost?.source === 'reported' && cost.reportedUsd !== null && cost.reportedUsd !== undefined) {
    return `${costSourceLabel('reported')} $${cost.reportedUsd.toFixed(2)}`;
  }
  if (cost?.source === 'estimated' && cost.estimatedUsd !== null && cost.estimatedUsd !== undefined) {
    return `${costSourceLabel('estimated')} $${cost.estimatedUsd.toFixed(2)}`;
  }
  if (cost?.source === 'unknown') return costSourceLabel('unknown');
  if (!costKnown || costUsd === null || costUsd === undefined) return costSourceLabel('unknown');
  return `$${costUsd.toFixed(2)}`;
}

export function tokensLabel(input?: number | null, output?: number | null): string {
  const total = (input ?? 0) + (output ?? 0);
  if (total === 0) return '—';
  return `${total.toLocaleString(CN)} tok`;
}

export function durationLabel(ms: number | null | undefined): string {
  if (!ms || ms < 0) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const diff = Date.now() - then;
  const minute = 60_000;
  const hour = 3_600_000;
  const day = 86_400_000;
  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`;
  return new Date(then).toLocaleDateString(CN, { year: 'numeric', month: '2-digit', day: '2-digit' });
}

export function dateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return '—';
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return '—';
  return value.toLocaleString(CN, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...(timeZone ? { timeZone } : {}),
  });
}

export function dateOnly(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return '—';
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return iso;
  return value.toLocaleDateString(CN, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(timeZone ? { timeZone } : {}),
  });
}

/** Epoch seconds → `2026-02-10`, in the exchange's timezone. */
export function epochToDate(seconds: number, timeZone = 'Asia/Shanghai'): string {
  return new Date(seconds * 1000).toLocaleDateString('sv-SE', { timeZone }).slice(0, 10);
}

/** `▲ 2.4%` / `▼ 1.1%` — direction is never conveyed by colour alone. */
export function deltaArrow(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const arrow = value > 0 ? '▲' : value < 0 ? '▼' : '■';
  return `${arrow} ${Math.abs(value).toFixed(digits)}%`;
}

export function toneFor(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined || value === 0) return 'flat';
  return value > 0 ? 'up' : 'down';
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1))}…`;
}

export function versionLabel(version: number, provisional: boolean): string {
  return provisional ? `v${version} · 临时版` : `v${version}`;
}

/** `600519.SH` → `600519` for the tight rail layout. */
export function shortSymbol(symbol: string): string {
  return symbol.split('.')[0] ?? symbol;
}

export function marketLabel(market: string): string {
  return market === 'CN' ? 'A 股' : market === 'HK' ? '港股' : market === 'US' ? '美股' : market;
}

export function currencySymbol(currency: string | null | undefined): string {
  return moneySymbols[currency ?? 'CNY'] ?? '';
}
