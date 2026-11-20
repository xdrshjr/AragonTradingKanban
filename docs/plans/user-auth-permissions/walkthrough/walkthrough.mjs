#!/usr/bin/env node
/**
 * 真实浏览器走查（spec §25.3.6 / AC-235）——可复跑脚本。
 *
 * 前提：真实栈已启动（API: AUTH_MODE=enforced AUTH_TEST_HOOKS=1 AGENT_MODE=mock；Web: next start），且库里有 ≥3 个标的。
 * 用法：  node walkthrough.mjs <localhost|127.0.0.1> [webPort=3000] [apiPort=8000]
 * 产物：  <步骤>-<主机>-<视口>.png  +  result-<主机>.json（步骤结果、控制台 error、游客 POST 计数、360px 横向溢出）。
 * 浏览器：本机已装的 Edge（channel=msedge），不依赖 playwright 的下载。
 */
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(resolve(here, '../../../../web/package.json'));
const { chromium } = require('playwright-core');

const HOST = process.argv[2] ?? 'localhost';
const WEB_PORT = process.argv[3] ?? '3000';
const API_PORT = process.argv[4] ?? '8000';
const WEB = `http://${HOST}:${WEB_PORT}`;
const API = `http://${HOST}:${API_PORT}`;
const ROOT_USER = 'jdqqjr';
const ROOT_DEFAULT = process.env.ROOT_DEFAULT_PASSWORD ?? '13519126390';
const ROOT_NEW = process.env.ROOT_NEW_PASSWORD ?? 'Wk-Root-Pass-2026!';
const SUFFIX = Date.now().toString(36).slice(-5);
const USER = `wt_${HOST.replace(/\./g, '')}_${SUFFIX}`.slice(0, 20);
const PASS = 'Wt-Str0ng-pass-1';
const PASS2 = 'Wt-N3w-pass-2';
const VIEWPORTS = [
  ['1440x900', 1440, 900],
  ['1024x768', 1024, 768],
  ['390x844', 390, 844],
];

const results = { host: HOST, user: USER, steps: [], consoleErrors: [], guestReportPosts: 0, overflow: {} };
const log = (m) => process.stdout.write(`[walk ${HOST}] ${m}\n`);
const ok = (step, detail = '') => {
  results.steps.push({ step, ok: true, detail });
  log(`OK ${step} ${detail}`);
};

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const page = await context.newPage();

let phase = 'guest';
page.on('console', (msg) => {
  if (msg.type() === 'error') {
    const text = msg.text();
    // 4xx 的网络日志（登录失败 / 验证码换图 / 额度 429）是预期行为，不是页面错误；其余一律计入。
    if (/Failed to load resource.*\b(401|403|422|423|429)\b/.test(text)) return;
    results.consoleErrors.push({ phase, text: text.slice(0, 300), url: page.url() });
  }
});
page.on('pageerror', (e) =>
  results.consoleErrors.push({ phase, text: `pageerror: ${String(e).slice(0, 300)}`, url: page.url() }),
);
page.on('request', (req) => {
  if (phase === 'guest' && req.method() === 'POST' && /\/reports(\?|$)|\/generate/.test(req.url())) {
    results.guestReportPosts += 1;
  }
});

async function shot(name) {
  for (const [label, w, h] of VIEWPORTS) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(250);
    await page.screenshot({ path: join(here, `${name}-${HOST}-${label}.png`) });
  }
  await page.setViewportSize({ width: 360, height: 800 });
  await page.waitForTimeout(200);
  results.overflow[name] = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(150);
}

function watchCaptcha() {
  const state = { id: null };
  page.on('response', async (r) => {
    if (r.url().includes('/auth/captcha') && r.ok()) {
      try {
        state.id = (await r.json()).challengeId;
      } catch {
        /* ignore */
      }
    }
  });
  return () => state.id;
}
async function answerOf(id) {
  const r = await page.request.get(`${API}/api/v1/auth/_test/captcha-answer?challengeId=${id}`);
  if (!r.ok()) throw new Error('captcha-answer 不可用：需要 AUTH_TEST_HOOKS=1');
  return (await r.json()).answer;
}
const byLabel = (re, opts = {}) => page.getByLabel(re, opts);
async function expectVisible(locator, what, timeout = 15000) {
  await locator
    .first()
    .waitFor({ state: 'visible', timeout })
    .catch((e) => {
      throw new Error(`未出现：${what}（${e.message.split('\n')[0]}）`);
    });
}
async function loginUI(user, pass) {
  await page.goto(`${WEB}/login`);
  await byLabel('用户名').fill(user);
  await byLabel('密码', { exact: true }).fill(pass);
  await page.getByRole('button', { name: '登录', exact: true }).click();
}

