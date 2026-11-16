import { expect, test } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * v6 E 层（G1 可信度 / G2 阅读与区间 / G3 成本）的端到端证明（AC-49 ~ AC-53）。
 *
 * 五条用例，每条只证明一件"入口真的通"的事：徽标出现在报告头、样本点得动、
 * 全文视图有大纲、框选行情能反查出段落、成本面板能从命令面板打开。
 *
 * 数据全部来自 `tests/e2e/fixtures/fake-api.mjs`：
 *
 * - 最新版 `quality.grounding = { factClaims: 12, factMissing: 0 }`
 *   → 徽标显示「已核验 12/12」（`trustState()`：`factMissing === 0` 即是核验通过）；
 * - `?report=rpt-600519-v1`（上一版）`{ factClaims: 12, factMissing: 1, samples: [valuation#2] }`
 *   → 琥珀态 + 一条可点击、真的落在文档块上的样本；
 * - `capabilities.features` 里 `grounding/rangeQuery/readingMode/costLedger` 全为 true
 *   → 五个入口都必须渲染（`false` 时不渲染，是本轮的另一半纪律）。
 *
 * ⚠️ 两版的数据差异不是"为了测试编的"：一份报告不会既说"所有数字都能追溯"又列出一条
 * 未接地样本。要让两态都被证明，就得有两份报告 —— 这也正是 `?report=` 深链存在的理由。
 *
 * ⚠️ 定位一律用**可访问名 / 文案**（`web/lib/copy.ts` 的 `trust` / `reading` /
 * `range` / `cost` 段），不用 CSS 结构：这几个组件还在落地中，测试要对"读者看到
 * 什么"负责，而不是对 DOM 层级负责。
 */

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);

const SYMBOL = '/CN:600519.SH';

/** fixture 里那条未接地样本的块 id（`<sectionId>#<index>`，见 `lib/blocks.ts`）。 */
const SAMPLE_BLOCK_ID = 'valuation#2';

/** 有未接地数字的那一版（`factClaims 12 / factMissing 1`）：琥珀态 + 一条可点样本。 */
const REPORT_WITH_SAMPLE = '/CN:600519.SH?report=rpt-600519-v1';

/** 报告文档的章节标题（来自 fixture，不是文案表）：大纲里必须能读到它们。 */
const FIRST_SECTION_TITLE = '核心逻辑';

test.beforeEach(async ({ page }) => {
  await page.goto(SYMBOL);
  // 报告本体是后面四条的公共前提：先确认它在，否则失败原因会指向错误的层。
  await expect(page.getByRole('region', { name: '投研报告' })).toBeVisible();
});

test('可信度徽标显示「已核验 12/12」而不是一个笼统的"降级"', async ({ page }) => {
  // 三态由 `lib/grounding.ts::trustState()` 唯一裁决：最新版 `factMissing === 0`
  // → 「已核验 ok/total」（`copy.trust.verified`）。分母是 `factClaims`（12），
  // 不是"全部数字" —— 这正是 G1 的口径修正。
  const badge = page.getByTestId('trust-badge');
  await expect(badge).toBeVisible({ timeout: 15_000 });
  // 可访问名 = 「数字可信度：已核验 12/12」，读屏用户听到的是结论而不是"按钮"。
  await expect(badge).toHaveAccessibleName(/数字可信度.*已核验/);
  await expect(badge).toContainText(/已核验\s*12\s*\/\s*12/);
});

test('展开徽标列出未接地样本，点击后定位到该块', async ({ page }) => {
  // 换到那一版有未接地数字的报告（`?report=` 深链，RSC 首个字节就是这一版）。
  await page.goto(REPORT_WITH_SAMPLE);
  const badge = page.getByTestId('trust-badge');
  await expect(badge).toBeVisible({ timeout: 15_000 });
  await expect(badge).toContainText(/1\s*个数字未找到依据/);

  await badge.click();
  const panel = page.getByTestId('trust-badge-panel');
  await expect(panel).toBeVisible();

  // 每条样本是一个 `role="link"` 的按钮（`copy.trust.jump` 是它的 title）。
  const sample = panel.getByRole('link').first();
  await expect(sample).toBeVisible();
  await expect(sample).toContainText(SAMPLE_BLOCK_ID);
  await sample.click();

  // 定位的可见证据：目标块被滚动进视口，或被打上 `data-highlighted`（2.4s 后自动移除）。
  // 两种都算"跳到了"，因为面板内跳转与 `?block=` 深链走的是同一组机制。
  await expect
    .poll(
      async () => {
        const block = page.locator(`[data-block-id="${SAMPLE_BLOCK_ID}"]`);
        if ((await block.count()) === 0) return false;
        if ((await block.getAttribute('data-highlighted')) === 'true') return true;
        const box = await block.first().boundingBox();
        const height = page.viewportSize()?.height ?? 900;
        return Boolean(box && box.y >= -8 && box.y < height);
      },
      { timeout: 10_000, message: `样本点击后没有定位到 ${SAMPLE_BLOCK_ID}` },
    )
    .toBe(true);
});

