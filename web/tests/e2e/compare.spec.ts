import { expect, test } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * 版本比较（A3 / AC-34），端到端。
 *
 * 前置：该标的至少有两版报告（`npm run seed` 之后在 mock 模式生成一次即可）。
 * 入口只有一处——报告头的 `DiffBadge`。这正是本轮修掉的缺陷：徽标要靠
 * `diffCount` 才出现，而 `diffCount` 曾经依赖"比较面板已打开"，于是整条路径不可达。
 *
 * ★v6（§19.3.7 / AC-59）：fake API 直接提供 v1/v2 两版（第二版从 fixture 派生），
 * `GET /reports/{a}/diff/{b}` 给出真实的指标/块差异，所以不需要种子数据或生成。
 */

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

const SYMBOL = '/CN:600519.SH';

test('报告头有比较入口，打开后能读出指标变化', async ({ page }) => {
  await page.goto(SYMBOL);

  const badge = page.getByRole('button', { name: /版本比较|与上一版比较/ }).first();
  await expect(badge).toBeVisible({ timeout: 15_000 });
  await badge.click();

  const panel = page.getByRole('region', { name: '版本比较' });
  await expect(panel).toBeVisible();

  // 面板必须给出人话摘要（§18.5.4），而不是一屏原始 JSON。
  await expect(panel).toContainText(/指标变化|评级变化|没有结构性差异/);

  // AC-36：Esc 退出比较，焦点回到触发徽标。
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(badge).toBeFocused();
});
