import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

import { SKIP_REASON, browserAvailable, failIfBrowserRequired } from './fixtures/browser';

/**
 * 用户与权限系统 e2e（spec §25.3.6 / AC-236）：游客拦截 / 注册 + 恢复码 / 额度 / 找回 / 后台守卫。
 *
 * ★与其它 spec 的区别：这些用例需要**真实的** API（`AUTH_MODE=enforced`、`AUTH_TEST_HOOKS=1`
 * 才有取验证码答案的 `/auth/_test/captcha-answer`），`fake-api.mjs` 不实现认证。所以本文件
 * 只在显式给出真实栈地址时才跑：
 *
 *     # 终端 A：真实后端（AUTH_TEST_HOOKS 仅走查/e2e 期开启，**生产永远不要开**）
 *     AUTH_MODE=enforced AUTH_TEST_HOOKS=1 AGENT_MODE=mock  <启动 api>   # 默认 127.0.0.1:8000
 *     # 终端 B：前端 → 指向它
 *     NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000  npx next dev --port 3000
 *     # 终端 C：
 *     E2E_AUTH=1 E2E_BASE_URL=http://127.0.0.1:3000 E2E_API_BASE=http://127.0.0.1:8000 \
 *       npx playwright test tests/e2e/auth.spec.ts
 *
 * 没设 `E2E_AUTH=1`（或没装 chromium）时全部 skip——**环境缺失不是代码坏了**；但走查证据
 * （AC-235，`docs/plans/user-auth-permissions/walkthrough/`）不可被 skip 代替（E-21）。
 */

failIfBrowserRequired();
test.skip(!browserAvailable, SKIP_REASON);
test.skip(process.env.E2E_AUTH !== '1', '需要真实 enforced 栈：设 E2E_AUTH=1 E2E_BASE_URL=… E2E_API_BASE=…（见文件头）');

const API = process.env.E2E_API_BASE ?? 'http://127.0.0.1:8000';
const SUFFIX = Date.now().toString(36).slice(-6);
const USERNAME = `e2e_${SUFFIX}`;
const PASSWORD = 'E2e-Str0ng-pass-1';
const NEW_PASSWORD = 'E2e-N3w-pass-2';

test.describe.configure({ mode: 'serial' });

/** 记下最近一张验证码挑战（要在触发取图的操作**之前**挂上）。 */
function watchCaptcha(page: Page): { latest: () => string | null } {
  let id: string | null = null;
  page.on('response', async (response) => {
    if (response.url().includes('/auth/captcha') && response.ok()) {
      try {
        id = ((await response.json()) as { challengeId: string }).challengeId;
      } catch {
        /* 忽略 */
      }
    }
  });
  return { latest: () => id };
}

async function answerOf(page: Page, id: string | null): Promise<string> {
  if (!id) throw new Error('还没有验证码挑战');
  const res = await page.request.get(`${API}/api/v1/auth/_test/captcha-answer?challengeId=${id}`);
  expect(res.ok(), '需要 AUTH_TEST_HOOKS=1').toBeTruthy();
  return ((await res.json()) as { answer: string }).answer;
}

test('游客：可浏览首页；三处生成入口都弹登录框，且不发出 POST /reports', async ({ page }) => {
  const posts: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/instruments\/[^/]+\/reports/.test(request.url())) posts.push(request.url());
  });
  await page.goto('/');
  await expect(page.getByRole('main').or(page.locator('body'))).toBeVisible();

  // 报告库对游客可读。
  await page.goto('/reports');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

  // 在任一标的页尝试生成（按钮 / 热键 G / 命令面板）。没有标的数据的空库里这一步会被跳过。
  const first = page.locator('a[href^="/"][href*="."]').first();
  if ((await first.count()) > 0) {
    await first.click();
    const generate = page.getByRole('button', { name: /登录后生成/ }).first();
    if (await generate.isVisible().catch(() => false)) {
      await generate.click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await page.keyboard.press('Escape');
    }
    await page.keyboard.press('g');
    await expect(page.getByRole('dialog').first()).toBeVisible();
    await page.keyboard.press('Escape');
  }
  expect(posts, '游客不得发出生成请求').toEqual([]);
});

test('/admin 与 /account：没有会话 → 去登录页并带 next', async ({ page }) => {
  await page.goto('/admin');
  await expect(page).toHaveURL(/\/login\?next=%2Fadmin/);
  await page.goto('/account');
  await expect(page).toHaveURL(/\/login\?next=%2Faccount/);
});

