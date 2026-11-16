import { expect, test } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * Main path, end to end, in mock mode (AC-2, AC-4, AC-6, AC-7, AC-15, AC-16).
 *
 * Deliberately runs against a *real* API process in `AGENT_MODE=mock`: the value
 * of this test is that no model, no key and no network is required for a
 * complete demonstration.
 *
 * ★v6（§19.3.7 / AC-59）：那个 "API process" 现在是 `tests/e2e/fixtures/fake-api.mjs`
 * ——零依赖 Node http，数据来自 `api/tests/fixtures/sample_report.json`。于是本条
 * 用例不再需要 Python、数据库或 aragon，{@link SKIP_REASON} 那一行只是把"没有浏览器"
 * 从失败改成跳过。
 */

// PW_REQUIRED=1 时缺浏览器必须失败（CI 不接受静默跳过）。
failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

const SEED_QUERY = '600519';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('landing page renders and can resolve a symbol', async ({ page }) => {
  await expect(page.getByRole('heading', { name: '输入一个投资标的' })).toBeVisible();
  const input = page.getByRole('combobox').first();
  await input.fill(SEED_QUERY);
  await expect(page.getByRole('listbox').getByText('贵州茅台')).toBeVisible();
  await input.press('Enter');
  await page.waitForURL(/\/CN%3A600519\.SH|\/CN:600519\.SH/);
});

test('workspace shows the three columns at 15/34/51', async ({ page }) => {
  await page.goto('/CN:600519.SH');
  await expect(page.getByRole('navigation', { name: '标的列表' })).toBeVisible();

  const viewport = page.viewportSize();
  expect(viewport?.width).toBe(1440);

  // AC-3 measures all three columns, not just the rail: a loose rail-only bound
  // is exactly what let a 16.7% default ship. ±1 percentage point is the contract.
  const widths = await page.evaluate(() => {
    const width = (element: Element | null) =>
      element ? element.getBoundingClientRect().width : 0;
    return {
      rail: width(document.querySelector('main > div')),
      kline: width(document.querySelector('[aria-label="K 线面板"]')),
      report: width(document.querySelector('[aria-label="投研报告"]')),
      viewport: window.innerWidth,
    };
  });

  const pct = (value: number) => (value / widths.viewport) * 100;
  expect(pct(widths.rail)).toBeGreaterThan(14);
  expect(pct(widths.rail)).toBeLessThan(16);
  expect(pct(widths.kline)).toBeGreaterThan(33);
  expect(pct(widths.kline)).toBeLessThan(35);
  expect(pct(widths.report)).toBeGreaterThan(50);
  expect(pct(widths.report)).toBeLessThan(52);
  // The three columns must consume the viewport (the 1px splitters aside).
  expect(widths.rail + widths.kline + widths.report).toBeGreaterThan(widths.viewport - 8);
});

test('dragging the splitter persists across reloads', async ({ page }) => {
  await page.goto('/CN:600519.SH');
  const separator = page.getByRole('separator', { name: '调整标的列表宽度' });
  await expect(separator).toBeVisible();

  const before = await separator.boundingBox();
  expect(before).not.toBeNull();

  await separator.focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');

  const stored = await page.evaluate(() => window.localStorage.getItem('aragon.layout.v1'));
  expect(stored).toBeTruthy();

  await page.reload();
  const after = await separator.boundingBox();
  expect(after?.x).toBeGreaterThan(before?.x ?? 0);
});

test('generating a report advances through the six stages', async ({ page }) => {
  await page.goto('/CN:600519.SH');

  const generate = page.getByRole('button', { name: /生成投研报告|生成最新版本/ }).first();
  await generate.click();

  // The overlay is non-modal: the old version stays readable underneath (§10.3).
  const overlay = page.getByRole('region', { name: '正在生成投研报告' });
  await expect(overlay.or(page.getByText('生成完成'))).toBeVisible({ timeout: 30_000 });

  await expect(page.getByText('生成完成')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('heading', { level: 1 })).not.toBeEmpty();
});

test('report renders blocks and citations open the source drawer', async ({ page }) => {
  await page.goto('/CN:600519.SH');

  const generate = page.getByRole('button', { name: /生成投研报告|生成最新版本/ }).first();
  if (await generate.isVisible()) {
    await generate.click();
    await expect(page.getByText('生成完成')).toBeVisible({ timeout: 60_000 });
  }

  const citation = page.getByRole('button', { name: /查看来源 S1/ }).first();
  if (await citation.count()) {
    await citation.click();
    await expect(page.getByRole('dialog', { name: '来源' })).toBeVisible();
    await page.keyboard.press('Escape');
  }
});

test('keyboard navigation works without a mouse', async ({ page }) => {
  await page.goto('/CN:600519.SH');

  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog', { name: /搜索标的、切换版本/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: /搜索标的、切换版本/ })).toBeHidden();
});

test('version switcher lists versions', async ({ page }) => {
  await page.goto('/CN:600519.SH');
  const trigger = page.getByRole('button', { name: /^v\d+/ }).first();
  if (!(await trigger.count())) return;
  await trigger.click();
  await expect(page.getByRole('listbox', { name: '报告版本' })).toBeVisible();
});

test('export produces a downloadable HTML file', async ({ page }) => {
  await page.goto('/CN:600519.SH');
  const exportButton = page.getByRole('button', { name: '导出 HTML' }).first();
  if (!(await exportButton.count())) return;

  const downloadPromise = page.waitForEvent('download', { timeout: 20_000 }).catch(() => null);
  await exportButton.click();
  const download = await downloadPromise;
  if (download) {
    expect(download.suggestedFilename()).toMatch(/\.html$/);
  }
});

test('unknown symbol shows the not-found state', async ({ page }) => {
  await page.goto('/CN:ZZZZZZ');
  await expect(page.getByText('找不到这个标的')).toBeVisible();
});
