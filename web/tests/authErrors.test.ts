import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api';
import { describeAuthError, formatWait } from '@/lib/authErrorText';
import {
  AUTH_ERROR_ACTIONS,
  AUTH_ERROR_CODES,
  authErrorAction,
  isAuthErrorCode,
  isQuotaExceeded,
  isUnauthorized,
  readQuotaDetail,
} from '@/lib/authErrors';
import { copyAuthPages } from '@/lib/copy.authPages';
import { copy } from '@/lib/copy';

/** spec §24.5.4 的新增错误码（+ 既有 `unauthorized`、+ RV-7 的 `busy`）。逐码必须有文案与动作。 */
const SPEC_CODES = [
  'unauthorized',
  'forbidden',
  'invalid_credentials',
  'account_disabled',
  'account_locked',
  'password_change_required',
  'csrf_rejected',
  'rate_limited',
  'quota_exceeded',
  'captcha_required',
  'captcha_invalid',
  'captcha_unavailable',
  'registration_closed',
  'invite_required',
  'invite_invalid',
  'invalid_username',
  'username_taken',
  'weak_password',
  'root_protected',
  'self_action_forbidden',
  'admin_protected',
  'quota_request_pending',
  'invalid_setting',
  'policy_missing',
  // ★Y3（§25.5.3）
  'recovery_invalid',
  'recovery_disabled',
];

function apiError(status: number, code: string, detail: Record<string, unknown> = {}): ApiError {
  return new ApiError(status, { error: { code, message: 'server text', requestId: 'req-1', detail } });
}

describe('authErrors 码表（§24.5.4）', () => {
  it('规格里的每个码都在表里，且有动作', () => {
    for (const code of SPEC_CODES) {
      expect(isAuthErrorCode(code), code).toBe(true);
      expect(authErrorAction(code), code).toBeTruthy();
    }
    expect(AUTH_ERROR_CODES.length).toBeGreaterThanOrEqual(SPEC_CODES.length);
  });

  it('每个码都有非空的陈述句文案；hint 要么空要么给出下一步', () => {
    for (const code of AUTH_ERROR_CODES) {
      const entry = (copyAuthPages.errors as unknown as Record<string, { message: string; hint: string } | undefined>)[code];
      expect(entry, `缺文案：${code}`).toBeDefined();
      expect(entry!.message.length).toBeGreaterThan(3);
    }
  });

  it('全部文案不用感叹号（语气约定）', () => {
    const bag = JSON.stringify([copyAuthPages, copy.auth, copy.quota]);
    expect(bag).not.toMatch(/[!！]/);
  });

  it('与 runErrors 分表：run 级码集合不被 HTTP 准入码污染（AC-104）', () => {
    const runCodes = Object.keys((AUTH_ERROR_ACTIONS as Record<string, unknown>));
    expect(runCodes).not.toContain('run_failed');
  });

  it('额度 / 登录类动作符合 §24.3.15c', () => {
    expect(authErrorAction('quota_exceeded')).toBe('request_quota');
    expect(authErrorAction('unauthorized')).toBe('login');
    expect(authErrorAction('password_change_required')).toBe('change_password');
    expect(authErrorAction('captcha_invalid')).toBe('refresh_captcha');
    expect(authErrorAction('account_locked')).toBe('wait');
  });
});

describe('describeAuthError', () => {
  it('invalid_credentials 统一话术，不区分原因', () => {
    const view = describeAuthError(apiError(401, 'invalid_credentials'));
    expect(view.message).toBe('用户名或密码不正确。');
    expect(view.action).toBe('fix_input');
  });

  it('unauthorized 按 reason 区分“没登录”与“登录失效”', () => {
    expect(describeAuthError(apiError(401, 'unauthorized', { reason: 'no_session' })).message).toBe(
      copyAuthPages.errors.unauthorized.message,
    );
    expect(describeAuthError(apiError(401, 'unauthorized', { reason: 'expired' })).message).toBe(
      copyAuthPages.errors.unauthorizedExpired.message,
    );
  });

  it('account_locked 把 retryAfterMs 翻成人话（向上取整，永不给 0）', () => {
    const view = describeAuthError(apiError(423, 'account_locked', { retryAfterMs: 14 * 60_000 + 1 }));
    expect(view.retryAfterMs).toBe(14 * 60_000 + 1);
    expect(view.hint).toContain('15 分钟');
    expect(formatWait(0)).toBe('稍候');
    expect(formatWait(1)).toBe('1 秒');
    expect(formatWait(59_000)).toBe('59 秒');
    expect(formatWait(59_001)).toBe('1 分钟');
    expect(formatWait(61_000)).toBe('2 分钟');
  });

  it('weak_password 带出未满足的规则 id', () => {
    const view = describeAuthError(apiError(422, 'weak_password', { rules: ['min_length', 'common_password'] }));
    expect(view.rules).toEqual(['min_length', 'common_password']);
  });

  it('网络错误与超时是“无法连接”，不是业务码', () => {
    const view = describeAuthError(new ApiError(0, null));
    expect(view.message).toBe(copyAuthPages.errors.network.message);
    expect(describeAuthError(new ApiError(408, null)).action).toBe('retry');
  });

  it('未知码与非 ApiError 都有兜底，不抛', () => {
    expect(describeAuthError(apiError(500, 'something_new')).message).toBe(copyAuthPages.errors.fallback.message);
    expect(describeAuthError(new Error('boom')).message).toBe(copyAuthPages.errors.fallback.message);
  });

  it('requestId 回显（排障用），占位符 - 不回显', () => {
    expect(describeAuthError(apiError(500, 'x')).requestId).toBe('req-1');
    expect(describeAuthError(new ApiError(0, null)).requestId).toBeNull();
  });
});

describe('额度错误识别', () => {
  it('isQuotaExceeded / isUnauthorized 只认各自的码', () => {
    expect(isQuotaExceeded(apiError(429, 'quota_exceeded'))).toBe(true);
    expect(isQuotaExceeded(apiError(429, 'rate_limited'))).toBe(false);
    expect(isUnauthorized(apiError(401, 'unauthorized'))).toBe(true);
    expect(isUnauthorized(apiError(401, 'invalid_credentials'))).toBe(false);
    expect(isQuotaExceeded(null)).toBe(false);
  });

  it('readQuotaDetail 规整 detail；全站名额已满时 canRequest 恒为 false', () => {
    const user = readQuotaDetail(
      apiError(429, 'quota_exceeded', { resource: 'report', scope: 'user', limit: 3, usedToday: 3, bonus: 0, resetAt: '2026-11-21T16:00:00Z', canRequest: true }),
    );
    expect(user).toMatchObject({ scope: 'user', limit: 3, usedToday: 3, canRequest: true });
    const site = readQuotaDetail(apiError(429, 'quota_exceeded', { scope: 'site', canRequest: true }));
    expect(site.scope).toBe('site');
    expect(site.canRequest).toBe(false);
    // 缺字段不抛
    expect(readQuotaDetail(apiError(429, 'quota_exceeded'))).toMatchObject({ resource: 'report', usedToday: 0, limit: null });
  });
});
