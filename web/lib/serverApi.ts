import 'server-only';

import { cookies } from 'next/headers';

import { createApi, type Api } from './api';

/** 会话 Cookie 的名字（与后端 `aragon_sid` 一致，§24.3.2）。 */
export const SESSION_COOKIE = 'aragon_sid';

/**
 * 服务端（RSC）用的 API 客户端：把浏览器的会话 Cookie **显式转发**给 API（§24.3.2(c)）。
 *
 * 为什么必须是单独的 `server-only` 文件，而不是在 `api.ts` 里动态 `import('next/headers')`：
 * `api.ts` 同时被客户端组件引用，Next 会对客户端依赖图里出现的 `next/headers` 报构建错。
 *
 * 只能在服务端组件 / route handler 里调用（`cookies()` 需要请求上下文）。
 * 服务端实例一律 `no-store`（见 `createApi`），所以首屏按主体渲染，不会串缓存（RV-6）。
 */
export async function serverApi(): Promise<Api> {
  const jar = await cookies();
  const sid = jar.get(SESSION_COOKIE)?.value;
  return createApi({ cookie: sid ? `${SESSION_COOKIE}=${sid}` : undefined });
}
