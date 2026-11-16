/**
 * Screenshot + geometry probe for manual design review (not part of `npm test`).
 *
 * Uses a locally installed Chrome/Edge because the Playwright browser download is
 * blocked in this environment. It is the only way to check AC-3 (the 15/34/51
 * column geometry) against a real layout engine instead of trusting the maths.
 *
 *   node scripts/screenshot.mjs [url] [outfile] [width] [height]
 */
import { existsSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

import { chromium } from 'playwright-core';

const url = process.argv[2] ?? 'http://127.0.0.1:3000/';
const out = process.argv[3] ?? 'shots/page.png';
const width = Number(process.argv[4] ?? 1440);
const height = Number(process.argv[5] ?? 900);

const CHROME_PATHS = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter((candidate) => Boolean(candidate));

const executablePath = CHROME_PATHS.find((candidate) => existsSync(candidate));
if (!executablePath) {
  console.error('No local Chrome/Edge found — set CHROME_PATH or run `npx playwright install chromium`.');
  process.exit(1);
}

mkdirSync(dirname(out), { recursive: true });

const browser = await chromium.launch({ executablePath, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });

const errors = [];
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text());
});
page.on('pageerror', (error) => errors.push(String(error)));

await page.goto(url, { waitUntil: 'networkidle', timeout: 60_000 });
await page.waitForTimeout(1500);

const geometry = await page.evaluate(() => {
  const measure = (selector) => {
    const element = document.querySelector(selector);
    return element ? Math.round(element.getBoundingClientRect().width) : 0;
  };
  const viewport = window.innerWidth;
  const rail = measure('main > div');
  const kline = measure('[aria-label="K 线面板"]');
  const report = measure('[aria-label="投研报告"]');
  const pct = (value) => Number(((value / viewport) * 100).toFixed(2));
  return {
    viewport,
    rail,
    kline,
    report,
    percent: { rail: pct(rail), kline: pct(kline), report: pct(report) },
    sum: rail + kline + report,
    hasChartCanvas: Boolean(document.querySelector('[data-testid="candle-chart"] canvas')),
    h1: document.querySelector('h1')?.textContent?.trim() ?? null,
  };
});

await page.screenshot({ path: out, fullPage: false });
console.log(JSON.stringify(geometry, null, 2));
console.log(`saved ${out} (${width}x${height})`);

if (errors.length) {
  console.log('console errors:');
  for (const line of errors.slice(0, 10)) console.log('  -', line.slice(0, 300));
} else {
  console.log('no console errors');
}

await browser.close();