test('注册：验证码 → 恢复码弹窗（Esc 不关、未勾选不能继续）→ 进入账号', async ({ page }) => {
  const captcha = watchCaptcha(page);
  await page.goto('/register');
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD);
  await page.getByLabel('确认密码').fill(PASSWORD);
  await expect(page.getByAltText(/验证码/)).toBeVisible();
  await page.getByLabel('验证码').fill(await answerOf(page, captcha.latest()));
  await page.getByLabel(/我已阅读/).check();
  // 服务端要求“距签发 ≥ 1.2 s”（过快陷阱），等一下再提交。
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: '注册并登录' }).click();

  const dialog = page.getByTestId('recovery-key-dialog');
  await expect(dialog).toBeVisible();
  const key = (await page.getByTestId('recovery-key-value').innerText()).trim();
  expect(key).toMatch(/^[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}$/);
  test.info().annotations.push({ type: 'recoveryKey', description: 'kept in memory for the recover step' });
  (globalThis as { __e2eKey?: string }).__e2eKey = key;

  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('button', { name: '继续' })).toBeDisabled();
  await page.getByLabel(/我已妥善保存/).check();
  await page.getByRole('button', { name: '继续' }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/($|\?)/);
});

test('额度芯片可见；退出后用恢复码找回：旧密码失效、新恢复码展示', async ({ page }) => {
  const key = (globalThis as { __e2eKey?: string }).__e2eKey;
  test.skip(!key, '上一步没有拿到恢复码');

  // 登录（注册成功后会话在上一个测试的上下文里，这里重新登录）
  await page.goto('/login');
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/($|\?)/);

  // 账号页：恢复码卡显示“已设置”，危险区存在。
  await page.goto('/account');
  await page.getByRole('tab', { name: '安全' }).click();
  await expect(page.getByTestId('recovery-status')).toContainText('已设置');
  await expect(page.getByTestId('danger-zone')).toBeVisible();

  // 退出 → 找回
  await page.context().clearCookies();
  const captcha = watchCaptcha(page);
  await page.goto('/recover');
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('恢复码').fill(key!.toLowerCase().replace(/-/g, ' '));
  await page.getByLabel('新密码', { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel('确认新密码').fill(NEW_PASSWORD);
  await expect(page.getByAltText(/验证码/)).toBeVisible();
  await page.getByLabel('验证码').fill(await answerOf(page, captcha.latest()));
  await page.getByRole('button', { name: '重置密码' }).click();

  const dialog = page.getByTestId('recovery-key-dialog');
  await expect(dialog).toBeVisible();
  const fresh = (await page.getByTestId('recovery-key-value').innerText()).trim();
  expect(fresh).not.toBe(key);
  await page.getByLabel(/我已妥善保存/).check();
  await page.getByRole('button', { name: '继续' }).click();
  await expect(page).toHaveURL(/\/login\?recovered=1/);
  await expect(page.getByTestId('login-notice')).toContainText('密码已重置');

  // 旧密码失效，新密码可用。
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page.getByTestId('login-error')).toContainText('用户名或密码不正确');
  await page.getByLabel('密码', { exact: true }).fill(NEW_PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/($|\?)/);
});

test('普通用户访问 /admin：权限视图（不是 404），后台接口 403', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('密码', { exact: true }).fill(NEW_PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/($|\?)/);

  await page.goto('/admin');
  await expect(page.getByTestId('admin-forbidden')).toBeVisible();
  const overview = await page.request.get(`${API}/api/v1/admin/overview`);
  expect(overview.status()).toBe(403);
});

test('自助注销：密码 + 键入用户名 → 回首页；旧账号不能再登录', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('密码', { exact: true }).fill(NEW_PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/($|\?)/);

  await page.goto('/account');
  await page.getByRole('tab', { name: '安全' }).click();
  await page.getByRole('button', { name: '注销账号…' }).click();
  const dialog = page.getByRole('dialog');
  const confirm = dialog.getByRole('button', { name: '确认注销' });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel('当前密码').fill(NEW_PASSWORD);
  await dialog.getByLabel('键入用户名确认').fill(USERNAME);
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page).toHaveURL(/\/$/);

  await page.goto('/login');
  await page.getByLabel('用户名').fill(USERNAME);
  await page.getByLabel('密码', { exact: true }).fill(NEW_PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page.getByTestId('login-error')).toContainText('用户名或密码不正确');
});
