import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { cpus, platform, arch } from 'node:os';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import {
  SKIP_REASON, browserAvailable, failIfBrowserRequired,
} from './fixtures/browser';

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

const SYMBOL = '/CN:600519.SH';
const API = 'http://127.0.0.1:8010/api/v1';
const BAR_ROUTE = '**/api/v1/instruments/*/bars?*';
const ACCEPTANCE = resolve(process.cwd(),
  '../docs/plans/professional-kline-workbench');
const SHOTS = resolve(ACCEPTANCE, 'screenshots');
test.beforeAll(async () => { await mkdir(SHOTS, { recursive: true }); });

const panel = (page: Page) => page.getByRole('region', {
  name: 'K 线面板', exact: true,
});
const graph = (page: Page) => page.getByRole('region', {
  name: 'K 线图形', exact: true,
});

async function settleChart(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      requestAnimationFrame(() => resolve());
    }));
  }));
}

async function openChart(page: Page) {
  await page.goto(SYMBOL, { waitUntil: 'domcontentloaded' });
  await expect(panel(page)).toBeVisible();
  await expect(graph(page)).toBeVisible();
  await expect(page.locator('.chart-pane-main canvas').first()).toBeVisible();
  await settleChart(page);
}

async function more(page: Page) {
  const details = panel(page).locator('details.chart-toolbar-more');
  if (await details.locator('summary').isVisible()
    && !(await details.evaluate(node => (
    (node as HTMLDetailsElement).open
  )))) await details.locator('summary').click();
}

async function locate(page: Page, date: string) {
  await more(page);
  await panel(page).getByLabel('定位日期').fill(date);
  await panel(page).getByRole('button', { name: '定位', exact: true }).click();
  await expect(panel(page).getByText('键盘检视', { exact: true })).toBeVisible();
}

async function visibleSpan(page: Page) {
  await graph(page).focus();
  await settleChart(page);
  const guide = page.locator('.chart-pane-main .chart-shared-crosshair');
  await expect(guide).toBeVisible();
  const before = await guide.evaluate(node => parseFloat(
    (node as HTMLElement).style.left,
  ));
  await page.keyboard.press('ArrowLeft');
  await expect.poll(async () => guide.evaluate(node => parseFloat(
    (node as HTMLElement).style.left,
  ))).not.toBe(before);
  const after = await guide.evaluate(node => parseFloat(
    (node as HTMLElement).style.left,
  ));
  await page.keyboard.press('ArrowRight');
  await expect.poll(async () => Math.abs(before - await guide.evaluate(
    node => parseFloat((node as HTMLElement).style.left),
  ))).toBeLessThan(0.1);
  const plotWidth = await page.locator(
    '.chart-pane-main .chart-pane-canvas table tr',
  ).first().locator('td').nth(1).evaluate(node => (
    node.getBoundingClientRect().width
  ));
  const date = await panel(page).locator('.chart-readout-heading .num')
    .textContent();
  const days = (Date.parse(`${date}T00:00:00Z`)
    - Date.UTC(1970, 0, 5)) / 86400000;
  const ordinal = Math.floor(days / 7) * 5 + Math.min(days % 7, 5);
  return { span: plotWidth / Math.abs(before - after),
    left: ordinal - before / Math.abs(before - after) };
}

