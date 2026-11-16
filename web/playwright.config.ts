import { defineConfig, devices } from '@playwright/test';

import { browserAvailable, required } from './tests/e2e/fixtures/browser';

/**
 * E2E 完全脱钩 Python（spec §19.3.7 / D7 / AC-59）。
 *
 * 以前 `webServer` 拉的是 `../scripts/dev.mjs`，也就是"真实 API + SQLite + 可能的
 * aragon 子进程"。代价：e2e 需要 Python 环境、需要跑迁移、需要种子数据，还要看
 * `AGENT_MODE=mock` 的脸色。于是最该被端到端证明的东西，恰恰是最跑不起来的东西。
 *
 * 现在起**两个**进程：
 *
 *   1. `fake-api.mjs` —— 零依赖 `node:http`，只实现 e2e 用到的端点，数据来自
 *      `api/tests/fixtures/sample_report.json`（单一真源），确定性；
 *   2. `next dev --port 3010` —— 前端，`NEXT_PUBLIC_API_BASE` 指向 fake API。
 *
 * 结论：**没有 Python、没有 aragon、没有数据库、没有网络**也能跑完整套件。
 *
 * ── 浏览器缺失时的语义（重要） ─────────────────────────────────────────────
 *
 * 没有 chromium 时 `npm run test:e2e` 输出 N skipped + 一行安装命令，**退出码 0**
 * ——这是"环境缺失"，不是"代码坏了"。四个既有 spec 与新 `grounding.spec.ts`
 * 都在顶部 `test.skip(!browserAvailable, SKIP_REASON)`（见 `tests/e2e/fixtures/browser.ts`）。
 *
 * CI 里不接受静默跳过，因此设 `PW_REQUIRED=1`：同名 helper 会让缺浏览器变成**失败**
 * 而不是 skip。即：
 *
 *     PW_REQUIRED=1 npm run test:e2e        # CI：没装浏览器 → 红
 *     npm run test:e2e                      # 本地：没装浏览器 → 全 skip，退出码 0
 */

const WEB_PORT = 3010;
const FAKE_API_PORT = Number(process.env.FAKE_API_PORT ?? 8010);
const FAKE_API_BASE = `http://127.0.0.1:${FAKE_API_PORT}`;
const WEB_BASE = `http://127.0.0.1:${WEB_PORT}`;

/**
 * 生产构建语义（offline.spec.ts 需要它：service worker 只在 production 注册）。
 *
 * `npm --prefix web run build` 之后用 `E2E_PRODUCTION=1` 跑，`next start` 仍然在 3010。
 */
const PRODUCTION = process.env.E2E_PRODUCTION === '1';

// 没有浏览器时把"该做什么"直接打在输出最前面 —— AC-59 要求的"一行安装命令"。
// （`list` reporter 不打印跳过原因，只有 HTML 报告里有；这一行是给终端里的读者看的。）
if (!browserAvailable) {
  const prefix = required() ? '✗ PW_REQUIRED=1，但' : '· 未安装 chromium，e2e 将全部跳过 →';
  console.warn(`${prefix} npx playwright install chromium`);
}

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    // 3010 而不是 3000：开发者的 `npm run dev` 常年占着 3000，复用它等于让 e2e
    // 跑在一个连真实 API 的服务上（症状：明明改了 fake，断言却按老数据通过）。
    baseURL: process.env.E2E_BASE_URL ?? WEB_BASE,
    trace: 'retain-on-failure',
    video: 'off',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  /**
   * 没有 chromium 时**不拉起任何服务**：18 条用例全部 skip，退出码 0。
   *
   * 否则 Playwright 仍会先起 webServer —— 也就是为了"跑 0 条用例"去编译一次 Next，
   * 白等 60 秒。跳过是环境缺失的表达，不该有任何副作用。
   */
  webServer: browserAvailable
    ? [
        {
          command: 'node tests/e2e/fixtures/fake-api.mjs',
          url: `${FAKE_API_BASE}/api/v1/capabilities`,
          reuseExistingServer: !process.env.CI,
          timeout: 30_000,
          env: { FAKE_API_PORT: String(FAKE_API_PORT) },
        },
        {
          command: PRODUCTION ? `npx next start --port ${WEB_PORT}` : `npx next dev --port ${WEB_PORT}`,
          url: WEB_BASE,
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
          env: {
            // 浏览器端（`NEXT_PUBLIC_*` 会被内联进 bundle）与服务端的唯一 API 地址。
            NEXT_PUBLIC_API_BASE: FAKE_API_BASE,
            INTERNAL_API_BASE: FAKE_API_BASE,
            AGENT_MODE: 'mock',
          },
        },
      ]
    : [],
});
