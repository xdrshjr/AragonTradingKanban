import { expect, test } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * 报告内追问（A1 / AC-27 / AC-40），端到端。
 *
 * 默认场景覆盖 cite，独立 cookie 场景覆盖真实浏览器内的
 * 202 → 有界 SSE → GET 核验、多轮上下文与恢复。
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
const ARTIFACTS = '../docs/plans/terminal-experience/screenshots';
let browserErrors: string[] = [];

const screenshot = (page: import('@playwright/test').Page, name: string) =>
  page.screenshot({ path: `${ARTIFACTS}/${name}.png` });

const drawerOf = (page: import('@playwright/test').Page) =>
  page.getByRole('region', { name: 'AI 助手', exact: true });

test.beforeEach(async ({ page }) => {
  browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  if (process.env.E2E_PRODUCTION === '1') {
    await page.route('http://127.0.0.1:8000/api/v1/**', async (route) => {
      const url = route.request().url().replace(':8000/', ':8010/');
      await route.fulfill({ response: await route.fetch({ url }) });
    });
  }
  await page.goto(SYMBOL);
});

test.afterEach(async ({ page }) => {
  // Production forwarding reads bounded fixture streams to completion.
  // Finish those handlers before Playwright closes this test's context.
  if (process.env.E2E_PRODUCTION === '1') {
    await page.unrouteAll({ behavior: 'wait' });
  }
  expect(browserErrors.filter((message) => /hydration|not defined/i.test(message)))
    .toEqual([]);
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

test('圆形入口与工具栏共享面板，桌面高度720px且收起保留草稿', async ({ page }) => {
  const launcher = page.getByRole('button', { name: '打开 AI 助手', exact: true });
  await expect(launcher).toBeVisible();
  await screenshot(page, 'S02-desktop-1440x900-dark');
  await launcher.click();
  const drawer = drawerOf(page);
  await expect(drawer).toBeVisible();
  await expect.poll(async () => Math.round((await drawer.boundingBox())!.height))
    .toBe(720);
  const panel = (await drawer.boundingBox())!;
  const button = (await page.locator('[data-assistant-launcher]').boundingBox())!;
  expect(Math.round(button.y - (panel.y + panel.height))).toBe(12);
  await expect(drawer.getByLabel('追问内容')).toBeEditable();
  await screenshot(page, 'S03-empty-1440x900-dark');
  await drawer.getByLabel('追问内容').fill('未发送的草稿');
  await page.keyboard.press('Escape');
  await expect(launcher).toBeFocused();
  await page.getByRole('button', { name: '追问', exact: true }).click();
  await expect(drawer.getByLabel('追问内容')).toHaveValue('未发送的草稿');
  await expect(drawerOf(page)).toHaveCount(1);
});

for (const [width, height] of [[390, 844], [568, 320], [390, 320]]) {
  test(`助手 ${width}×${height} 关闭输入发送均可达`, async ({ page }) => {
    await page.setViewportSize({ width: width!, height: height! });
    await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
    const drawer = drawerOf(page);
    await expect(drawer).toHaveAttribute('data-compact', 'true');
    await drawer.getByLabel('追问内容').fill('第一行\n第二行\n第三行\n第四行');
    for (const control of [
      drawer.getByRole('button', { name: '收起 AI 助手', exact: true }),
      drawer.getByLabel('追问内容'),
      drawer.getByRole('button', { name: '提问', exact: true }),
    ]) {
      const rect = (await control.boundingBox())!;
      expect(rect.y).toBeGreaterThanOrEqual(0);
      expect(rect.y + rect.height).toBeLessThanOrEqual(height!);
    }
    await screenshot(page, `S07-compact-${width}x${height}-dark`);
  });
}

test('两轮深度分析走202/SSE/GET并提交前轮id，新话题清上下文', async ({ page, context }) => {
  const sid = `ask-e2e-${test.info().testId.replace(/\W/g, '')}`;
  await context.addCookies([{ name: 'aragon_sid', value: sid,
    domain: '127.0.0.1', path: '/' }]);
  const api = 'http://127.0.0.1:8010/api/v1';
  await context.request.delete(`${api}/test/assistant`);
  await page.goto(SYMBOL);
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const drawer = drawerOf(page);
  await drawer.getByRole('radio', { name: '深度分析', exact: true }).click();
  await drawer.getByLabel('追问内容').fill('总结核心结论');
  await drawer.getByRole('button', { name: '深度追问', exact: true }).click();
  await expect(drawer.getByText(/第一，盈利稳定/)).toBeVisible();
  await expect.poll(async () => (await context.request.get(
    `${api}/test/assistant`)).json().then((body) => body.asks[0]?.status))
    .toBe('done');
  await drawer.getByLabel('追问内容').fill('刚才第二点有哪些证据？');
  await drawer.getByRole('button', { name: '深度追问', exact: true }).click();
  await expect(drawer.getByText(/刚才第二点的估值假设/)).toBeVisible();
  await screenshot(page, 'S04-two-turns-1440x900-dark');
  const state = await (await context.request.get(`${api}/test/assistant`)).json();
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1].contextAskIds).toEqual([state.asks[0].id]);
  await expect(drawer.getByRole('button', { name: '新话题', exact: true }))
    .toBeEnabled();
  await drawer.getByRole('button', { name: '新话题', exact: true }).click();
  await drawer.getByLabel('追问内容').fill('新的风险分析');
  await drawer.getByRole('button', { name: '深度追问', exact: true }).click();
  await expect.poll(async () => (await (await context.request.get(
    `${api}/test/assistant`)).json()).posts.length).toBe(3);
  const finalState = await (await context.request.get(`${api}/test/assistant`)).json();
  expect(finalState.posts[2].contextAskIds).toEqual([]);
});