test('chart profile秒时间戳、十九指标及真实周月聚合', async ({ request }) => {
  const response = await request.get(`${API}/instruments/CN:600519.SH/bars`
    + '?timeframe=1d&range=1y&adjust=qfq&profile=chart');
  expect(response.ok()).toBe(true);
  const daily = await response.json();
  expect(daily.bars.at(-1).ts).toBeLessThan(100_000_000_000);
  expect(daily.chart.version).toBe('chart/v1');
  expect(Object.keys(daily.indicators)).toHaveLength(19);
  const mean = (count: number) => daily.bars.slice(0, count)
    .reduce((sum: number, bar: { c: number }) => sum + bar.c, 0) / count;
  expect(daily.indicators.ma5[4]).toBeCloseTo(mean(5), 10);
  expect(daily.indicators.ema12[11]).toBeCloseTo(mean(12), 10);
  expect(daily.indicators.ema26[25]).toBeCloseTo(mean(26), 10);
  expect(daily.indicators.macdSignal.slice(0, 33))
    .toEqual(Array(33).fill(null));
  expect(daily.indicators.macdHistogram[33]).toBeCloseTo(
    2 * (daily.indicators.macd[33] - daily.indicators.macdSignal[33]), 12,
  );
  for (const timeframe of ['1w', '1m']) {
    const grouped = await (await request.get(
      `${API}/instruments/CN:600519.SH/bars?timeframe=${timeframe}`
      + '&range=1y&adjust=qfq&profile=chart',
    )).json();
    expect(grouped.bars.length).toBeLessThan(daily.bars.length);
    expect(grouped.bars.at(-1).c).toBe(daily.bars.at(-1).c);
    expect(grouped.chart.stats.at(-1).periodStart
      < grouped.chart.stats.at(-1).periodEnd).toBe(true);
    expect(grouped.chart.calculationBarsCount)
      .toBe(grouped.chart.warmupBarsCount + grouped.bars.length);
    const stat = grouped.chart.stats.at(-1);
    const children = daily.bars.filter((bar: { ts: number }) => {
      const date = new Date(bar.ts * 1000).toISOString().slice(0, 10);
      return date >= stat.periodStart && date <= stat.periodEnd;
    });
    expect(grouped.bars.at(-1).o).toBe(children[0].o);
    expect(grouped.bars.at(-1).h).toBe(Math.max(...children.map(
      (bar: { h: number }) => bar.h,
    )));
    expect(grouped.bars.at(-1).v).toBe(children.reduce(
      (sum: number, bar: { v: number }) => sum + bar.v, 0,
    ));
  }
});

test('报告日期focus只消费一次，刷新和扩展不重放', async ({ page }) => {
  await openChart(page);
  const date = page.locator('button[title="在 K 线中定位 2026-02-10"]').first();
  await expect(date).toBeVisible();
  await date.click();
  await locate(page, '2026-02-05');
  const before = await visibleSpan(page);
  await more(page);
  await panel(page).getByRole('button', { name: '扩展', exact: true }).click();
  const expanded = await visibleSpan(page);
  expect(Math.abs(expanded.span - before.span)).toBeLessThan(0.3);
  expect(Math.abs(expanded.left - before.left)).toBeLessThan(0.3);
  await panel(page).getByRole('button', { name: '收起扩展' }).click();
  await more(page);
  await panel(page).getByRole('button', { name: '刷新行情' }).click();
  await expect(panel(page).getByRole('button', { name: '刷新中…' })).toHaveCount(0);
  const refreshed = await visibleSpan(page);
  // Pixel-derived spans include native fractional edge spacing after resize.
  expect(Math.abs(refreshed.span - before.span)).toBeLessThan(2);
  await date.click();
  const focused = await visibleSpan(page);
  expect(focused.span).toBeLessThan(before.span - 5);
});

test('跨窗hover不闪回，测量手势不启动平移', async ({ page }) => {
  await openChart(page);
  await locate(page, '2025-11-03');
  const moreMenu = panel(page).locator('details.chart-toolbar-more');
  if (await moreMenu.locator('summary').isVisible()) {
    await moreMenu.locator('summary').click();
  }
  const main = page.locator('.chart-pane-main .chart-pane-canvas');
  const volume = page.locator('.chart-pane-volume .chart-pane-canvas');
  await page.locator('.chart-scroll-body').evaluate(node => {
    const readout = node.querySelector('.chart-readout') as HTMLElement;
    node.scrollTop = Math.max(0, readout.clientHeight - 80);
  });
  const a = (await main.boundingBox())!;
  const b = (await volume.boundingBox())!;
  const readout = (await panel(page).locator('.chart-readout').boundingBox())!;
  const x = a.x + (a.width - 72) * 0.5;
  await page.mouse.move(x, Math.max(a.y + 30, readout.y + readout.height + 15));
  const date = await panel(page).locator('.chart-readout-heading .num')
    .textContent();
  await page.mouse.move(x, b.y + 45);
  await expect(panel(page).locator('.chart-readout-heading .num'))
    .toHaveText(date!);
  await more(page);
  await panel(page).getByRole('button', { name: '扩展', exact: true }).click();
  await panel(page).getByRole('button', { name: '测量', exact: true }).click();
  const before = page.url();
  await page.locator('.chart-scroll-body').evaluate(node => { node.scrollTop = 0; });
  const bounds = (await main.boundingBox())!;
  await page.mouse.move(bounds.x + 80, bounds.y + 70);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 160, bounds.y + 70, { steps: 8 });
  await page.mouse.up();
  await expect(panel(page).locator('.chart-measurement')).toBeVisible();
  expect(page.url()).toBe(before);
});

