import { NextResponse, type NextRequest } from 'next/server';

import { safeNext } from '@/lib/safeNext';

/**
 * 边缘中间件（spec §24.3.12 / §24.4.2）——只做两件**无需网络**的事：
 *
 * 1. 给每个页面响应打 `X-Aragon-Viewer: guest|user`，并声明 `Vary: Cookie`。
 *    依据**仅仅是 `aragon_sid` Cookie 是否存在**，不校验有效性（校验要回源，边缘不该做）。
 *    离线 Service Worker 据此决定"这份导航响应能不能落缓存"：只有 `guest` 才缓存，登录态的页面
 *    渲染着用户名与额度，缓存它等于把上一个人的页面交给下一个人（E-11 / AC-204）。
 *    Cookie 存在但已失效时标成 `user`——宁可少缓存，不可错缓存。
 * 2. `/admin`、`/account` 在**没有** Cookie 时直接 307 到 `/login?next=`，省一次服务端渲染。
 *    有 Cookie 但无效的情形由页面自己的服务端校验处理（guest → 重定向 / user → 403 视图）。
 *
 * 授权**不在这里**：中间件看不到会话是否有效，更不知道角色。
 */

const SESSION_COOKIE_NAME = 'aragon_sid';
const VIEWER_HEADER = 'X-Aragon-Viewer';

const PROTECTED_PREFIXES = ['/admin', '/account'];

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export function middleware(request: NextRequest) {
  const hasSession = request.cookies.has(SESSION_COOKIE_NAME);
  const { pathname, search } = request.nextUrl;

  if (!hasSession && isProtected(pathname)) {
    const target = request.nextUrl.clone();
    target.pathname = '/login';
    target.search = '';
    target.searchParams.set('next', safeNext(`${pathname}${search}`, '/'));
    const redirect = NextResponse.redirect(target);
    redirect.headers.set(VIEWER_HEADER, 'guest');
    redirect.headers.set('Cache-Control', 'no-store');
    return redirect;
  }

  const response = NextResponse.next();
  response.headers.set(VIEWER_HEADER, hasSession ? 'user' : 'guest');
  response.headers.append('Vary', 'Cookie');
  return response;
}

export const config = {
  // 页面请求；静态资源、SW 与公开图片不经过（它们不渲染"当前用户"）。
  matcher: ['/((?!_next/static|_next/image|favicon.ico|sw.js|logo/).*)'],
};
