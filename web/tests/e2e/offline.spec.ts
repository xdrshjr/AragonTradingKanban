import { expect, test } from '@playwright/test';

/**
 * 离线只读（A5 / AC-37 / AC-45 / §18.3.7），端到端。
 *
 * ⚠️ 这条用例**必须**跑在生产构建上：`lib/offline.ts` 在任何非生产环境里都是
 * 彻底的 no-op（开发期注册 service worker 会干扰 HMR，是设计决定，不是缺陷）。
 * 默认的 `playwright.config.ts` 起的是 `next dev`，因此这里显式跳过，并给出
 * 一条能跑的命令：
 *
 *     npm --prefix web run build
 *     E2E_PRODUCTION=1 E2E_BASE_URL=http://127.0.0.1:3000 npx playwright test offline
 *
 * 单元层对同一批语义的覆盖不依赖环境，见 `web/tests/offline.test.ts`。
 */

const PRODUCTION = process.env.E2E_PRODUCTION === '1';
const SYMBOL = '/CN:600519.SH';

test.skip(!PRODUCTION, '需要生产构建（service worker 只在 production 注册）');

test('读过的报告在离线状态下仍能打开，并标注"离线副本"', async ({ page, context }) => {
  // 第一次访问：SW 注册 + 预缓存壳与报告文档。
  await page.goto(SYMBOL);
  await expect(page.getByRole('region', { name: '投研报告' })).toBeVisible();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });

  // 再访问一次，让导航请求真的由 SW 接管（第一次只完成了安装）。
  await page.reload();
  await expect(page.getByRole('region', { name: '投研报告' })).toBeVisible();

  await context.setOffline(true);
  try {
    await page.reload();

    // 断言一：不是白屏——报告正文还在。
    await expect(page.getByRole('region', { name: '投研报告' })).toBeVisible();
    // 断言二：离线副本必须自证过期（★V4-P1-9），不能冒充新报告。
    await expect(page.getByRole('status').filter({ hasText: '离线副本' })).toBeVisible();
  } finally {
    await context.setOffline(false);
  }
});
