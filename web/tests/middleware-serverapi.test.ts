import { NextRequest } from 'next/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { config, middleware } from '@/middleware';

const cookieJar = vi.hoisted(() => ({ value: undefined as string | undefined }));

vi.mock('next/headers', () => ({
  cookies: async () => ({
    get: (name: string) => (name === 'aragon_sid' && cookieJar.value ? { name, value: cookieJar.value } : undefined),
  }),
}));

function req(path: string, cookie?: string) {
  return new NextRequest(`https://aragontrading.online${path}`, cookie ? { headers: { cookie } } : undefined);
}

afterEach(() => {
  cookieJar.value = undefined;
  vi.unstubAllGlobals();
});

describe('middleware：X-Aragon-Viewer（§24.3.12，SW 隔离的第一道）', () => {
  it('没有 aragon_sid ⇒ guest；有（不论是否有效）⇒ user；都带 Vary: Cookie', () => {
    const guest = middleware(req('/600519.SH'));
    expect(guest.headers.get('X-Aragon-Viewer')).toBe('guest');
    expect(guest.headers.get('Vary')).toContain('Cookie');

    const user = middleware(req('/600519.SH', 'aragon_sid=whatever'));
    expect(user.headers.get('X-Aragon-Viewer')).toBe('user');
    expect(user.headers.get('Vary')).toContain('Cookie');
  });

  it('只看 Cookie 名：别的 Cookie 不算登录', () => {
    expect(middleware(req('/', 'theme=dark')).headers.get('X-Aragon-Viewer')).toBe('guest');
  });
});

describe('middleware：/admin、/account 无 Cookie 时重定向到 /login?next=', () => {
  it.each(['/admin', '/admin/users', '/account', '/account/password'])('%s', (path) => {
    const response = middleware(req(`${path}?x=1`));
    expect(response.status).toBe(307);
    const location = new URL(response.headers.get('location')!);
    expect(location.pathname).toBe('/login');
    expect(location.searchParams.get('next')).toBe(`${path}?x=1`);
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.get('X-Aragon-Viewer')).toBe('guest');
  });

  it('有 Cookie 时放行（有效性由页面自己的服务端校验）', () => {
    const response = middleware(req('/admin', 'aragon_sid=x'));
    expect(response.status).toBe(200);
    expect(response.headers.get('location')).toBeNull();
  });

  it('不误伤前缀相似的公开路径', () => {
    expect(middleware(req('/administrator')).status).toBe(200);
    expect(middleware(req('/accounts-receivable')).status).toBe(200);
  });

  it('matcher 排除静态资源与 SW', () => {
    const pattern = new RegExp(`^${config.matcher[0]}$`);
    expect(pattern.test('/600519.SH')).toBe(true);
    expect(pattern.test('/_next/static/chunks/a.js')).toBe(false);
    expect(pattern.test('/sw.js')).toBe(false);
    expect(pattern.test('/logo/a.png')).toBe(false);
  });
});

describe('serverApi：服务端转发会话 Cookie（§24.3.2(c)）', () => {
  it('把 aragon_sid 转发给 API，并 no-store', async () => {
    vi.stubGlobal('window', undefined); // 服务端：没有 window
    cookieJar.value = 'tok123';
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ authenticated: false }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { serverApi } = await import('@/lib/serverApi');
    const api = await serverApi();
    await api.me();
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit & { cache?: string; next?: unknown }])[1];
    expect((init.headers as Record<string, string>).cookie).toBe('aragon_sid=tok123');
    expect(init.cache).toBe('no-store');
    expect(init.next).toBeUndefined();
  });

  it('没有 Cookie 就不带 cookie 头', async () => {
    vi.stubGlobal('window', undefined);
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { serverApi } = await import('@/lib/serverApi');
    await (await serverApi()).capabilities();
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>).cookie).toBeUndefined();
  });
});