test('来源覆盖助手，Esc逐层关闭并归还原引用焦点', async ({ page }) => {
  const launcher = page.getByRole('button', { name: '打开 AI 助手', exact: true });
  await launcher.click();
  const drawer = drawerOf(page);
  await drawer.getByLabel('追问内容').fill('毛利率是多少？');
  await drawer.getByRole('button', { name: '提问', exact: true }).click();
  const citation = drawer.getByRole('button', { name: /^引用 S3/ }).first();
  await citation.click();
  const source = page.getByRole('dialog', { name: '来源', exact: true });
  await expect(source).toBeVisible();
  await screenshot(page, 'S05-source-over-assistant-1440x900-dark');
  await page.keyboard.press('Escape');
  await expect(source).toBeHidden();
  await expect(drawer).toBeVisible();
  await expect(citation).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(launcher).toBeFocused();
});

test('模拟软键盘与视口平移后，输入关闭发送仍在可视区域', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    const viewport = new EventTarget();
    Object.assign(viewport, {
      width: 390, height: 320, offsetTop: 180, offsetLeft: 0,
    });
    Object.defineProperty(window, 'visualViewport', {
      configurable: true, value: viewport,
    });
  });
  await page.reload();
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const drawer = drawerOf(page);
  await expect(drawer).toHaveAttribute('data-short', 'true');
  await drawer.getByLabel('追问内容').fill('键盘展开时继续研究');
  for (const control of [drawer.getByLabel('追问内容'),
    drawer.getByRole('button', { name: '收起 AI 助手', exact: true }),
    drawer.getByRole('button', { name: '提问', exact: true })]) {
    const rect = (await control.boundingBox())!;
    expect(rect.y).toBeGreaterThanOrEqual(180);
    expect(rect.y + rect.height).toBeLessThanOrEqual(500);
  }
  await screenshot(page, 'S07-keyboard-390x320-offset180-dark');
});

test('浅色、减少动画与打印共享既有主题，打印隐藏助手', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const launcher = page.getByRole('button', { name: '打开 AI 助手', exact: true });
  await expect(launcher).toBeVisible();
  await page.keyboard.press('t');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await launcher.click();
  const drawer = drawerOf(page);
  await expect(drawer).toBeVisible();
  await expect(drawer.getByLabel('追问内容')).toBeEditable();
  expect(await drawer.evaluate((node) => getComputedStyle(node).animationName))
    .toBe('none');
  await screenshot(page, 'S08-assistant-1440x900-light-reduced-motion');
  await page.emulateMedia({ media: 'print' });
  await expect(drawer).toBeHidden();
  await expect(page.locator('[data-assistant-launcher]')).toBeHidden();
});