test('浅色、低高度和200%缩放的图表入口可用', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('aragon.theme.v1', 'light');
  });
  await openChart(page);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.screenshot({ path: resolve(SHOTS, 'chart-light.png') });
  await page.setViewportSize({ width: 1440, height: 450 });
  await page.screenshot({ path: resolve(SHOTS, 'chart-low-height.png') });
  // Reproduce 200% zoom geometry: half CSS viewport, double device scale.
  await page.setViewportSize({ width: 720, height: 450 });
  const zoom = await page.context().newCDPSession(page);
  await zoom.send('Emulation.setDeviceMetricsOverride', {
    width: 720, height: 450, deviceScaleFactor: 2, mobile: false,
  });
  await page.getByRole('button', { name: '打开 K 线' }).click();
  await expect(graph(page)).toBeVisible();
  expect(await page.evaluate(() => (
    document.documentElement.scrollWidth <= window.innerWidth
  ))).toBe(true);
  await more(page);
  await panel(page).getByRole('button', { name: '数据表', exact: true }).click();
  await expect(panel(page).locator('.chart-data-table table')).toBeVisible();
  await page.screenshot({ path: resolve(SHOTS, 'chart-200-percent.png') });
});

test('真实canvas、副图、指标设置、键盘及跨窗竖线对齐', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await openChart(page);
  await expect(page.locator('[data-chart-pane]:visible')).toHaveCount(3);
  await expect(panel(page).getByText('最新周期', { exact: true })).toBeVisible();
  await graph(page).focus();
  await page.keyboard.press('ArrowLeft');
  await expect(panel(page).getByText('键盘检视', { exact: true })).toBeVisible();
  await expect.poll(async () => page.locator(
    '.chart-shared-crosshair:visible',
  ).evaluateAll(nodes => {
    const positions = nodes.map(node => node.getBoundingClientRect().x);
    return Math.max(...positions) - Math.min(...positions);
  })).toBeLessThanOrEqual(1);
  await expect(page.locator('.chart-shared-crosshair:visible')).toHaveCount(3);
  await panel(page).getByRole('button', { name: '指标', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '图表指标设置' });
  await settings.getByRole('checkbox', { name: /EMA12/ }).check();
  await settings.getByRole('checkbox', { name: /BOLL/ }).check();
  await settings.getByRole('radio', { name: /KDJ/ }).check();
  await settings.getByRole('button', { name: '关闭设置' }).click();
  await expect(page.locator('.chart-pane-main')).toContainText('BOLL MID');
  await expect(page.locator('.chart-pane-oscillator')).toContainText('K ');
  await expect(page.locator('.chart-pane-main')).toContainText('EMA26');
  await page.screenshot({ path: resolve(SHOTS, 'chart-desktop.png') });
  expect(errors).toEqual([]);
});

test('扩展保留历史视区，跨断点保持唯一图表且无额外请求', async ({ page }) => {
  console.log('chart:e2e 开始扩展视区验证');
  let requests = 0;
  page.on('request', request => {
    if (request.url().includes('/bars?')) requests += 1;
  });
  await openChart(page);
  await locate(page, '2025-11-03');
  console.log('chart:e2e 日期定位完成');
  const initial = await visibleSpan(page);
  console.log('chart:e2e 初始视区', initial);
  await more(page);
  await panel(page).getByRole('button', { name: '扩展', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '扩展 K 线工作台' })).toBeVisible();
  await expect(page.locator('.chart-group')).toHaveCount(1);
  const expanded = await visibleSpan(page);
  console.log('chart:e2e 扩展视区', expanded);
  expect(Math.abs(expanded.span - initial.span)).toBeLessThan(0.3);
  expect(Math.abs(expanded.left - initial.left)).toBeLessThan(0.3);
  await page.setViewportSize({ width: 1000, height: 800 });
  await expect(page.getByRole('dialog', { name: '扩展 K 线工作台' })).toBeVisible();
  await expect(page.locator('.chart-group')).toHaveCount(1);
  await page.screenshot({ path: resolve(SHOTS, 'chart-expanded.png') });
  await page.keyboard.press('Escape');
  // The first Escape releases fixed keyboard inspection.
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '扩展 K 线工作台' })).toBeHidden();
  await expect(page.getByRole('button', { name: /K 线|行情/ }).first())
    .toBeVisible();
  expect(requests).toBe(1);
});

