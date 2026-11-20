/**
 * 认证相关的**全局浏览器事件**（§24.3.15a）。
 *
 * `lib/api.ts` 的 `request()` 在浏览器里收到"会话失效"或"必须改密"时只负责广播，
 * `components/shell/Providers.tsx` 负责订阅并做 UI 反应（清 `['me']`、toast、打开登录框、跳转）。
 * 拆成事件而不是让 `api.ts` 直接 import UI，是为了让 `api.ts` 保持纯数据层、可在服务端安全引用。
 */

export type AuthEventName = 'unauthorized' | 'password-change-required';

export interface AuthEventDetail {
  reason?: string | null;
}

const PREFIX = 'aragon:auth:';

export function authEventType(name: AuthEventName): string {
  return `${PREFIX}${name}`;
}

export function emitAuthEvent(name: AuthEventName, detail: AuthEventDetail = {}): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<AuthEventDetail>(authEventType(name), { detail }));
}

export function onAuthEvent(name: AuthEventName, handler: (detail: AuthEventDetail) => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const listener = (event: Event) => handler((event as CustomEvent<AuthEventDetail>).detail ?? {});
  window.addEventListener(authEventType(name), listener);
  return () => window.removeEventListener(authEventType(name), listener);
}