test('首次打开前不请求历史或动态助手，打开不发送收费请求', async ({ page, context }) => {
  const sid = `ask-e2e-lazy-${test.info().testId.replace(/\W/g, '')}`;
  await context.addCookies([{ name: 'aragon_sid', value: sid,
    domain: '127.0.0.1', path: '/' }]);
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));
  await page.goto(SYMBOL);
  const launcher = page.getByRole('button', { name: '打开 AI 助手', exact: true });
  await expect(launcher).toBeVisible();
  expect(requests.filter((url) => /\/asks\?|AskPanel/.test(url))).toEqual([]);
  const history = page.waitForResponse((response) =>
    /\/reports\/[^/]+\/asks\?/.test(response.url()));
  await launcher.click();
  const response = await history;
  expect(new URL(response.url()).searchParams.get('scope')).toBe('mine');
  await expect(drawerOf(page).getByLabel('追问内容')).toBeEditable();
  expect(requests.filter((url) => /\/reports\/[^/]+\/ask$/.test(url)))
    .toEqual([]);
});

test('助手打开时切换未缓存版本，保持浮窗并清除旧草稿', async ({ page }) => {
  await page.route(/\/api\/v1\/reports\/[a-f0-9]{32}$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.continue();
  });
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const drawer = drawerOf(page);
  await drawer.getByLabel('追问内容').fill('只属于旧版本的草稿');
  // The command palette remains above the non-modal assistant.
  await page.keyboard.press('Control+k');
  const command = page.getByRole('dialog', {
    name: '搜索标的、切换版本、生成、导出…',
  });
  await command.getByRole('textbox').fill('v1');
  await command.getByRole('button', { name: /^v1\b/ }).first().click();
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(/报告 v1/)).toBeVisible();
  await expect(drawer.getByLabel('追问内容')).toHaveValue('');
  await expect(drawer).toHaveCount(1);
});

test('离线保留草稿，恢复网络不会自动发问题', async ({ page, context }) => {
  const posts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/reports\/[^/]+\/ask$/.test(request.url())) {
      posts.push(request.url());
    }
  });
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const drawer = drawerOf(page);
  await drawer.getByLabel('追问内容').fill('联网后继续研究');
  await context.setOffline(true);
  await expect(drawer.getByRole('button', { name: '提问', exact: true }))
    .toBeDisabled();
  await expect(drawer.getByText('离线时可阅读已有内容，联网后继续提问'))
    .toBeVisible();
  await context.setOffline(false);
  await expect(drawer.getByRole('button', { name: '提问', exact: true }))
    .toBeEnabled();
  await expect(drawer.getByLabel('追问内容')).toHaveValue('联网后继续研究');
  expect(posts).toEqual([]);
});

test('紧凑助手中的真实导出通知避让关闭与输入目标', async ({ page }) => {
  await page.setViewportSize({ width: 568, height: 320 });
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const drawer = drawerOf(page);
  await page.keyboard.press('Control+k');
  const command = page.getByRole('dialog', { name: '搜索标的、切换版本、生成、导出…' });
  await command.getByRole('textbox').fill('导出 HTML');
  await command.getByRole('button', { name: '导出 HTML', exact: true }).click();
  const notification = page.locator('[data-sonner-toast]').filter({ hasText: '正在下载 HTML' });
  await expect(notification).toBeVisible();
  await expect(drawer).toBeVisible();
  const toast = (await notification.boundingBox())!;
  const close = (await drawer.getByRole('button', {
    name: '收起 AI 助手', exact: true,
  }).boundingBox())!;
  const input = (await drawer.getByLabel('追问内容').boundingBox())!;
  expect(toast.x + toast.width).toBeLessThanOrEqual(close.x - 12);
  expect(toast.y + toast.height).toBeLessThanOrEqual(input.y);
  await screenshot(page, 'S08-toast-568x320-dark');
});

