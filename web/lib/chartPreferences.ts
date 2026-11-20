/** Browser display preferences. URL state and temporary tools stay outside. */
export interface ChartPreferences {
  version: 2;
  main: {
    ma5: boolean;
    ma10: boolean;
    ma20: boolean;
    ma60: boolean;
    ema: boolean;
    boll: boolean;
  };
  volume: boolean;
  oscillator: 'macd' | 'rsi' | 'kdj' | 'atr' | 'none';
  style: 'candlestick' | 'line';
  scale: 'linear' | 'log';
}

export const DEFAULT_CHART_PREFERENCES: ChartPreferences = {
  version: 2,
  main: {
    ma5: true, ma10: false, ma20: true, ma60: true,
    ema: false, boll: false,
  },
  volume: true,
  oscillator: 'macd',
  style: 'candlestick',
  scale: 'linear',
};

const STORAGE_KEY = 'aragon.chart.v2';
const LEGACY_KEY = 'aragon.indicators.v1';

function defaults(): ChartPreferences {
  return {
    ...DEFAULT_CHART_PREFERENCES,
    main: { ...DEFAULT_CHART_PREFERENCES.main },
  };
}

function parseStored(value: string | null): Record<string, unknown> | null {
  if (value === null) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function parsePreferences(stored: Record<string, unknown>): ChartPreferences {
  const result = defaults();
  const main = stored.main as Record<string, unknown> | undefined;
  for (const key of Object.keys(result.main) as
    Array<keyof ChartPreferences['main']>) {
    if (typeof main?.[key] === 'boolean') result.main[key] = main[key];
  }
  if (typeof stored.volume === 'boolean') result.volume = stored.volume;
  if (['macd', 'rsi', 'kdj', 'atr', 'none'].includes(
    String(stored.oscillator),
  )) result.oscillator = stored.oscillator as ChartPreferences['oscillator'];
  if (['candlestick', 'line'].includes(String(stored.style))) {
    result.style = stored.style as ChartPreferences['style'];
  }
  if (['linear', 'log'].includes(String(stored.scale))) {
    result.scale = stored.scale as ChartPreferences['scale'];
  }
  return result;
}

/** Read after mount; unavailable storage and malformed values use defaults. */
export function loadChartPreferences(): ChartPreferences {
  if (typeof window === 'undefined') return defaults();
  try {
    const stored = parseStored(window.localStorage.getItem(STORAGE_KEY));
    if (stored?.version === 2) return parsePreferences(stored);
    if (stored?.version != null) return defaults();
    const legacy = parseStored(window.localStorage.getItem(LEGACY_KEY));
    if (!legacy) return defaults();
    const migrated = parsePreferences({
      version: 2, main: legacy, volume: legacy.volume,
    });
    saveChartPreferences(migrated);
    return migrated;
  } catch {
    return defaults();
  }
}

/** Persist explicit changes; private browsing failures do not block charts. */
export function saveChartPreferences(settings: ChartPreferences): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Storage is optional: the current tab still owns the chosen preferences.
  }
}