try {
  // ── 1. 游客 ─────────────────────────────────────────────────────────────
  await page.goto(WEB);
  await page.waitForLoadState('networkidle').catch(() => {});
  ok('1a 游客首页可读', await page.title());
  const links = await page.locator('a[href^="/"]').evaluateAll((as) => [
    ...new Set(
      as
        .map((a) => a.getAttribute('href'))
        .filter((h) => h && /^\/[A-Za-z0-9._-]+$/.test(h) && !/^\/(login|register|reports|account|admin|recover)$/.test(h)),
    ),
  ]);
  if (links.length === 0) throw new Error('没有任何标的链接：先 seed 三个标的');
  const href = links[0];
  await page.goto(`${WEB}/reports`);
  await expectVisible(page.getByRole('heading', { level: 1 }), '报告库标题');
  ok('1b 游客报告库可读');
  await page.goto(`${WEB}${href}`);
  await page.waitForLoadState('networkidle').catch(() => {});
  ok('1c 游客标的页可读', href);
  const guestBtn = page.getByRole('button', { name: /生成/ }).first();
  if (await guestBtn.isVisible().catch(() => false)) {
    await guestBtn.click();
    await expectVisible(page.getByRole('dialog'), '游客点生成 → 登录弹窗');
    await shot('01-guest-login-dialog');
    await page.keyboard.press('Escape');
    ok('1d 按钮入口 → 登录弹窗');
  }
  await page.keyboard.press('g');
  await expectVisible(page.getByRole('dialog'), '热键 G → 登录弹窗');
  await page.keyboard.press('Escape');
  ok('1e 热键 G → 登录弹窗');
  await page.keyboard.press('Control+k');
  await page.waitForTimeout(500);
  if (await page.getByRole('dialog').first().isVisible().catch(() => false)) {
    await page.keyboard.type('生成');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(700);
    await page.keyboard.press('Escape').catch(() => {});
    ok('1f 命令面板「生成」');
  }
  if (results.guestReportPosts !== 0) throw new Error(`游客发出了 ${results.guestReportPosts} 次生成请求`);
  ok('1g 游客生成 POST = 0');

  // ── 2. 登录页 / 注册 → 恢复码 ────────────────────────────────────────────
  phase = 'register';
  await page.goto(`${WEB}/login`);
  await expectVisible(page.getByTestId('login-form'), '登录表单');
  await shot('02-login');
  const cap = watchCaptcha();
  await page.goto(`${WEB}/register`);
  await byLabel('用户名').fill(USER);
  await byLabel('密码', { exact: true }).fill(PASS);
  await byLabel('确认密码').fill(PASS);
  await expectVisible(page.getByAltText(/验证码/), '验证码图');
  await byLabel('验证码').fill(await answerOf(cap()));
  await page.getByLabel(/我已阅读/).check();
  await shot('03-register');
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: '注册并登录' }).click();
  const dialog = page.getByTestId('recovery-key-dialog');
  await expectVisible(dialog, '恢复码弹窗');
  const key1 = (await page.getByTestId('recovery-key-value').innerText()).trim();
  if (!/^[0-9A-HJKMNP-TV-Z]{5}(-[0-9A-HJKMNP-TV-Z]{5}){3}$/.test(key1)) throw new Error(`恢复码格式不对：${key1}`);
  await shot('04-recovery-dialog');
  await page.keyboard.press('Escape');
  await expectVisible(dialog, 'Esc 不关闭恢复码弹窗');
  const cont = page.getByRole('button', { name: '继续' });
  if (!(await cont.isDisabled())) throw new Error('未勾选时「继续」应为 disabled');
  await page.getByLabel(/我已妥善保存/).check();
  await cont.click();
  await dialog.waitFor({ state: 'hidden' });
  ok('2 注册 → 恢复码（Esc 不关、未勾选不可继续）→ 进入工作台');

  phase = 'user';
  await page.goto(`${WEB}${href}`);
  const chipText = async () => (await page.getByTestId('quota-chip').first().innerText()).replace(/\s+/g, ' ');
  await expectVisible(page.getByTestId('quota-chip'), '额度芯片');
  const chip0 = await chipText();
  if (!chip0.includes('3')) throw new Error(`芯片初始应含 3：${chip0}`);
  ok('2b 额度芯片初始', chip0);

  let generated = 0;
  for (const sym of links.slice(0, 3)) {
    await page.goto(`${WEB}${sym}`);
    await expectVisible(page.getByTestId('quota-chip'), '额度芯片');
    const before = await chipText();
    await page.getByRole('button', { name: /生成/ }).first().click();
    await page.waitForFunction(
      (b) => (document.querySelector('[data-testid="quota-chip"]')?.textContent ?? '').replace(/\s+/g, ' ') !== b,
      before,
      { timeout: 60000 },
    );
    generated += 1;
    ok(`2c 生成第 ${generated} 次`, `${sym}: ${before} → ${await chipText()}`);
    await page.waitForTimeout(8000); // 等 mock 引擎跑完，避免与下一次并发
  }

  // ── 3. 额度耗尽 ──────────────────────────────────────────────────────────
  if (generated >= 3) {
    await page.goto(`${WEB}${links[0]}`);
    await page.getByRole('button', { name: /生成/ }).first().click();
    await expectVisible(page.getByTestId('quota-dialog'), '额度耗尽对话框');
    await shot('05-quota-exhausted');
    await page.getByLabel('申请理由').fill('走查：需要为新关注的标的再生成几份研报');
    await page.getByRole('button', { name: '提交申请' }).click();
    await expectVisible(page.getByText(/申请已提交/), '额度申请已提交');
    ok('3 额度耗尽对话框 + 申请额外额度');
    await page.keyboard.press('Escape');
  }

  await page.goto(`${WEB}/account`);
  await page.waitForLoadState('networkidle').catch(() => {});
  await shot('06-account');
  ok('3b 账号页');

  // ── 4. 登出 → 恢复码找回 ─────────────────────────────────────────────────
  phase = 'recover';
  await context.clearCookies();
  const cap2 = watchCaptcha();
  await page.goto(`${WEB}/recover`);
  await byLabel('用户名').fill(USER);
  await byLabel('恢复码').fill(key1.toLowerCase().replace(/-/g, ' '));
  await byLabel('新密码', { exact: true }).fill(PASS2);
  await byLabel('确认新密码').fill(PASS2);
  await expectVisible(page.getByAltText(/验证码/), '找回页验证码图');
  await byLabel('验证码').fill(await answerOf(cap2()));
  await shot('07-recover');
  await page.getByRole('button', { name: '重置密码' }).click();
  await expectVisible(page.getByTestId('recovery-key-dialog'), '找回后展示新恢复码');
  const key2 = (await page.getByTestId('recovery-key-value').innerText()).trim();
  if (key2 === key1) throw new Error('新恢复码与旧码相同');
  await page.getByLabel(/我已妥善保存/).check();
  await page.getByRole('button', { name: '继续' }).click();
  await page.waitForURL(/\/login\?recovered=1/);
  ok('4a 恢复码找回 → 新码展示 → 跳登录页');
  await byLabel('用户名').fill(USER);
  await byLabel('密码', { exact: true }).fill(PASS);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expectVisible(page.getByTestId('login-error'), '旧密码应失败');
  await byLabel('密码', { exact: true }).fill(PASS2);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL(`${WEB}/`);
  ok('4b 旧密码失效、新密码可用');

  await context.clearCookies();
  const cap3 = watchCaptcha();
  await page.goto(`${WEB}/recover`);
  await byLabel('用户名').fill(USER);
  await byLabel('恢复码').fill(key1);
  await byLabel('新密码', { exact: true }).fill('Wt-Again-pass-3');
  await byLabel('确认新密码').fill('Wt-Again-pass-3');
  await expectVisible(page.getByAltText(/验证码/), '验证码图');
  await byLabel('验证码').fill(await answerOf(cap3()));
  await page.getByRole('button', { name: '重置密码' }).click();
  await expectVisible(page.getByTestId('recover-error'), '旧恢复码应被拒');
  ok('4c 旧恢复码被拒（统一的 recovery_invalid 文案）');

  // ── 5. root：后台六页 / 导出 / 批量赠送 ────────────────────────────────
  phase = 'root';
  await context.clearCookies();
  await loginUI(ROOT_USER, ROOT_NEW);
  await page.waitForTimeout(2000);
  if (/login/.test(page.url())) {
    await loginUI(ROOT_USER, ROOT_DEFAULT);
    await page.waitForTimeout(2500);
    log(`root 用初始口令登录后落在 ${page.url()}`);
    if (!/\/account\/password/.test(page.url())) await page.goto(`${WEB}/account/password`);
    await byLabel('当前密码').fill(ROOT_DEFAULT);
    await byLabel('新密码', { exact: true }).fill(ROOT_NEW);
    await byLabel('确认新密码').fill(ROOT_NEW);
    await shot('08-forced-password-change');
    await page.getByRole('button', { name: '修改密码', exact: true }).click();
    await page.waitForTimeout(2500);
    // 改密后其它会话被吊销、当前会话保留：直接访问后台即可验证。
    await page.goto(`${WEB}/admin`);
    if (/login/.test(page.url())) {
      await loginUI(ROOT_USER, ROOT_NEW);
      await page.waitForTimeout(2000);
    }
    ok('5a root 初始口令 → 强制改密 → 进入后台');
  } else {
    ok('5a root 已改过密码，直接登录');
  }

  const adminPages = [
    ['/admin', '概览', '09-admin-overview'],
    ['/admin/users', '用户', null],
    ['/admin/invites', '邀请码', null],
    ['/admin/quota', '额度申请', null],
    ['/admin/audit', '审计', null],
    ['/admin/settings', '设置', null],
  ];
  for (const [path, name, shotName] of adminPages) {
    await page.goto(`${WEB}${path}`);
    await page.waitForLoadState('networkidle').catch(() => {});
    await expectVisible(page.getByRole('heading').first(), `${name}页标题`);
    if (shotName) await shot(shotName);
    ok(`5b 后台·${name}`);
  }

  await page.goto(`${WEB}/admin/users`);
  await page.waitForLoadState('networkidle').catch(() => {});
  const search = page.getByRole('searchbox').or(page.getByPlaceholder(/搜索|用户名/)).first();
  if (await search.isVisible().catch(() => false)) {
    await search.fill(USER.slice(0, 8));
    await page.waitForTimeout(1200);
  }
  const row = page.getByText(USER, { exact: false }).first();
  await expectVisible(row, '走查用户出现在用户表');
  await row.click();
  await page.waitForTimeout(800);
  await shot('10-user-drawer');
  ok('5d 用户抽屉');
  await page.keyboard.press('Escape');

  const check = page.getByRole('checkbox', { name: new RegExp(USER.slice(0, 8)) }).first();
  if (await check.isVisible().catch(() => false)) {
    await check.check();
    await page.getByRole('button', { name: /批量赠送额度/ }).first().click();
    const dlg = page.getByRole('dialog');
    await expectVisible(dlg, '批量赠送对话框');
    await dlg.getByRole('spinbutton').first().fill('2');
    await dlg.getByRole('button', { name: /确认|赠送|提交/ }).last().click();
    await page.waitForTimeout(1500);
    ok('5e 批量赠送额度');
  } else {
    log('WARN 未找到用户行复选框，批量赠送跳过');
  }

  const csv = await page.request.get(`${API}/api/v1/admin/users/export.csv?q=${USER.slice(0, 8)}`);
  const body = await csv.text();
  if (!csv.ok() || !body.includes('username')) throw new Error(`导出失败 ${csv.status()}`);
  if (/password|recovery_hash|token|secret/i.test(body.split('\n')[0])) throw new Error('导出表头含敏感列');
  ok('5f 用户 CSV 导出', `status=${csv.status()} nosniff=${csv.headers()['x-content-type-options']}`);
  const exportBtn = page.getByRole('link', { name: /导出/ }).or(page.getByRole('button', { name: /导出/ })).first();
  if (await exportBtn.isVisible().catch(() => false)) ok('5g 导出按钮可见');

  // ── 6. 注销（走查用户） ──────────────────────────────────────────────────
  phase = 'delete';
  await context.clearCookies();
  await loginUI(USER, PASS2);
  await page.waitForURL(`${WEB}/`, { timeout: 15000 });
  await page.goto(`${WEB}/account`);
  await page.getByRole('tab', { name: '安全' }).click();
  await page.getByRole('button', { name: '注销账号…' }).click();
  const del = page.getByRole('dialog');
  await del.getByLabel('当前密码').fill(PASS2);
  await del.getByLabel('键入用户名确认').fill(USER);
  await del.getByRole('button', { name: '确认注销' }).click();
  await page.waitForURL(`${WEB}/`, { timeout: 15000 });
  ok('6 注销账号');
  results.passed = true;
} catch (err) {
  results.passed = false;
  results.error = String(err?.stack ?? err);
  await page.screenshot({ path: join(here, `FAILED-${HOST}.png`) }).catch(() => {});
  log(`FAIL ${err.message}`);
} finally {
  results.consoleErrorCount = results.consoleErrors.length;
  writeFileSync(join(here, `result-${HOST}.json`), JSON.stringify(results, null, 2), 'utf8');
  await browser.close();
}
process.exit(results.passed && results.consoleErrors.length === 0 ? 0 : 1);
