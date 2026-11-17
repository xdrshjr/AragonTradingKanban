/**
 * AC-104 的前端半边：`contracts/run_error_codes.json` 的**双向**一致性。
 *
 * 三个断言，每个都对应一次真实事故：
 * 1. `RUN_ERROR_ACTIONS ⊇ codes` —— v8 的 V8-P1-3：服务端发了 `run.retry`，前端订阅清单
 *    里没有，事件静默消失（测试只测 reducer 就会全绿）。
 * 2. `HTTP_ERROR_ACTIONS ⊇ httpCodes` —— 402/409/503 是另一组码，混进 run 码表会让
 *    "集合相等"永远红。
 * 3. `sseEvents` 与 `web/lib/sse.ts` 的订阅数组**逐项相等** —— 那张数组是手写的，
 *    服务端多一个名字就是静默丢失。
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { ACTION_LABELS, HTTP_ERROR_ACTIONS, RUN_ERROR_ACTIONS, actionFor, primaryActionFor } from '@/lib/runErrors';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');
const CONTRACT = JSON.parse(readFileSync(join(ROOT, 'contracts', 'run_error_codes.json'), 'utf-8')) as {
  version: number;
  codes: { code: string; severity: string; retryable: boolean; action: string }[];
  httpCodes: { code: string; status: number; action: string }[];
  sseEvents: string[];
};

describe('contracts/run_error_codes.json 双向一致（AC-104）', () => {
  it('每一个 run 码都有动作（18 条，一个不漏）', () => {
    const missing = CONTRACT.codes.map((row) => row.code).filter((code) => !(code in RUN_ERROR_ACTIONS));
    expect(missing).toEqual([]);
    expect(Object.keys(RUN_ERROR_ACTIONS).length).toBeGreaterThanOrEqual(CONTRACT.codes.length);
  });

  it('每一个 HTTP 码都有动作，且不与 run 码混表', () => {
    const missing = CONTRACT.httpCodes.map((row) => row.code).filter((code) => !(code in HTTP_ERROR_ACTIONS));
    expect(missing).toEqual([]);
    // 防回归：`monthly_budget_exceeded` 是 402 HTTP 码，**不在** RUN_ERROR_CODES 里。
    expect(RUN_ERROR_ACTIONS.monthly_budget_exceeded).toBeUndefined();
  });

  it('动作与契约夹具逐条一致', () => {
    for (const row of [...CONTRACT.codes, ...CONTRACT.httpCodes]) {
      expect(actionFor(row.code).action, `${row.code} 的动作漂移了`).toBe(row.action);
    }
  });

  it('SSE 订阅清单与服务端事件名逐项相等', () => {
    // 用 `.forEach(wire)` 与 `as RunStreamEventType` 两个锚点定位那张数组：
    // 后者里面的 `[]` 是类型后缀，不是数组本身，所以先把它切掉再取最后一对方括号。
    const source = readFileSync(join(HERE, '..', 'lib', 'sse.ts'), 'utf-8');
    const head = source.slice(0, source.indexOf('.forEach(wire)'));
    const beforeType = head.slice(0, head.lastIndexOf('as RunStreamEventType'));
    const block = beforeType.slice(beforeType.lastIndexOf('['), beforeType.lastIndexOf(']') + 1);
    const wired = [...block.matchAll(/'([a-z]+\.[a-z]+)'/g)].map((match) => match[1]);
    expect(wired.length).toBeGreaterThan(0);
    expect([...wired].sort()).toEqual([...CONTRACT.sseEvents].sort());
  });

  it('每个动作都有一句人话（`none` 可以是空串，它不该出现按钮）', () => {
    for (const [code, spec] of Object.entries(RUN_ERROR_ACTIONS)) {
      if (spec.action === 'none') continue;
      expect(ACTION_LABELS[spec.action], `${code} 的动作没有文案`).toBeTruthy();
    }
  });
});

describe('primaryActionFor', () => {
  it('凭证错 → 检查密钥（唯一的主按钮）', () => {
    expect(primaryActionFor('provider_auth')).toBe('checkKey');
  });

  it('网络抖动 → 没有主按钮（唯一该做的是等）', () => {
    expect(primaryActionFor('provider_unavailable')).toBeNull();
  });

  it('取消 / 标的被删 → 不需要任何动作', () => {
    expect(primaryActionFor('cancelled')).toBeNull();
    expect(primaryActionFor('instrument_not_found')).toBeNull();
  });

  it('未知码兜底到"看日志"，不猜动作', () => {
    expect(actionFor('something_new').action).toBe('openLog');
    expect(primaryActionFor('something_new')).toBeNull();
  });

  it('有生成在跑时的探针 → 等，而不是报错', () => {
    expect(actionFor('probe_busy').action).toBe('wait');
    expect(actionFor('probe_in_progress').action).toBe('wait');
  });
});
