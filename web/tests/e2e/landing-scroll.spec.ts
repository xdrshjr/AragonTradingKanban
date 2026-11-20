import { expect, test } from '@playwright/test';

import {
  SKIP_REASON, browserAvailable, failIfBrowserRequired,
} from './fixtures/browser';

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

for (const [width, height] of [
  [1440, 900], [1280, 600], [1024, 480], [390, 844], [320, 568],
  [720, 450],
]) {
  test(`首页 ${width}×${height} 可用鼠标和键盘滚到底部`, async ({ page, context }) => {
    await context.addCookies([{ name: 'aragon_sid',
      value: 'ask-e2e-landing', domain: '127.0.0.1', path: '/' }]);
    await page.setViewportSize({ width: width!, height: height! });
    await page.goto('/');
    const main = page.getByRole('main');
    const geometry = await main.evaluate((element) => ({
      height: element.clientHeight, content: element.scrollHeight,
      width: element.clientWidth, contentWidth: element.scrollWidth,
    }));
    expect(geometry.content).toBeGreaterThan(geometry.height);
    expect(geometry.height).toBeLessThanOrEqual(height!);
    expect(geometry.contentWidth).toBeLessThanOrEqual(geometry.width);
    await main.hover({ position: { x: 10, y: 100 } });
    await page.mouse.wheel(0, 5000);
    await expect.poll(() => main.evaluate((e) => e.scrollTop))
      .toBeGreaterThan(0);
    const footer = page.locator('footer');
    await expect.poll(async () => {
      const rect = (await footer.boundingBox())!;
      return rect.y + rect.height;
    })
      .toBeLessThanOrEqual(height!);
    await main.focus();
    await page.keyboard.press('Home');
    await expect.poll(() => main.evaluate((e) => e.scrollTop)).toBe(0);
    await page.keyboard.press('End');
    await expect.poll(async () => {
      const rect = (await footer.boundingBox())!;
      return rect.y + rect.height;
    })
      .toBeLessThanOrEqual(height!);
    await expect(page.locator('[data-report-id]')).toHaveCount(6);
    await expect(page.getByRole('link', { name: '查看全部 6 份报告' }))
      .toBeVisible();
    await page.screenshot({ path:
      `../docs/plans/terminal-experience/screenshots/S01-home-${width}x${height}-dark.png`,
    });
  });
}