test('测量不改URL，CSV及PNG从当前同源数据下载', async ({ page }) => {
  await openChart(page);
  await more(page);
  const before = page.url();
  await panel(page).getByRole('button', { name: '测量', exact: true }).click();
  await panel(page).getByLabel('测量起始日期').fill('2025-11-03');
  await panel(page).getByLabel('测量终止日期').fill('2025-11-10');
  await panel(page).getByRole('button', { name: '计算测量' }).click();
  await expect(panel(page).getByText(/首末收盘收益/)).toBeVisible();
  expect(page.url()).toBe(before);
  const csvEvent = page.waitForEvent('download');
  await panel(page).getByRole('button', { name: '下载 CSV' }).click();
  const csv = await csvEvent;
  const contents = await readFile((await csv.path())!, 'utf8');
  expect(contents.startsWith('\uFEFF')).toBe(true);
  expect(contents).toContain('macdHistogram');
  expect(contents).toContain('warmupBarsCount');
  expect(contents.split('\r\n').length).toBeGreaterThan(100);
  const pngEvent = page.waitForEvent('download');
  await panel(page).getByRole('button', { name: '下载 PNG' }).click();
  const png = await pngEvent;
  const image = await readFile((await png.path())!);
  expect(image.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
  expect(png.suggestedFilename()).toMatch(/_1d_qfq_.*\.png$/);
  await page.screenshot({ path: resolve(SHOTS, 'chart-measurement.png') });
});

test('二十次扩展切换不残留canvas或订阅异常', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await openChart(page);
  const initialCanvases = await panel(page).locator('canvas').count();
  for (let index = 0; index < 20; index += 1) {
    await more(page);
    await panel(page).getByRole('button', { name: '扩展', exact: true }).click();
    await expect(page.locator('.chart-group')).toHaveCount(1);
    await panel(page).getByRole('button', { name: '收起扩展' }).click();
    await expect(page.getByRole('dialog', { name: '扩展 K 线工作台' })).toBeHidden();
    await expect(panel(page).locator('canvas')).toHaveCount(initialCanvases);
  }
  expect(errors).toEqual([]);
});

test('同参数失败刷新保留有效图形与旧价格', async ({ page }) => {
  await openChart(page);
  const price = await page.locator('.chart-latest-price strong').textContent();
  await page.route(BAR_ROUTE, route => route.fulfill({
    status: 503, contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'data_provider_unavailable',
      message: 'fixture refresh unavailable', requestId: 'chart-e2e' } }),
  }));
  await more(page);
  await panel(page).getByRole('button', { name: '刷新行情' }).click();
  await expect(panel(page).getByText(/刷新失败，保留旧数据/)).toBeVisible();
  await expect(page.locator('.chart-latest-price strong')).toHaveText(price!);
  await expect(graph(page)).toBeVisible();
});

test('空包、旧服务与未知版本分别显示明确状态', async ({ page }) => {
  let mode: 'empty' | 'legacy' | 'invalid' = 'empty';
  await page.route(BAR_ROUTE, async route => {
    const response = await route.fetch();
    const body = await response.json();
    if (mode === 'empty') {
      body.bars = [];
      body.chart.stats = [];
      body.chart.warmupBarsCount = body.chart.calculationBarsCount;
      for (const key of Object.keys(body.indicators)) body.indicators[key] = [];
      Object.assign(body.meta, { barsCount: 0, fromTs: null, toTs: null });
    } else if (mode === 'legacy') delete body.chart;
    else body.chart.version = 'chart/unknown';
    await route.fulfill({ response, json: body });
  });
  await page.goto(SYMBOL);
  await expect(panel(page).getByText(/暂无行情/)).toBeVisible();
  await expect(page.locator('.chart-group')).toHaveCount(0);
  mode = 'legacy';
  await page.reload();
  await expect(panel(page).getByText(/当前服务未支持完整图表数据/)).toBeVisible();
  await expect(page.getByLabel('简化 K 线图')).toBeVisible();
  mode = 'invalid';
  await page.reload();
  await expect(panel(page).getByText(/图表数据契约错误/)).toBeVisible();
  await expect(page.locator('.chart-group')).toHaveCount(0);
});

