/**
 * 把一个 `ApiError` 翻成**界面要说的话 + 下一步动作**（spec §24.5.4：前端 `authErrors.ts` 逐码有文案）。
 *
 * 码表与动作在 `lib/authErrors.ts`（体积小、首屏可引用）；文案在 `lib/copy.authPages.ts`（懒加载），
 * 所以这个文件只被登录/注册/账号/对话框这些懒加载面则 import。
 */

import { ApiError } from './api';
import { authErrorAction, isAuthErrorCode, type AuthErrorAction } from './authErrors';
import { copyAuthPages } from './copy.authPages';
import { fill } from './copy';

export interface AuthErrorView {
  code: string;
  message: string;
  hint: string;
  action: AuthErrorAction | null;
  /** `423 account_locked` / `429 rate_limited` 的等待毫秒数。 */
  retryAfterMs: number | null;
  /** `422 weak_password` 未满足的规则 id（前端据此点亮清单）。 */
  rules: string[];
  requestId: string | null;
}

/** 「45 秒」「12 分钟」——向上取整，永不给出"0"。 */
export function formatWait(ms: number | null | undefined): string {
  const w = copyAuthPages.errors.wait;
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms <= 0) return w.now;
  const seconds = Math.ceil(ms / 1000);
  if (seconds < 60) return fill(w.seconds, { n: seconds });
  return fill(w.minutes, { n: Math.ceil(seconds / 60) });
}

export function describeAuthError(error: unknown): AuthErrorView {
  const errors = copyAuthPages.errors;
  if (!(error instanceof ApiError)) {
    return { code: 'unknown', message: errors.fallback.message, hint: errors.fallback.hint, action: 'retry', retryAfterMs: null, rules: [], requestId: null };
  }

  const retryAfterMs = error.retryAfterMs;
  const rules = Array.isArray(error.detail.rules) ? error.detail.rules.filter((r): r is string => typeof r === 'string') : [];
  const requestId = error.requestId && error.requestId !== '-' ? error.requestId : null;

  // 网络层（0）与超时（408）不是业务码。
  if (error.status === 0 || error.status === 408) {
    return { code: error.code, message: errors.network.message, hint: errors.network.hint, action: 'retry', retryAfterMs: null, rules, requestId };
  }

  let entry: { message: string; hint: string } = errors.fallback;
  let key = error.code;
  if (error.code === 'unauthorized') {
    const reason = typeof error.detail.reason === 'string' ? error.detail.reason : 'no_session';
    entry = reason === 'no_session' ? errors.unauthorized : errors.unauthorizedExpired;
  } else if (error.code === 'forbidden' && error.detail.reason === 'only_version') {
    // ★Y2(a)：非 manage_any 归档自己的唯一版本（不新增错误码，走 detail.reason）。
    entry = errors.forbiddenOnlyVersion;
  } else if (isAuthErrorCode(error.code)) {
    entry = errors[error.code];
  } else {
    key = 'fallback';
  }

  return {
    code: error.code,
    message: entry.message,
    hint: entry.hint ? fill(entry.hint, { wait: formatWait(retryAfterMs) }) : '',
    action: authErrorAction(error.code) ?? (key === 'fallback' ? 'retry' : null),
    retryAfterMs,
    rules,
    requestId,
  };
}