test('额度拒绝显示明确降级确认，接受后只发引用检索', async ({ page, context }) => {
  await context.addCookies([{ name: 'aragon_sid',
    value: 'ask-e2e-quota', domain: '127.0.0.1', path: '/' }]);
  await context.request.delete('http://127.0.0.1:8010/api/v1/test/assistant');
  const modes: string[] = [];
  await page.route(/\/api\/v1\/reports\/[^/]+\/ask$/, async (route) => {
    const body = route.request().postDataJSON();
    modes.push(body.mode);
    if (body.mode !== 'deep') return route.fallback();
    await route.fulfill({ status: 429, contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'quota_exceeded',
        message: '今日深度追问额度已用完', requestId: 'quota-e2e',
        detail: { suggestMode: 'cite', retryAfterMs: 60_000 },
      } }),
    });
  });
  await page.goto(SYMBOL);
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const drawer = drawerOf(page);
  await drawer.getByRole('radio', { name: '深度分析', exact: true }).click();
  await drawer.getByLabel('追问内容').fill('解释估值假设');
  await drawer.getByRole('button', { name: '深度追问', exact: true }).click();
  await expect(drawer.getByRole('alertdialog')).toBeVisible();
  expect(modes).toEqual(['deep']);
  await screenshot(page, 'S06-quota-confirmation-1440x900-dark');
  await drawer.getByRole('button', { name: '改用引用检索', exact: true }).click();
  await expect(drawer.getByRole('heading', { name: /命中 \d+ 处/ })).toBeVisible();
  expect(modes).toEqual(['deep', 'cite']);
});

test('活动生成与助手共享浮层，顶部进度恢复原任务且不重复创建', async ({ page }) => {
  const createdRuns: string[] = [];
  const cancelledRuns: string[] = [];
  const eventRuns: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'POST') return;
    if (/\/instruments\/[^/]+\/reports$/.test(path)) createdRuns.push(path);
    if (/\/runs\/[^/]+\/cancel$/.test(path)) cancelledRuns.push(path);
  });
  await page.route(/\/api\/v1\/runs\/[^/]+\/events(?:\?|$)/, async (route) => {
    const runId = new URL(route.request().url()).pathname.split('/')[4]!;
    eventRuns.push(runId);
    const payload = { runId, stage: 'research', pct: 45,
      label: 'AI 研究与撰写' };
    await route.fulfill({ status: 200, contentType: 'text/event-stream',
      headers: { 'access-control-allow-origin': 'http://127.0.0.1:3010' },
      body: `id: 1\nevent: run.stage\ndata: ${JSON.stringify(payload)}\n\n`,
    });
  });
  await page.getByRole('button', { name: '打开 AI 助手', exact: true }).click();
  const assistant = drawerOf(page);
  await expect(assistant.getByLabel('追问内容')).toBeEditable();
  await page.keyboard.press('Control+k');
  const command = page.getByRole('dialog', {
    name: '搜索标的、切换版本、生成、导出…',
  });
  await command.getByRole('textbox').fill('生成最新版本');
  const queued = page.waitForResponse((response) =>
    response.request().method() === 'POST'
    && /\/instruments\/[^/]+\/reports$/.test(new URL(response.url()).pathname));
  await command.getByRole('button', { name: '生成最新版本', exact: true }).click();
  const originalRunId = (await (await queued).json()).runId;
  expect(originalRunId).toBe('run-test');
  const badge = page.locator('[data-assistant-no-focus-return]');
  await expect(badge).toContainText('AI 研究与撰写');
  const overlay = page.locator('section[aria-label="正在生成投研报告"]');
  await expect(assistant).toBeVisible();
  await expect(overlay).toHaveCount(1);
  await expect(overlay).toBeHidden();
  await badge.click();
  await expect(assistant).toBeHidden();
  await expect(overlay).toBeVisible();
  await expect(overlay.getByRole('button', { name: '取消', exact: true })).toBeEnabled();
  await overlay.getByRole('button', { name: '收起', exact: true }).click();
  await overlay.getByRole('button', { name: '展开', exact: true }).click();
  await expect(overlay.getByRole('button', { name: '取消', exact: true })).toBeEnabled();
  await expect(badge).toContainText('45%');
  expect(createdRuns).toHaveLength(1);
  expect(cancelledRuns).toEqual([]);
  expect(eventRuns.length).toBeGreaterThan(0);
  expect(new Set(eventRuns)).toEqual(new Set([originalRunId]));
  await screenshot(page, 'S08-original-run-restored-1440x900-production');
});