test('移动触摸点击固定读数，拖动和双指不误固定', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  });
  const page = await context.newPage();
  await page.goto(`http://127.0.0.1:3010${SYMBOL}`);
  await page.getByRole('button', { name: '打开 K 线', exact: true }).click();
  await expect(graph(page)).toBeVisible();
  const host = page.locator('.chart-pane-main .chart-pane-canvas');
  await page.locator('.chart-scroll-body').evaluate(node => { node.scrollTop = 0; });
  const bounds = (await host.boundingBox())!;
  const x = bounds.x + (bounds.width - 72) * 0.6;
  const y = bounds.y + 70;
  await page.touchscreen.tap(x, y);
  await expect(panel(page).getByText('固定检视', { exact: true })).toBeVisible();
  await panel(page).getByRole('button', { name: '返回最新' }).click();
  const session = await context.newCDPSession(page);
  await session.send('Input.dispatchTouchEvent', { type: 'touchStart',
    touchPoints: [{ x, y }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchMove',
    touchPoints: [{ x: x + 35, y }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchEnd',
    touchPoints: [] });
  await expect(panel(page).getByText('固定检视', { exact: true })).toHaveCount(0);
  await session.send('Input.dispatchTouchEvent', { type: 'touchStart',
    touchPoints: [{ x, y, id: 1 }, { x: x + 30, y, id: 2 }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchEnd',
    touchPoints: [] });
  await expect(panel(page).getByText('固定检视', { exact: true })).toHaveCount(0);
  const overflow = await page.evaluate(() => (
    document.documentElement.scrollWidth > window.innerWidth
  ));
  expect(overflow).toBe(false);
  await page.screenshot({ path: resolve(SHOTS, 'chart-mobile.png') });
  await context.close();
});

test('Shift及显式报告框选更新range，取消和测量恢复平移', async ({ page }) => {
  const navigation: string[] = [];
  page.on('request', request => {
    if (request.url().includes('_rsc')) navigation.push(`request ${request.url()}`);
  });
  page.on('response', response => {
    if (response.url().includes('_rsc')) navigation.push(`response ${response.status()} ${response.url()}`);
  });
  page.on('pageerror', error => navigation.push(`pageerror ${error.message}`));
  await openChart(page);
  await more(page);
  await panel(page).getByRole('button', { name: '扩展', exact: true }).click();
  await expect(panel(page).getByRole('button', {
    name: '报告框选', exact: true,
  })).toBeEnabled();
  await settleChart(page);
  const main = page.locator('.chart-pane-main .chart-pane-canvas');
  await page.locator('.chart-scroll-body').evaluate(node => { node.scrollTop = 0; });
  const bounds = (await main.boundingBox())!;
  const y = bounds.y + 60;
  await page.keyboard.down('Shift');
  await page.mouse.move(bounds.x + 70, y);
  await settleChart(page);
  const hit = await page.evaluate(({ x, y }) => {
    const node = document.elementFromPoint(x, y);
    return { target: node?.className,
      isCanvas: Boolean(node?.closest('.chart-pane-canvas')) };
  }, { x: bounds.x + 70, y });
  const afterHover = (await main.boundingBox())!;
  console.log('chart gesture hit', JSON.stringify({ bounds,
    after: afterHover,
    readout: await panel(page).locator('.chart-readout').boundingBox(), hit }));
  expect(hit.isCanvas).toBe(true);
  expect(Math.abs(afterHover.y - bounds.y)).toBeLessThanOrEqual(1);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 150, y, { steps: 6 });
  await page.mouse.up();
  await page.keyboard.up('Shift');
  await expect(page).toHaveURL(url => /^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$/.test(
    url.searchParams.get('range') ?? '',
  ), { timeout: 3000 }).catch(async error => {
    const cached = page.url();
    const actual = await page.evaluate(() => ({
      href: location.href, focused: document.hasFocus(),
      visible: document.visibilityState, now: performance.now(),
    }));
    console.log('route failure', JSON.stringify({ cached, actual, navigation,
      afterEvaluate: page.url(), footer: await panel(page)
        .locator('.chart-notice').allTextContents() }));
    throw error;
  });
  const firstRange = new URL(await page.evaluate(() => location.href))
    .searchParams.get('range');
  await more(page);
  await panel(page).getByRole('button', { name: '报告框选', exact: true }).click();
  await page.locator('.chart-scroll-body').evaluate(node => { node.scrollTop = 0; });
  const next = (await main.boundingBox())!;
  await page.mouse.move(next.x + 190, next.y + 60);
  await page.mouse.down();
  await page.mouse.move(next.x + 100, next.y + 60, { steps: 6 });
  await page.mouse.up();
  await expect(page).toHaveURL(url => url.searchParams.get('range') !== firstRange);
  const range = new URL(await page.evaluate(() => location.href))
    .searchParams.get('range');
  expect(range!.split('..')[0]! <= range!.split('..')[1]!).toBe(true);
  await page.mouse.move(next.x + 80, next.y + 60);
  await page.mouse.down();
  await main.dispatchEvent('pointercancel', { pointerId: 1, pointerType: 'mouse' });
  await page.mouse.up();
  await expect(page.locator('.chart-range-draft')).toHaveCount(0);
  expect(new URL(await page.evaluate(() => location.href))
    .searchParams.get('range')).toBe(range);
  await graph(page).focus();
  await page.keyboard.press('Escape');
  await more(page);
  await expect(panel(page).getByRole('button', { name: '平移', exact: true }))
    .toHaveAttribute('aria-pressed', 'true');
});

