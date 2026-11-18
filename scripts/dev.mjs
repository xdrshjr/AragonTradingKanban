#!/usr/bin/env node
/**
 * `npm run dev` — start the API (uvicorn, **single worker**: run orchestration is
 * in-process, see spec §7.1 / R-13) and the web dev server side by side, prefix
 * every line with `[api]` / `[web]`, and exit as soon as either of them exits.
 */
import { spawn } from 'node:child_process';
import { existsSync, copyFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { isWin, npmCommand, spawnOptions } from './lib/process.mjs';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const envPath = join(ROOT, '.env');

if (!existsSync(envPath)) {
  copyFileSync(join(ROOT, '.env.example'), envPath);
  process.stdout.write('[dev] .env was missing — created it from .env.example\n');
}

const venvPython = isWin
  ? join(ROOT, 'api', '.venv', 'Scripts', 'python.exe')
  : join(ROOT, 'api', '.venv', 'bin', 'python');
const python = existsSync(venvPython) ? venvPython : isWin ? 'python' : 'python3';
const npm = npmCommand();

const children = [];
let shuttingDown = false;

function start(label, cmd, args, cwd, color, extraEnv = {}) {
  // `spawnOptions` carries `shell: true` on Windows: npm is a `.cmd` shim and
  // Node refuses to spawn one directly on 18.20+/20.12+/22 (spawn EINVAL).
  const child = spawn(cmd, args, spawnOptions({ cwd, env: { ...process.env, ...extraEnv } }));
  const prefix = `\u001b[${color}m[${label}]\u001b[0m `;
  const pipe = (stream, target) => {
    let buffer = '';
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) target.write(`${prefix}${line}\n`);
    });
    stream.on('end', () => {
      if (buffer.length > 0) target.write(`${prefix}${buffer}\n`);
    });
  };
  pipe(child.stdout, process.stdout);
  pipe(child.stderr, process.stderr);
  child.on('exit', (code, signal) => {
    process.stdout.write(`${prefix}exited (code=${code ?? 'null'} signal=${signal ?? 'null'})\n`);
    shutdown(code === 0 || shuttingDown ? 0 : (code ?? 1));
  });
  child.on('error', (err) => {
    process.stderr.write(`${prefix}failed to start: ${err.message}\n`);
    shutdown(1);
  });
  children.push(child);
  return child;
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode === null) {
      if (isWin) spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
      else child.kill('SIGTERM');
    }
  }
  setTimeout(() => process.exit(code), 250);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

process.stdout.write('\u001b[36m[dev]\u001b[0m API :8000 (workers=1)   web :3000\n');

// ★v12（S1 / §22.3.2(g) / AC-113）：`--reload` 在 Windows 上是**能力杀手**，不是开发便利。
// 实测（E-88）：uvicorn 的 `use_subprocess = bool(reload or workers > 1)`（uvicorn/config.py:378-379）
// 在 Windows 上因此装 `SelectorEventLoop`，而 `asyncio.create_subprocess_exec` 在它上面
// 直接抛 `NotImplementedError('')` ⇒ **每一次生成都在 research 阶段以 internal 结束**
// （runs.7aa5aeaa 就是那一行）。所以默认**不带** `--reload`，要热重载必须显式承担后果：
//   DEV_RELOAD=1 npm run dev
const reload = process.env.DEV_RELOAD === '1';
if (reload) {
  process.stdout.write(
    '\u001b[33m[dev]\u001b[0m 警告：DEV_RELOAD=1 —— 热重载会关掉子进程能力，生成将全部失败\n',
  );
}

start(
  'api',
  python,
  [
    '-m',
    'uvicorn',
    'app.main:app',
    ...(reload ? ['--reload'] : []),
    '--port',
    process.env.PY_API_PORT || '8000',
    '--workers',
    '1',
  ],
  join(ROOT, 'api'),
  '32',
);
// Force the development build: `next dev` miscompiles its CSS pipeline when an
// inherited NODE_ENV=production leaks in from the parent shell.
start('web', npm, ['run', 'dev'], join(ROOT, 'web'), '35', { NODE_ENV: 'development' });
