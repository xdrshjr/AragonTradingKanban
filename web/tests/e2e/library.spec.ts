import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * 报告库 → 打开"我点的那一版"（AC-143 / U-0b / U-0c），端到端。
 *
 * 三条用例证明的是一件事的三个面：
 *
 * 1. **AC-143**：库页每一行的 `href` 都形如 `/{symbol}?report={32 位 hex}`，且 `report=`
 *    就是**那一行自己的** id（不是"标的的 id"，也不是最新版的 id）。形状不对，U-0b 就已经
 *    在 URL 层失守了——`web/app/page.tsx:100` 曾经写的是 `href={/${instrument.id}}`，
 *    于是点 v12 的卡片进去看到的是 v13。
 * 2. **U-0b**：点旧版本那一行，打开的就是那一版（历史版本横幅点名 v1），**不是**被静默
 *    升级成最新版。只断言 URL 不够——URL 对了、面板却是最新版，是最难发现的一种错。
 * 3. **U-0c**：`?report=` 是"我在看哪一版"的唯一真源，所以 F5 之后 URL 与画面都不变。
 *
 * ★e2e 与"切换版本"的分工：本文件只钉住 **URL 契约**（`?report=` 是谁写的、写成了什么），
 * 而"换了版本之后**面板里真的是那一版**"由 `web/tests/workspace-url.test.tsx` 的第 4 条用例
 * 守着（它在 v14 期间抓到过一个真缺陷：`initialData` 是不带 id 守卫的数据 + `staleTime:
 * Infinity`，于是新 key 既"有数据"又"永不陈旧"⇒ 界面停在旧版，见 spec「实施过程发现的方案
 * 缺陷」D-8；修复是 `initialData` 只在 `initialDetail.report.id === selectedReportId` 时才给）。
 * 一条 e2e 在这里重测那件事只会更慢、更容易假绿，所以不做。
 *
 * 数据来自 `tests/e2e/fixtures/fake-api.mjs`（零依赖 Node，无 Python / 无数据库 / 无网络）：
 * 它服务 `600519.SH` 的两版（v2 = 最新，v1 = 历史）与 `GET /api/v1/reports`。
 *
 * ★纪律（spec §19.3.7 / AC-59）：本机没有 chromium 时**跳过**而不是失败——那是"环境缺失"，
 * 不是"代码坏了"。`PW_REQUIRED=1` 时才反过来（CI 不接受静默跳过）。
 */

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

/** AC-143 的原文形状（spec 表格里的正则，一字不改）。 */
const ROW_HREF = /^\/[A-Za-z0-9._-]+\?report=[0-9a-f]{32}$/;

/** fake API 只服务这一个标的（`libraryHref()` 用 symbol，不用 `CN:` 前缀的 instrumentId）。 */
const SYMBOL_PATH = '/600519.SH';

const HEX_ID = /^[0-9a-f]{32}$/;

/** 库页的一行 = `role="option"` + `data-report-id`（`ReportLibraryRow` 的两个属性）。 */
const rowsOn = (page: Page) => page.locator('[role="option"][data-report-id]');

/** 旧版本那一行。可访问名形如「贵州茅台，v1，3 天前，可信 92%，示例」。 */
const olderRowOn = (page: Page) => page.getByRole('option', { name: /，v1，/ });

/** `href` → 绝对 URL（只为了拿 query，不起服务器）。 */
const asUrl = (href: string | null) => new URL(href ?? '', 'http://playwright.local');

/** 当前页的 `?report=`（没有就是 null）。 */
const reportParamOf = (page: Page) => new URL(page.url()).searchParams.get('report');

test('库页每一行都带 ?report=32 位 hex，且指向它自己那一版（AC-143）', async ({ page }) => {
  await page.goto('/reports');

  const rows = rowsOn(page);
  // 首次访问 `/reports` 时 Next dev 会现编译该路由，所以首屏断言给 30s。
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });

  // fake 服务两版（v2 最新 / v1 历史）⇒ 至少两行，且必须是**两行不同的**报告。
  const count = await rows.count();
  expect(count).toBeGreaterThanOrEqual(2);

  const ids = await rows.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute('data-report-id') ?? ''),
  );
  expect(new Set(ids).size).toBe(count);

  const hrefs = await rows.evaluateAll((nodes) =>
    nodes.map((node) => node.querySelector('a')?.getAttribute('href') ?? ''),
  );
  expect(hrefs).toHaveLength(count);

  for (const [index, href] of hrefs.entries()) {
    // ① 形状：AC-143 的正则。
    expect(href).toMatch(ROW_HREF);
    // ② 内容：`report=` 必须等于**这一行**的 id（否则"点进去看到别的版本"已经发生了）。
    expect(asUrl(href).searchParams.get('report')).toBe(ids[index]);
    expect(asUrl(href).pathname).toBe(SYMBOL_PATH);
  }
});

test('点旧版本那一行，打开的就是那一版，不是最新版（U-0b）', async ({ page }) => {
  await page.goto('/reports');

  const olderRow = olderRowOn(page);
  await expect(olderRow).toHaveCount(1, { timeout: 30_000 });

  const href = await olderRow.locator('a').getAttribute('href');
  expect(href).toMatch(ROW_HREF);
  const clickedId = asUrl(href).searchParams.get('report') ?? '';
  expect(clickedId).toMatch(HEX_ID);

  await olderRow.locator('a').click();
  await page.waitForURL((url) => url.searchParams.get('report') === clickedId);

  // ① URL 钉住的是**点下去的那一个 id**，不是最新版的 id。
  const url = new URL(page.url());
  expect(decodeURIComponent(url.pathname)).toBe(SYMBOL_PATH);
  expect(url.searchParams.get('report')).toBe(clickedId);

  // ② 画面也是那一版：历史版本横幅点名 v1（fake 的最新版是 v2）。
  //    只断言 URL 会漏掉最坏的一种错误——URL 对、面板却是最新版。
  await expect(page.getByText('正在查看历史版本 v1')).toBeVisible();
});

test('打开旧版本后刷新，URL 与画面都还是那一版（U-0c）', async ({ page }) => {
  await page.goto('/reports');
  await olderRowOn(page).locator('a').click();
  await page.waitForURL((url) => url.searchParams.get('report') !== null);
  await expect(page.getByText('正在查看历史版本 v1')).toBeVisible();

  const openedId = reportParamOf(page);
  expect(openedId).toMatch(HEX_ID);

  // F5：`?report=` 是"我在看哪一版"的唯一真源，刷新之后既不丢参数、也不偷偷回到最新版。
  await page.reload();
  await expect(page.getByText('正在查看历史版本 v1')).toBeVisible();
  expect(reportParamOf(page)).toBe(openedId);
});

test('分页游标：第一页给游标，加载更多之后到底（nextCursor → null）', async ({ page }) => {
  await page.goto('/reports');
  // 首次访问 `/reports` 时 Next dev 会现编译该路由，给足时间（其余用例共享已编译的产物）。
  await expect(rowsOn(page).first()).toBeVisible({ timeout: 30_000 });

  // fake 的第一页带 `nextCursor` ⇒ 出现「加载更多」；第二页（带 `before`）返回空列表 +
  // `nextCursor: null` ⇒ 文案变成「已经到底了」。整条 `useInfiniteQuery` 路径都走到。
  const more = page.getByRole('button', { name: '加载更多' });
  await expect(more).toBeVisible();
  await more.click();
  await expect(page.getByText('已经到底了')).toBeVisible();
});
