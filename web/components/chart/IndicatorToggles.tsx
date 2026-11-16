'use client';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';

export interface IndicatorState {
  ma5: boolean;
  ma10: boolean;
  ma20: boolean;
  ma60: boolean;
  boll: boolean;
  volume: boolean;
}

const OPTIONS: { key: keyof IndicatorState; label: string; color?: string }[] = [
  { key: 'ma5', label: 'MA5', color: 'var(--warn)' },
  { key: 'ma10', label: 'MA10', color: 'var(--info)' },
  { key: 'ma20', label: 'MA20', color: '#a78bfa' },
  { key: 'ma60', label: 'MA60', color: '#22d3ee' },
  { key: 'boll', label: 'BOLL', color: '#f472b6' },
  { key: 'volume', label: copy.chart.volume },
];

/** Toggle set the panel starts from before localStorage is consulted. */
export const DEFAULT_INDICATORS: IndicatorState = {
  ma5: true,
  ma10: false,
  ma20: true,
  ma60: true,
  boll: false,
  volume: true,
};

const STORAGE_KEY = 'aragon.indicators.v1';

/** §6.3 requires the toggles to survive a reload; a corrupt value never throws. */
export function loadIndicators(): IndicatorState {
  if (typeof window === 'undefined') return DEFAULT_INDICATORS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_INDICATORS;
    const parsed = JSON.parse(raw) as Partial<IndicatorState>;
    return {
      ma5: typeof parsed.ma5 === 'boolean' ? parsed.ma5 : DEFAULT_INDICATORS.ma5,
      ma10: typeof parsed.ma10 === 'boolean' ? parsed.ma10 : DEFAULT_INDICATORS.ma10,
      ma20: typeof parsed.ma20 === 'boolean' ? parsed.ma20 : DEFAULT_INDICATORS.ma20,
      ma60: typeof parsed.ma60 === 'boolean' ? parsed.ma60 : DEFAULT_INDICATORS.ma60,
      boll: typeof parsed.boll === 'boolean' ? parsed.boll : DEFAULT_INDICATORS.boll,
      volume: typeof parsed.volume === 'boolean' ? parsed.volume : DEFAULT_INDICATORS.volume,
    };
  } catch {
    return DEFAULT_INDICATORS;
  }
}

export function saveIndicators(state: IndicatorState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* private mode: persistence is a convenience, not a requirement */
  }
}

export function IndicatorToggles({
  state,
  onChange,
}: {
  state: IndicatorState;
  onChange: (state: IndicatorState) => void;
}) {
  return (
    <div role="group" aria-label={copy.chart.indicators} className="flex flex-wrap items-center gap-1">
      {OPTIONS.map((option) => {
        const active = state[option.key];
        return (
          <button
            key={option.key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange({ ...state, [option.key]: !active })}
            className={cn(
              'flex h-6 items-center gap-1 rounded-[var(--radius-sm)] border px-1.5 text-[11px] transition-colors',
              active
                ? 'border-[var(--border-strong)] bg-[var(--bg-surface-3)] text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)]',
            )}
          >
            {option.color ? (
              <span
                aria-hidden="true"
                className="size-1.5 rounded-full"
                style={{ background: active ? option.color : 'var(--border-strong)' }}
              />
            ) : null}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
