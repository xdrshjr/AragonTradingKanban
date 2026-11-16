#!/usr/bin/env node
/**
 * `npm run setup` — check the toolchain, create api/.venv, install both sides,
 * run migrations, and print a per-item repair hint for anything missing.
 * Cross-platform: no shell built-ins, no `&&`, path join via node:path.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, copyFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { isWin, npmCommand, spawnOptions } from './lib/process.mjs';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const venvDir = join(ROOT, 'api', '.venv');
const venvPython = isWin ? join(venvDir, 'Scripts', 'python.exe') : join(venvDir, 'bin', 'python');

const problems = [];
const notes = [];

function run(cmd, args, opts = {}) {
  return spawnSync(
    cmd,
    args,
    spawnOptions({
      cwd: opts.cwd ?? ROOT,
      stdio: opts.stdio ?? 'inherit',
      encoding: 'utf8',
    }),
  );
}

function capture(cmd, args) {
  const r = spawnSync(cmd, args, spawnOptions({ cwd: ROOT, encoding: 'utf8' }));
  return { ok: r.status === 0, out: `${r.stdout ?? ''}${r.stderr ?? ''}`.trim() };
}

function step(title) {
  process.stdout.write(`\n\u001b[36m▸ ${title}\u001b[0m\n`);
}

// 1. node
const nodeMajor = Number(process.versions.node.split('.')[0]);
if (nodeMajor < 20) problems.push(`Node ${process.versions.node} < 20 — install Node 20+ (https://nodejs.org)`);
else notes.push(`node ${process.versions.node} ✓`);

// 2. python
let py = capture('python', ['--version']);
if (!py.ok) py = capture('python3', ['--version']);
if (!py.ok) {
  problems.push('python not found — install Python 3.11+ and put it on PATH');
} else {
  const m = /(\d+)\.(\d+)/.exec(py.out);
  const minor = m ? Number(m[1]) * 100 + Number(m[2]) : 0;
  if (minor < 311) problems.push(`python ${py.out} < 3.11 — install Python 3.11+`);
  else notes.push(`python ${py.out} ✓`);
}

// 3. aragon (optional — mock mode works without it)
const aragon = capture(isWin ? 'aragon.cmd' : 'aragon', ['info', '--json']);
if (!aragon.ok) {
  notes.push('aragon CLI not found → the app runs in mock mode (AGENT_MODE=auto will pick mock)');
} else {
  try {
    const info = JSON.parse(aragon.out.slice(aragon.out.indexOf('{')));
    const hasSubmit = Array.isArray(info.tools) && info.tools.includes('submit_result');
    notes.push(
      `aragon ${info.cli} (core ${info.core}, provider ${info.provider}/${info.model}, ` +
        `apiKey=${info.hasApiKey}, ajv=${info.result?.ajv}, submit_result=${hasSubmit}) ✓`,
    );
    if (!hasSubmit) notes.push('  ⚠ submit_result missing → structured generation impossible, mock mode will be used');
    if (!info.hasApiKey) notes.push('  ⚠ no model API key → mock mode will be used');
  } catch {
    notes.push('aragon CLI present but `info --json` was not parseable');
  }
}

// 4. .env
if (!existsSync(join(ROOT, '.env'))) {
  copyFileSync(join(ROOT, '.env.example'), join(ROOT, '.env'));
  notes.push('.env created from .env.example');
} else {
  notes.push('.env present ✓');
}

if (problems.length > 0) {
  process.stdout.write('\n\u001b[31m✗ Missing prerequisites:\u001b[0m\n');
  for (const p of problems) process.stdout.write(`  - ${p}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write('\n\u001b[32m✓ Toolchain ready\u001b[0m\n');
}
for (const n of notes) process.stdout.write(`  ${n}\n`);

if (process.exitCode === 1) process.exit(1);

// 5. venv + python deps
if (!existsSync(venvPython)) {
  step(`creating virtualenv at api/.venv`);
  const mk = run(process.platform === 'win32' ? 'python' : 'python3', ['-m', 'venv', join(ROOT, 'api', '.venv')]);
  if (mk.status !== 0) {
    process.stderr.write('failed to create the venv; run `python -m venv api/.venv` manually\n');
    process.exit(1);
  }
  notes.push('venv created');
}

step('installing python dependencies');
const pipArgs = ['-m', 'pip', 'install', '--disable-pip-version-check', '-e', join(ROOT, 'api')];
if (run(venvPython, pipArgs).status !== 0) {
  process.stderr.write('\npip install failed — check your network, then re-run `npm run setup`\n');
  process.exit(1);
}

step('installing node dependencies');
if (run(npmCommand(), ['install', '--no-audit', '--no-fund'], { cwd: join(ROOT, 'web') }).status !== 0) {
  process.stderr.write('\nnpm install (web) failed\n');
  process.exit(1);
}
if (run(npmCommand(), ['install', '--no-audit', '--no-fund'], { cwd: ROOT }).status !== 0) {
  process.stderr.write('\nnpm install (root) failed\n');
  process.exit(1);
}

// 6. database
mkdirSync(join(ROOT, 'api', 'data'), { recursive: true });
step('applying database migrations (alembic upgrade head)');
const mig = run(venvPython, ['-m', 'alembic', 'upgrade', 'head'], { cwd: join(ROOT, 'api') });
if (mig.status !== 0) {
  process.stderr.write('\nmigration failed — see the traceback above\n');
  process.exit(1);
}

process.stdout.write('\n\u001b[32m✓ Setup complete\u001b[0m\n');
process.stdout.write('  npm run dev    → API :8000 + web :3000\n');
process.stdout.write('  npm run seed   → 3 demo instruments in the rail\n');
