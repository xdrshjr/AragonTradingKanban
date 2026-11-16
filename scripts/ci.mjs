#!/usr/bin/env node
/**
 * `npm run ci` — the one command §15 asks for: lint + typecheck + unit/contract
 * tests on both sides. Fails fast on the first red step.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { isWin, npmCommand, spawnOptions } from './lib/process.mjs';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const npm = npmCommand();
const webDir = join(ROOT, 'web');
const venvPython = isWin
  ? join(ROOT, 'api', '.venv', 'Scripts', 'python.exe')
  : join(ROOT, 'api', '.venv', 'bin', 'python');
const python = existsSync(venvPython) ? venvPython : isWin ? 'python' : 'python3';

const steps = [
  { label: 'api · ruff', cmd: python, args: ['-m', 'ruff', 'check', 'app', 'tests'], cwd: join(ROOT, 'api'), optional: true },
  { label: 'api · pytest', cmd: python, args: ['-m', 'pytest', '-q'], cwd: join(ROOT, 'api') },
  { label: 'web · typecheck', cmd: npm, args: ['run', 'typecheck'], cwd: webDir },
  { label: 'web · lint', cmd: npm, args: ['run', 'lint'], cwd: webDir },
  { label: 'web · vitest', cmd: npm, args: ['run', 'test'], cwd: webDir },
];

let failed = 0;
for (const step of steps) {
  process.stdout.write(`\n\u001b[36m▸ ${step.label}\u001b[0m\n`);
  const r = spawnSync(step.cmd, step.args, spawnOptions({ cwd: step.cwd, stdio: 'inherit' }));
  if ((r.status ?? 1) !== 0) {
    if (step.optional) {
      process.stdout.write(`\u001b[33m⚠ ${step.label} not available — skipped\u001b[0m\n`);
      continue;
    }
    failed += 1;
    process.stderr.write(`\u001b[31m✗ ${step.label} failed\u001b[0m\n`);
    break;
  }
}

if (failed > 0) process.exit(1);
process.stdout.write('\n\u001b[32m✓ CI green\u001b[0m\n');