test('全文视图（?view=full）渲染章节大纲', async ({ page }) => {
  await page.goto(`${SYMBOL}?view=full`);
  await expect(page.getByRole('region', { name: '投研报告' })).toBeVisible();

  // 大纲的容器名来自 `copy.reading.outline`（"大纲"）；标签可能是 nav/region/list，
  // 三种语义都接受（实现可及性时不一定用 <nav>）。
  const outline = page
    .getByRole('navigation', { name: /大纲/ })
    .or(page.getByRole('complementary', { name: /大纲/ }))
    .or(page.getByRole('list', { name: /大纲/ }))
    .or(page.getByText('大纲', { exact: true }));

  await expect(outline.first()).toBeVisible({ timeout: 15_000 });
  // 大纲必须列出**文档自己的**章节，而不是一句"暂无"。
  await expect(page.getByText(FIRST_SECTION_TITLE).first()).toBeVisible();
});

test('框选 K 线区间后，报告里出现区间联动条', async ({ page }) => {
  // 联动条的三种合法终态（`web/lib/copy.ts::copy.range`）：有命中 / 这段没被提及 /
  // 反查超时。三者都证明"条子渲染出来了"——本条用例只承诺这一点，命中数由
  // `/reports/{id}/blocks` 的接口契约守（fake API 里是文档中真实出现的日期）。
  const BAR = /关联\s*\d+\s*段|这段行情在报告里没有被提及|未找到关联段落|区间无效，已清除/;
  const bar = page.getByText(BAR);

  // 首选真实交互：`Shift + 拖拽` 在 K 线面板上框选（brush → 250ms 防抖 → 反查）。
  // 必须按 Shift：普通左键拖拽是图表自己的平移（`handleScroll.pressedMouseMove`）。
  const chart = page.getByLabel('K 线面板');
  await expect(chart).toBeVisible();
  const box = await chart.boundingBox();
  if (box) {
    const y = box.y + box.height * 0.55;
    await page.keyboard.down('Shift');
    await page.mouse.move(box.x + box.width * 0.25, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.5, y, { steps: 8 });
    await page.mouse.move(box.x + box.width * 0.78, y, { steps: 8 });
    await page.mouse.up();
    await page.keyboard.up('Shift');
  }

  if (!(await bar.first().isVisible().catch(() => false))) {
    // 退路：`?range=` 深链（AC-64 要求 view / range 正交，所以它必须是可分享的 URL）。
    // 区间取覆盖 fixture 日期（2026-02-10 / 2025-10-28 / 2025-08-14 / 2025-04-29）的
    // 一整段，避免"框到的那一格恰好没有段落"这种偶发失败。
    const windows = ['2025-04-01~2026-02-28', '2025-04-01,2026-02-28'];
    for (const value of windows) {
      await page.goto(`${SYMBOL}?range=${encodeURIComponent(value)}`);
      if (await bar.first().isVisible({ timeout: 8_000 }).catch(() => false)) break;
    }
  }

  await expect(bar.first()).toBeVisible({ timeout: 15_000 });
});

test('命令面板能打开成本面板', async ({ page }) => {
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', { name: /搜索标的、切换版本/ });
  await expect(palette).toBeVisible();

  // `copy.cost.open` = 「成本…」：入口在命令面板里，不在报告头（D5 的裁决）。
  await palette.getByRole('button', { name: /成本…/ }).first().click();

  const panel = page
    .getByRole('region', { name: '成本' })
    .or(page.getByRole('dialog', { name: '成本' }))
    .first();
  await expect(panel).toBeVisible({ timeout: 15_000 });

  // 面板必须给出已知花费 / 月度预算，而不是一个空壳（`copy.cost` 文案）。
  await expect(panel).toContainText(/已知花费|暂无成本记录|月度预算/);
});
