#!/usr/bin/env node
/**
 * Process helpers shared by the `scripts/*.mjs` entry points.
 *
 * Why this exists: since Node 18.20 / 20.12 / 22 — the CVE-2024-27980 fix —
 * `spawn('npm.cmd', …, { shell: false })` fails with `EINVAL` on Windows, because
 * Node no longer runs `.cmd` shims itself. Every script here is a documented
 * entry point (`npm run setup|dev|ci`), so getting this wrong broke the three
 * commands the README opens with.
 */
export const isWin = process.platform === 'win32';

/** The npm launcher name for this platform. */
export function npmCommand() {
  return isWin ? 'npm.cmd' : 'npm';
}

/**
 * Spawn options that can actually run an `npm`/`npx` shim on Windows.
 *
 * With `shell: true` the command goes through `cmd.exe /d /s /c`, which is the
 * only supported way to start a `.cmd` file on Node ≥ 18.20. Arguments are
 * static in every caller, so shell quoting is not a hazard here.
 */
export function spawnOptions(extra = {}) {
  return { shell: isWin, ...extra };
}
