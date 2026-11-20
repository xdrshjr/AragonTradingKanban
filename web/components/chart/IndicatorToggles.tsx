'use client';

import { cn } from '@/lib/cn';
import { copy } from '@/lib/copy';
import {
  DEFAULT_CHART_PREFERENCES,
  loadChartPreferences,
  saveChartPreferences,
} from '@/lib/chartPreferences';

export interface IndicatorState {
  ma5: boolean;
  ma10: boolean;
  ma20: boolean;
  ma60: boolean;
  boll: boolean;
  volume: boolean;
}

const OPTIONS: {
  key: keyof IndicatorState; label: string; color?: string;
}[] = [
  { key: 'ma5', label: 'MA5', color: 'var(--warn)' },
  { key: 'ma10', label: 'MA10', color: 'var(--info)' },
  { key: 'ma20', label: 'MA20', color: '#a78bfa' },
  { key: 'ma60', label: 'MA60', color: '#22d3ee' },
  { key: 'boll', label: 'BOLL', color: '#f472b6' },
  { key: 'volume', label: copy.chart.volume },
];

/** Toggle set the panel starts from before localStorage is consulted. */
export const DEFAULT_INDICATORS: IndicatorState = {
  ma5: DEFAULT_CHART_PREFERENCES.main.ma5,
  ma10: DEFAULT_CHART_PREFERENCES.main.ma10,
  ma20: DEFAULT_CHART_PREFERENCES.main.ma20,
  ma60: DEFAULT_CHART_PREFERENCES.main.ma60,
  boll: DEFAULT_CHART_PREFERENCES.main.boll,
  volume: DEFAULT_CHART_PREFERENCES.volume,
};

/** Keep the legacy API while reading the single versioned preference store. */
export function loadIndicators(): IndicatorState {
  const { main, volume } = loadChartPreferences();
  return {
    ma5: main.ma5, ma10: main.ma10,
    ma20: main.ma20, ma60: main.ma60,
    boll: main.boll, volume,
  };
}

/** Update only the six legacy switches without resetting newer preferences. */
export function saveIndicators(state: IndicatorState): void {
  const previous = loadChartPreferences();
  const { volume, ...main } = state;
  saveChartPreferences({
    ...previous, main: { ...previous.main, ...main }, volume,
  });
}

export function IndicatorToggles({
  state,
  onChange,
}: {
  state: IndicatorState;
  onChange: (state: IndicatorState) => void;
}) {
  return (
    <div role="group" aria-label={copy.chart.indicators}
      className="flex flex-wrap items-center gap-1">
      {OPTIONS.map((option) => {
        const active = state[option.key];
        return (
          <button
            key={option.key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange({ ...state, [option.key]: !active })}
            className={cn(
              'flex h-10 items-center gap-1 rounded-[var(--radius-sm)]',
              'border px-1.5 text-[11px] transition-colors',
              active
                ? 'border-[var(--border-strong)] bg-[var(--bg-surface-3)] '
                  + 'text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-tertiary)] '
                  + 'hover:bg-[var(--bg-hover)]',
            )}
          >
            {option.color ? (
              <span
                aria-hidden="true"
                className="size-1.5 rounded-full"
                style={{ background: active ? option.color
                  : 'var(--border-strong)' }}
              />
            ) : null}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