test('同key刷新插入撤回柱更新读数并清除旧测量', async ({ page }) => {
  await openChart(page);
  await locate(page, '2025-11-03');
  await more(page);
  await panel(page).getByRole('button', { name: '测量', exact: true }).click();
  await panel(page).getByLabel('测量起始日期').fill('2025-11-03');
  await panel(page).getByLabel('测量终止日期').fill('2025-11-10');
  await panel(page).getByRole('button', { name: '计算测量' }).click();
  await expect(panel(page).locator('.chart-measurement')).toBeVisible();
  let revision = 0;
  await page.route(BAR_ROUTE, async route => {
    revision += 1;
    const url = new URL(route.request().url());
    url.searchParams.set('chartFixture', revision === 1 ? 'inserted' : 'withdrawn');
    const response = await route.fetch({ url: url.toString() });
    await route.fulfill({ response });
  });
  const firstPacket = page.waitForResponse(response => response.url().includes('/bars?'));
  await panel(page).getByRole('button', { name: '刷新行情' }).click();
  const body = await (await firstPacket).json();
  await expect(panel(page).getByText('数据已更新，请重新测量')).toBeVisible();
  await expect(panel(page).locator('.chart-measurement')).toHaveCount(0);
  await expect(page.locator('.chart-latest-price strong')).toHaveText(
    body.bars.at(-1).c.toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }),
  );
  await expect(panel(page).locator('.chart-readout-heading .num'))
    .toHaveText('2025-11-03');
  const secondPacket = page.waitForResponse(response => response.url().includes('/bars?'));
  await panel(page).getByRole('button', { name: '刷新行情' }).click();
  const withdrawn = await (await secondPacket).json();
  expect(withdrawn.bars.length).toBeLessThan(body.bars.length);
  await expect(panel(page).locator('.chart-readout-heading .num'))
    .toHaveText('2025-11-03');
});

