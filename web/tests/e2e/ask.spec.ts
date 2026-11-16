import { expect, test } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * 报告内追问（A1 / AC-27 / AC-40），端到端。
 *
 * 只覆盖 `cite` 档：`mock` 模式下 `features.askDeep=false`，深度档由
 * `api/tests/test_ask_api.py` + 假 aragon 覆盖（§18.7.1 明确的分工）。这里要证明
 * 的是产品承诺：**问一个问题，拿到的是报告原文 + 合法引用，而不是模型转述**。
 *
 * 所有定位都用可访问名（`copy.ts` 里的字符串），不用 CSS 结构——那才是读者/键盘
 * 用户实际"看到"的界面。
 *
 * ★v6（§19.3.7 / AC-59）：接口来自 `tests/e2e/fixtures/fake-api.mjs`（`POST
 * /reports/{id}/ask` 返回固定的 cite 答案，命中块 id 由 fixture 文档数出来），
 * 因此不再需要 Python 侧起来。
 */

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

const SYMBOL = '/CN:600519.SH';

const drawerOf = (page: import('@playwright/test').Page) =>
  page.getByRole('region', { name: '追问这份报告' });

test.beforeEach(async ({ page }) => {
  await page.goto(SYMBOL);
});

test('cite 追问给出命中块，Esc 收起并把焦点还给触发按钮', async ({ page }) => {
  const trigger = page.getByRole('button', { name: '追问' }).first();
  await trigger.click();

  const drawer = drawerOf(page);
  await expect(drawer).toBeVisible();

  await drawer.getByLabel('追问内容').fill('毛利率是多少？');
  await drawer.getByRole('button', { name: '提问' }).click();

  // 命中块是报告原文的切片：标题写"命中 N 处（点击定位）"。
  await expect(drawer.getByRole('heading', { name: /命中 \d+ 处/ })).toBeVisible({ timeout: 20_000 });
  // AC-36：Esc 收起抽屉，焦点回到"追问"按钮（不是 body）。
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
});

test('点击命中块把报告滚到该段落并高亮', async ({ page }) => {
  await page.getByRole('button', { name: '追问' }).first().click();
  const drawer = drawerOf(page);
  await drawer.getByLabel('追问内容').fill('毛利率是多少？');
  await drawer.getByRole('button', { name: '提问' }).click();

  const firstHit = drawer.locator('ul li button').first();
  await expect(firstHit).toBeVisible({ timeout: 20_000 });
  await firstHit.click();

  // 定位的可见证据：该块被打上 `data-highlighted`（2.4s 后自动移除）。
  await expect(page.locator('[data-block-id][data-highlighted="true"]').first()).toBeVisible({
    timeout: 10_000,
  });
});
