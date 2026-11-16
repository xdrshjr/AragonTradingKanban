/**
 * Theme: dark / light / system, persisted and applied without a flash (§6.3).
 * The inline script in `layout.tsx` reads the same key before paint.
 */

export type ThemeMode = 'dark' | 'light' | 'system';
export type ResolvedTheme = 'dark' | 'light';

export const THEME_KEY = 'aragon.theme.v1';
export const LAYOUT_KEY = 'aragon.layout.v1';

export function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? systemTheme() : mode;
}

export function readTheme(): ThemeMode {
  if (typeof window === 'undefined') return 'dark';
  const stored = window.localStorage.getItem(THEME_KEY);
  return stored === 'light' || stored === 'system' || stored === 'dark' ? stored : 'dark';
}

export function applyTheme(mode: ThemeMode): ResolvedTheme {
  const resolved = resolveTheme(mode);
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }
  return resolved;
}

export function writeTheme(mode: ThemeMode): ResolvedTheme {
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(THEME_KEY, mode);
    } catch {
      /* ignore private-mode failures */
    }
  }
  return applyTheme(mode);
}

/** Runs before hydration; keeps the first paint on the right palette. */
export const themeBootstrapScript = `(function(){try{var m=localStorage.getItem('${THEME_KEY}')||'dark';var r=m==='system'?(window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):m;document.documentElement.dataset.theme=r;document.documentElement.style.colorScheme=r;}catch(e){document.documentElement.dataset.theme='dark';}})();`;

/** Applies persisted layout tokens before hydration so the grid never jumps. */
export const layoutBootstrapScript = `(function(){try{var raw=localStorage.getItem('${LAYOUT_KEY}');var s=raw?JSON.parse(raw):{};var r=document.documentElement;var rail=typeof s.railPx==='number'&&s.railPx>=200?Math.min(400,s.railPx):Math.min(400,Math.max(200,Math.round(window.innerWidth*0.15)));var f=typeof s.klineFraction==='number'?s.klineFraction:0.4;var rem=Math.max(0,window.innerWidth-rail);r.style.setProperty('--rail-w',rail+'px');r.style.setProperty('--kline-w',Math.round(rem*f)+'px');r.style.setProperty('--report-w',Math.round(rem*(1-f))+'px');}catch(e){}})();`;
