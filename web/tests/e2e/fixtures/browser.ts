import { existsSync } from 'node:fs';

import { chromium } from '@playwright/test';

/**
 * 「这台机器上能不能跑 e2e」的唯一判据（spec §19.3.7 / AC-59）。
 *
 * 背景：没有浏览器二进制时，旧 spec 在 `test.beforeAll` 里抛异常，`npm run test:e2e`
 * 于是以**失败**收场，而失败与"环境缺失"是两件事——前者需要人改代码，后者只需要
 * 一行 `npx playwright install chromium`。两者混在一起，套件就会长期红着，最终没人看。
 *
 * 因此：
 *
 * - `browserAvailable`：chromium 的可执行文件真的在磁盘上（不是"Playwright 认为它该在
 *   哪里"）。判断用 `existsSync(chromium.executablePath())`，不联网、不 launch。
 * - `required()`：`PW_REQUIRED=1` 表示"这次运行必须真的跑"。CI 上应当设它——
 *   在 CI 里静默跳过等于把端到端验证删掉，而 `PW_REQUIRED` 让这种缺失变成失败。
 *
 * 每个 spec 的用法固定两行：
 *
 * ```ts
 * failIfBrowserRequired();
 * test.skip(!browserAvailable, SKIP_REASON);
 * ```
 */

/** chromium 的可执行文件是否存在。任何异常都按"不可用"处理，绝不让探测本身炸掉套件。 */
export const browserAvailable: boolean = (() => {
  try {
    return existsSync(chromium.executablePath());
  } catch {
    return false;
  }
})();

/** 跳过的原因必须自带修复命令：读者不该再去翻文档。 */
export const SKIP_REASON = 'chromium 未安装：npx playwright install chromium';

/** `PW_REQUIRED=1` → 缺浏览器是失败而不是跳过（CI 用）。容忍 Windows `set` 的尾随空格。 */
export function required(): boolean {
  return process.env.PW_REQUIRED?.trim() === '1';
}

/**
 * `PW_REQUIRED=1` 且没有浏览器 → 直接失败（在收集阶段抛，报错里带修复命令）。
 *
 * 放在 `test.skip(...)` **之前**调用：只有不需要强制时才会走到 skip。
 */
export function failIfBrowserRequired(): void {
  if (browserAvailable || !required()) return;
  throw new Error(`PW_REQUIRED=1，但 ${SKIP_REASON}`);
}
