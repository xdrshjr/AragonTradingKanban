/**
 * 准入级错误码表（spec §24.3.15c / §24.5.4）。
 *
 * 与 `runErrors.ts` **分表**：`RUN_ERROR_ACTIONS` 约束的是 run 级失败码集合（AC-104 的双向集合相等不能被
 * HTTP 码污染），而这里是"请求在准入阶段就被拒绝"的码——它们不会出现在 run 的生命周期里。
 *
 * 这个文件**只放码表与判定**（体积小，`useGenerationStream` 要静态引用它）；面向用户的文案在
 * `lib/copy.authPages.ts`（懒加载），由 `describeAuthError()`（`lib/authErrorText.ts`）拼装。
 */

import type { ApiError } from './api';

/** 错误发生后界面应当给出的**下一步动作**。 */
export type AuthErrorAction =
  | 'login' // 打开登录框
  | 'retry' // 稍后重试 / 再点一次
  | 'wait' // 有明确的等待时长（`retryAfterMs`）
  | 'refresh_captcha' // 换一张验证码并聚焦
  | 'fix_input' // 修改输入
  | 'request_quota' // 打开额度对话框
  | 'change_password' // 跳转强制改密页
  | 'contact_admin' // 只能联系管理员
  | 'reload'; // 刷新页面

export const AUTH_ERROR_ACTIONS = {
  unauthorized: 'login',
  forbidden: 'contact_admin',
  invalid_credentials: 'fix_input',
  account_disabled: 'contact_admin',
  account_locked: 'wait',
  password_change_required: 'change_password',
  csrf_rejected: 'reload',
  rate_limited: 'wait',
  quota_exceeded: 'request_quota',
  captcha_required: 'refresh_captcha',
  captcha_invalid: 'refresh_captcha',
  captcha_unavailable: 'contact_admin',
  registration_closed: 'contact_admin',
  invite_required: 'fix_input',
  invite_invalid: 'fix_input',
  invalid_username: 'fix_input',
  username_taken: 'fix_input',
  weak_password: 'fix_input',
  root_protected: 'contact_admin',
  self_action_forbidden: 'contact_admin',
  admin_protected: 'contact_admin',
  quota_request_pending: 'wait',
  invalid_setting: 'fix_input',
  policy_missing: 'contact_admin',
  busy: 'retry',
  recovery_invalid: 'contact_admin',
  recovery_disabled: 'contact_admin',
} as const satisfies Record<string, AuthErrorAction>;

export type AuthErrorCode = keyof typeof AUTH_ERROR_ACTIONS;

export const AUTH_ERROR_CODES = Object.keys(AUTH_ERROR_ACTIONS) as AuthErrorCode[];

export function isAuthErrorCode(code: string): code is AuthErrorCode {
  return Object.prototype.hasOwnProperty.call(AUTH_ERROR_ACTIONS, code);
}

export function authErrorAction(code: string): AuthErrorAction | null {
  return isAuthErrorCode(code) ? AUTH_ERROR_ACTIONS[code] : null;
}

/** 额度类拒绝：`429 quota_exceeded`（预检或预留；`detail.scope` 区分个人/全站）。 */
export function isQuotaExceeded(error: unknown): boolean {
  return Boolean(error) && (error as ApiError).code === 'quota_exceeded';
}

/** 需要登录（含"会话刚刚失效"）。 */
export function isUnauthorized(error: unknown): boolean {
  return Boolean(error) && (error as ApiError).code === 'unauthorized';
}

export interface QuotaExceededDetail {
  resource: 'report' | 'deep_ask';
  scope: 'user' | 'site';
  limit: number | null;
  usedToday: number;
  bonus: number;
  resetAt: string | null;
  canRequest: boolean;
}

/** 把 `quota_exceeded` 的 `detail` 规整成对话框要的形状（缺字段给安全默认，不抛）。 */
export function readQuotaDetail(error: ApiError): QuotaExceededDetail {
  const d = error.detail ?? {};
  const num = (value: unknown, fallback: number) => (typeof value === 'number' && Number.isFinite(value) ? value : fallback);
  const scope = d.scope === 'site' ? 'site' : 'user';
  return {
    resource: d.resource === 'deep_ask' ? 'deep_ask' : 'report',
    scope,
    limit: typeof d.limit === 'number' ? d.limit : null,
    usedToday: num(d.usedToday, 0),
    bonus: num(d.bonus, 0),
    resetAt: typeof d.resetAt === 'string' ? d.resetAt : null,
    // 全站名额已满时"申请额外额度"无济于事（§24.3.8）。
    canRequest: scope === 'site' ? false : d.canRequest !== false,
  };
}