test('380px容器、yfinance后复权说明及短历史月线', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('aragon.layout.v1', JSON.stringify({
      railPx: 200, klineFraction: 380 / 1240,
    }));
  });
  let short = false;
  await page.route(BAR_ROUTE, async route => {
    const url = new URL(route.request().url());
    if (short) url.searchParams.set('chartFixture', 'short');
    const response = await route.fetch({ url: url.toString() });
    const body = await response.json();
    body.meta.source = 'yfinance';
    body.meta.stale = true;
    body.meta.reasonCode = 'divergent_source';
    body.chart.effectiveAdjust = 'auto';
    body.chart.volumeUnit = 'share';
    body.chart.volumeUnitSize = 1;
    await route.fulfill({ response, json: body });
  });
  await openChart(page);
  expect((await panel(page).boundingBox())!.width).toBeCloseTo(380, 0);
  await more(page);
  await panel(page).getByRole('button', { name: '后复权', exact: true }).click();
  await expect(panel(page).getByText(/非可验证后复权/)).toBeVisible();
  await page.screenshot({ path: resolve(SHOTS, 'chart-yfinance-hfq-380.png') });
  short = true;
  await panel(page).getByRole('button', { name: '月', exact: true }).click();
  await expect(panel(page).getByText(/历史不足（需要 60 根/)).toBeVisible();
  await expect(panel(page).getByText('末周期可能未结束').first()).toBeVisible();
  await page.screenshot({ path: resolve(SHOTS, 'chart-short-monthly.png') });
  await page.setViewportSize({ width: 568, height: 320 });
  await page.getByRole('button', { name: '打开 K 线', exact: true }).click();
  await expect(graph(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth
    <= innerWidth)).toBe(true);
  await page.screenshot({ path: resolve(SHOTS, 'chart-landscape-568x320.png') });
});

interface PerformanceProbe {
  renderStart: number;
  hoverStart: number;
  render: number[];
  hover: number[];
  recordHover: boolean;
}
type ProbeWindow = Window & { __chartPerf: PerformanceProbe };

async function installInputProbe(page: Page) {
  await page.addInitScript(() => {
    const root = window as ProbeWindow;
    root.__chartPerf = {
      renderStart: 0, hoverStart: 0, render: [], hover: [], recordHover: false,
    };
    const originalFetch = window.fetch;
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (response.url.includes('/bars?')) {
        root.__chartPerf.renderStart = performance.now();
      }
      return response;
    };
    document.addEventListener('pointermove', event => {
      const target = event.target as HTMLElement;
      if (root.__chartPerf.recordHover
        && target.closest('.chart-pane-canvas')) {
        root.__chartPerf.hoverStart = performance.now();
      }
    }, true);
  });
}

async function installMutationProbe(page: Page) {
  await page.addInitScript(() => {
    const probe = (window as ProbeWindow).__chartPerf;
    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        const target = mutation.target instanceof Element
          ? mutation.target : mutation.target.parentElement;
        if (probe.renderStart && mutation.attributeName === 'data-dataset-version'
          && target?.matches('.chart-group')) {
          const start = probe.renderStart;
          probe.renderStart = 0;
          requestAnimationFrame(() => requestAnimationFrame(() => {
            probe.render.push(performance.now() - start);
          }));
        }
        if (probe.recordHover && probe.hoverStart
          && target?.closest('.chart-readout')) {
          probe.hover.push(performance.now() - probe.hoverStart);
          probe.hoverStart = 0;
        }
      }
    });
    observer.observe(document, {
      childList: true, characterData: true, attributes: true, subtree: true,
    });
  });
}

async function installPerformanceFixture(page: Page) {
  let revision = 0;
  let counts = { calculation: 0, displayed: 0 };
  await page.route(BAR_ROUTE, async route => {
    const url = new URL(route.request().url());
    url.searchParams.set('chartFixture', 'performance');
    url.searchParams.set('range', '5y');
    const response = await route.fetch({ url: url.toString() });
    const body = await response.json();
    revision += 1;
    body.meta.cached = Boolean(revision % 2);
    counts = { calculation: body.chart.calculationBarsCount,
      displayed: body.bars.length };
    await route.fulfill({ response, json: body });
  });
  return () => counts;
}

async function collectRenderSamples(page: Page) {
  await more(page);
  await page.evaluate(() => { (window as ProbeWindow).__chartPerf.render = []; });
  for (let index = 0; index < 20; index += 1) {
    await panel(page).getByRole('button', { name: '刷新行情' }).click();
    await expect.poll(() => page.evaluate(() => (
      (window as ProbeWindow).__chartPerf.render.length
    ))).toBe(index + 1);
  }
}

