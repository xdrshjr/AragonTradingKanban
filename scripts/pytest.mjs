#!/usr/bin/env node
/** `npm run test:api` — run pytest with the api venv if it exists. */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const isWin = process.platform === 'win32';
const venvPython = isWin
  ? join(ROOT, 'api', '.venv', 'Scripts', 'python.exe')
  : join(ROOT, 'api', '.venv', 'bin', 'python');
const python = existsSync(venvPython) ? venvPython : isWin ? 'python' : 'python3';

const r = spawnSync(python, ['-m', 'pytest', '-q', ...process.argv.slice(2)], {
  cwd: join(ROOT, 'api'),
  stdio: 'inherit',
  shell: false,
});
process.exit(r.status ?? 1);
