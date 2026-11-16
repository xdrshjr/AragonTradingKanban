import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * AC-17 guard: no raw HTML injection beyond the theme bootstrap, no `any`.
 *
 * The design (§6.3) requires a pre-paint theme script to avoid a flash of the
 * wrong palette, and an inline script can only be injected with
 * `dangerouslySetInnerHTML`. That is exactly **two** call sites, both fed by
 * frozen constants in `lib/theme.ts` — never by server data or user input.
 *
 * So the enforced rule is the enforceable version of the AC: raw HTML injection
 * is permitted only for those two scripts, and their bodies may interpolate only
 * module-level string literals (storage keys), never runtime values.
 */

const ALLOWED_SITES = new Set(['app/layout.tsx']);
const ROOTS = ['app', 'components', 'lib'];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      walk(full, out);
    } else if (/\.(tsx?|mts)$/.test(entry)) {
      out.push(full.split('\\').join('/'));
    }
  }
  return out;
}

function sourceFiles(): string[] {
  return ROOTS.flatMap((root) => walk(root)).filter(
    (path) => !path.endsWith('.test.ts') && !path.endsWith('.test.tsx'),
  );
}

describe('AC-17: raw HTML injection is confined to the theme bootstrap', () => {
  it('uses dangerouslySetInnerHTML only in app/layout.tsx', () => {
    const offenders = sourceFiles().filter((path) => {
      const text = readFileSync(path, 'utf8');
      return text.includes('dangerouslySetInnerHTML') && !ALLOWED_SITES.has(path);
    });
    expect(offenders).toEqual([]);
  });

  it('injects only the two pre-computed scripts', () => {
    const layout = readFileSync('app/layout.tsx', 'utf8');
    const injected = [...layout.matchAll(/__html:\s*([A-Za-z_$][\w$]*)/g)].map((match) => match[1]);
    expect(new Set(injected)).toEqual(new Set(['themeBootstrapScript', 'layoutBootstrapScript']));
  });

  it('interpolates only module-level string constants into those scripts', () => {
    const theme = readFileSync('lib/theme.ts', 'utf8');

    // Every exported const holding a bootstrap script.
    const scripts = [...theme.matchAll(/export const (\w+BootstrapScript)\s*=\s*`([^`]*)`/g)];
    expect(scripts).toHaveLength(2);

    // The storage keys the scripts are allowed to reference.
    const declaredKeys = new Set(
      [...theme.matchAll(/export const (\w*_KEY)\s*=\s*'([^']*)'/g)].map((match) => match[1]),
    );
    expect(declaredKeys.size).toBeGreaterThanOrEqual(2);

    for (const [, name, body] of scripts) {
      expect(name).toMatch(/BootstrapScript$/);
      const script = body ?? '';
      expect(script).not.toContain('window.location');
      expect(script).not.toContain('innerHTML');
      // Every `${...}` placeholder must be one of the declared storage keys.
      const placeholders = [...script.matchAll(/\$\{(\w+)\}/g)].map((match) => match[1]);
      for (const placeholder of placeholders) {
        expect(declaredKeys.has(placeholder ?? '')).toBe(true);
      }
      // No interpolation of anything that is not a simple identifier.
      expect(script).not.toMatch(/\$\{(?!\w+\})/);
    }
  });
});

describe('AC-17: no explicit `any` in shipped source', () => {
  it('never declares or casts to any', () => {
    const offenders: string[] = [];
    for (const path of sourceFiles()) {
      const text = readFileSync(path, 'utf8');
      const withoutComments = text
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:])\/\/.*$/gm, '$1');
      if (/(:\s*any\b|\bas\s+any\b|<any>|\bany\[\])/.test(withoutComments)) {
        offenders.push(path);
      }
    }
    expect(offenders).toEqual([]);
  });
});