async function collectHoverSamples(page: Page) {
  const menu = panel(page).locator('details.chart-toolbar-more');
  if (await menu.locator('summary').isVisible()) await menu.locator('summary').click();
  const host = page.locator('.chart-pane-main .chart-pane-canvas');
  await page.locator('.chart-scroll-body').evaluate(node => { node.scrollTop = 0; });
  const bounds = (await host.boundingBox())!;
  await page.evaluate(() => {
    const probe = (window as ProbeWindow).__chartPerf;
    probe.hover = []; probe.recordHover = true;
  });
  for (let index = 0; index < 20; index += 1) {
    await page.mouse.move(bounds.x + 20 + index * (bounds.width - 115) / 20,
      bounds.y + 60);
    await expect.poll(() => page.evaluate(() => (
      (window as ProbeWindow).__chartPerf.hover.length
    ))).toBe(index + 1);
  }
  await page.evaluate(() => {
    const probe = (window as ProbeWindow).__chartPerf;
    probe.recordHover = false; probe.hoverStart = 0;
  });
}

async function assertFitsLoadedWindow(page: Page, count: number) {
  await more(page);
  await panel(page).getByRole('button', { name: '适配', exact: true }).click();
  await graph(page).focus();
  const settle = () => page.evaluate(() => new Promise<void>(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      requestAnimationFrame(() => resolve());
    }));
  }));
  const guide = page.locator('.chart-pane-main .chart-shared-crosshair');
  const x = () => guide.evaluate(node => parseFloat(
    (node as HTMLElement).style.left,
  ));
  await page.keyboard.press('Home'); await settle();
  const firstX = await x();
  await page.keyboard.press('End'); await settle();
  const lastX = await x();
  await page.keyboard.press('ArrowLeft'); await settle();
  const adjacentSpacing = lastX - await x();
  const plotWidth = await page.locator(
    '.chart-pane-main .chart-pane-canvas table tr',
  ).first().locator('td').nth(1).evaluate(node => (
    node.getBoundingClientRect().width
  ));
  const inferredSpan = plotWidth / ((lastX - firstX) / (count - 1));
  const adjacentSpan = plotWidth / adjacentSpacing;
  expect(firstX).toBeGreaterThanOrEqual(-1);
  expect(lastX).toBeLessThanOrEqual(plotWidth + 1);
  expect(inferredSpan).toBeGreaterThanOrEqual(count);
  expect(adjacentSpan).toBeGreaterThanOrEqual(count - 1);
  return { plotWidth, firstX, lastX, adjacentSpacing, adjacentSpan, inferredSpan };
}

async function savePerformanceSamples(
  page: Page, counts: { calculation: number; displayed: number },
  fit: Awaited<ReturnType<typeof assertFitsLoadedWindow>>,
) {
  const samples = await page.evaluate(() => (window as ProbeWindow).__chartPerf);
  const p95 = (values: number[]) => [...values].sort((a, b) => a - b)[18]!;
  const environment = await page.evaluate(() => ({
    dpr: devicePixelRatio, userAgent: navigator.userAgent,
    viewport: { width: innerWidth, height: innerHeight },
  }));
  const result = { mode: process.env.E2E_PRODUCTION === '1' ? 'production' : 'dev',
    machine: { os: platform(), arch: arch(), cpu: cpus()[0]?.model },
    ...environment, counts, fit, renderMs: samples.render, hoverMs: samples.hover,
    renderP95: p95(samples.render), hoverP95: p95(samples.hover) };
  console.log('chart performance', JSON.stringify(result));
  await writeFile(resolve(ACCEPTANCE, `chart-performance-${result.mode}.json`),
    JSON.stringify(result, null, 2));
  expect(counts.calculation).toBe(2600);
  expect(counts.displayed).toBeGreaterThan(1300);
  expect(samples.render).toHaveLength(20);
  expect(samples.hover).toHaveLength(20);
  if (result.mode === 'production') {
    expect(result.renderP95).toBeLessThanOrEqual(150);
    expect(result.hoverP95).toBeLessThanOrEqual(32);
  }
}

test('2600根历史与五年窗口真实绘图及hover各20样本', async ({ page }) => {
  test.setTimeout(120_000);
  await installInputProbe(page);
  await installMutationProbe(page);
  await page.addInitScript(() => localStorage.setItem(
    'aragon.layout.v1', JSON.stringify({
      railPx: 200, klineFraction: 380 / 1240,
    }),
  ));
  const counts = await installPerformanceFixture(page);
  await openChart(page);
  await collectRenderSamples(page);
  await collectHoverSamples(page);
  const fit = await assertFitsLoadedWindow(page, counts().displayed);
  await savePerformanceSamples(page, counts(), fit);
  await page.screenshot({ path: resolve(SHOTS, 'chart-fit-1306.png') });
});
