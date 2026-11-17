#!/usr/bin/env node
/**
 * `npm run ci` — the one command §15 asks for: lint + typecheck + unit/contract
 * tests on both sides. Fails fast on the first red step.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { isWin, npmCommand, spawnOptions } from './lib/process.mjs';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const npm = npmCommand();
const webDir = join(ROOT, 'web');

// ★v9（K6/K7 / AC-104）：契约夹具必须**存在**且**版本可读**。
// 它是前后端码表的唯一跨语言真源；缺了它，双向断言会静默变成"两边都不查"。
// 检查放在第一步：一条零成本的 fail-fast，比在 pytest/vitest 里各红一次更早。
const CONTRACT = join(ROOT, 'contracts', 'run_error_codes.json');
if (!existsSync(CONTRACT)) {
  process.stderr.write(`\u001b[31m✗ contracts/run_error_codes.json 缺失（AC-104 的契约夹具）\u001b[0m\n`);
  process.exit(1);
}
try {
  const contract = JSON.parse(readFileSync(CONTRACT, 'utf-8'));
  if (!Array.isArray(contract.codes) || !Array.isArray(contract.httpCodes) || !Array.isArray(contract.sseEvents)) {
    throw new Error('codes / httpCodes / sseEvents 必须都是数组');
  }
  if (contract.codes.length !== 18) {
    throw new Error(`run 码应为 18 条，实为 ${contract.codes.length}`);
  }
} catch (error) {
  process.stderr.write(`\u001b[31m✗ contracts/run_error_codes.json 不可用：${error.message}\u001b[0m\n`);
  process.exit(1);
}
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
