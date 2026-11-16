import { defineConfig, devices } from '@playwright/test';

/**
 * E2E runs entirely in mock mode: no network, no model key, no flakiness.
 * `webServer` boots the API with AGENT_MODE=mock and the Next dev server.
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
    video: 'off',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: 'node ../scripts/dev.mjs',
      url: 'http://127.0.0.1:3000',
      reuseExistingServer: true,
      timeout: 180_000,
      env: { AGENT_MODE: 'mock' },
    },
  ],
});
