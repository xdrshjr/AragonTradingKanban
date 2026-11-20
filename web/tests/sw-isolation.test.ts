import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PURGE_USER_CACHE_MESSAGE, purgeUserCache } from '@/lib/offline';

/**
 * AC-204 / §24.3.12 / E-11：离线 SW 的缓存隔离。
 *
 * 对 `public/sw.js` 的**决策函数**做单测（它在 Worker 里是普通脚本，末尾用 `module.exports` 仅供测试导出），
 * 再用一个假的 `self` 驱动 install / fetch / message 三个事件，证明：
 *   1. 只有 `X-Aragon-Viewer: guest` 的导航响应才写缓存；
 *   2. /login /register /account /admin 永不缓存（路径黑名单，纵深防御）；
 *   3. 收到 `purge-user-cache` 后清空导航缓存，`/_next/static` 的不可变资源保留；
 *   4. 缓存名升到 v2，activate 清掉旧名字。
 */

const SOURCE = readFileSync(join(process.cwd(), 'public', 'sw.js'), 'utf8');

type Listener = (event: Record<string, unknown>) => void;

class FakeCache {
  store = new Map<string, Response>();
  async put(key: string | Request, response: Response) {
    this.store.set(typeof key === 'string' ? new URL(key, 'https://app.test').href : key.url, response);
  }
  async match(key: string | Request) {
    return this.store.get(typeof key === 'string' ? new URL(key, 'https://app.test').href : key.url);
  }
  async keys() {
    return [...this.store.keys()].map((url) => new Request(url));
  }
  async delete(key: Request | string) {
    return this.store.delete(typeof key === 'string' ? new URL(key, 'https://app.test').href : key.url);
  }
}

function load() {
  const listeners: Record<string, Listener> = {};
  const caches = new Map<string, FakeCache>();
  const fakeCaches = {
    async open(name: string) {
      if (!caches.has(name)) caches.set(name, new FakeCache());
      return caches.get(name)!;
    },
    async keys() {
      return [...caches.keys()];
    },
    async delete(name: string) {
      return caches.delete(name);
    },
  };
  const self = {
    addEventListener: (type: string, fn: Listener) => {
      listeners[type] = fn;
    },
    caches: fakeCaches,
    location: { origin: 'https://app.test' },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };
  const mod = { exports: {} as Record<string, unknown> };
  new Function('self', 'module', SOURCE)(self, mod);
  return { listeners, caches, self, api: mod.exports as any };
}

function page(viewer: string | null, status = 200, type = 'basic'): Response {
  const headers: Record<string, string> = { 'content-type': 'text/html' };
  if (viewer) headers['X-Aragon-Viewer'] = viewer;
  const response = new Response('<html></html>', { status, headers });
  // `new Response()` 的 type 是 'default'；SW 只信同源 basic 响应。
  Object.defineProperty(response, 'type', { value: type, configurable: true });
  return response;
}

describe('sw.js 缓存决策函数', () => {
  const { api } = load();

  it('缓存名 v2；命名空间前缀不变（activate 才清得掉 v1）', () => {
    expect(api.CACHE_NAME).toBe('aragon-offline-v2');
    expect(api.CACHE_PREFIX).toBe('aragon-offline-');
  });

  it.each([
    ['guest', true],
    ['user', false],
    ['', false],
    [null, false],
    ['Guest', false],
  ])('X-Aragon-Viewer=%s ⇒ 写缓存=%s', (viewer, expected) => {
    expect(api.shouldCacheNavigation('/600519.SH', page(viewer as string | null))).toBe(expected);
  });

  it.each(['/login', '/register', '/account', '/account/password', '/admin', '/admin/users'])(
    '路径黑名单：%s 即便标了 guest 也不缓存',
    (pathname) => {
      expect(api.isNeverCachePath(pathname)).toBe(true);
      expect(api.shouldCacheNavigation(pathname, page('guest'))).toBe(false);
    },
  );

  it('黑名单是前缀+边界：/login-help、/administrator 不被误伤', () => {
    expect(api.isNeverCachePath('/login-help')).toBe(false);
    expect(api.isNeverCachePath('/administrator')).toBe(false);
    expect(api.shouldCacheNavigation('/reports', page('guest'))).toBe(true);
  });

  it('非 2xx、非 basic 响应不缓存', () => {
    expect(api.shouldCacheNavigation('/x', page('guest', 500))).toBe(false);
    expect(api.shouldCacheNavigation('/x', page('guest', 200, 'cors'))).toBe(false);
    expect(api.shouldCacheNavigation('/x', null)).toBe(false);
  });

  it('purgeNavigationEntries：删导航条目，保留 /_next/static', async () => {
    const cache = new FakeCache();
    await cache.put('/600519.SH', page('guest'));
    await cache.put('/', page('guest'));
    await cache.put('/_next/static/chunks/app.js', new Response('js'));
    const removed = await api.purgeNavigationEntries(cache);
    expect(removed).toBe(2);
    expect([...cache.store.keys()]).toEqual(['https://app.test/_next/static/chunks/app.js']);
  });
});

describe('sw.js 事件：fetch / install / message / activate', () => {
  let sw: ReturnType<typeof load>;
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    // Node 的 `Request` 不接受相对 URL（真实 SW 里相对于 scope）：测试里补一个基准。
    const NativeRequest = Request;
    vi.stubGlobal(
      'Request',
      class extends NativeRequest {
        constructor(input: RequestInfo | URL, init?: RequestInit) {
          super(typeof input === 'string' && input.startsWith('/') ? `https://app.test${input}` : input, init);
        }
      },
    );
    sw = load();
  });

  async function navigate(path: string): Promise<Response> {
    let responded: Promise<Response> | null = null;
    const request = new Request(`https://app.test${path}`);
    Object.defineProperty(request, 'mode', { value: 'navigate' });
    sw.listeners.fetch!({ request, respondWith: (p: Promise<Response>) => (responded = p) });
    expect(responded).not.toBeNull();
    return responded!;
  }

  async function flush() {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  it('guest 的导航响应被缓存，且断网后能从缓存读回', async () => {
    fetchMock.mockResolvedValueOnce(page('guest'));
    await navigate('/600519.SH');
    await flush();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    const offline = await navigate('/600519.SH');
    expect(offline.status).toBe(200);
  });

  it('登录态（user）的导航响应**不**写缓存：断网后得到“离线副本不可用”而不是上一个人的页面', async () => {
    fetchMock.mockResolvedValueOnce(page('user'));
    await navigate('/600519.SH');
    await flush();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    const offline = await navigate('/600519.SH');
    expect(offline.status).toBe(503);
  });

  it('install 预缓存壳时同样只收 guest（安装期 fetch 会带上当前 Cookie）', async () => {
    fetchMock.mockResolvedValueOnce(page('user'));
    let done: Promise<unknown> = Promise.resolve();
    sw.listeners.install!({ waitUntil: (p: Promise<unknown>) => (done = p) });
    await done;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const cache = sw.caches.get('aragon-offline-v2');
    expect(cache?.store.size ?? 0).toBe(0);

    fetchMock.mockResolvedValueOnce(page('guest'));
    sw.listeners.install!({ waitUntil: (p: Promise<unknown>) => (done = p) });
    await done;
    expect(sw.caches.get('aragon-offline-v2')!.store.size).toBe(1);
  });

  it('purge-user-cache：清空导航缓存，保留静态资源；其它消息忽略', async () => {
    const cache = await sw.self.caches.open('aragon-offline-v2');
    await cache.put('/600519.SH', page('guest'));
    await cache.put('/_next/static/a.js', new Response('x'));

    let done: Promise<unknown> = Promise.resolve();
    sw.listeners.message!({ data: { type: 'something-else' }, waitUntil: (p: Promise<unknown>) => (done = p) });
    await done;
    expect(cache.store.size).toBe(2);

    sw.listeners.message!({ data: { type: 'purge-user-cache' }, waitUntil: (p: Promise<unknown>) => (done = p) });
    await done;
    expect([...cache.store.keys()]).toEqual(['https://app.test/_next/static/a.js']);
  });

  it('activate 清掉 v1（以及其它旧名字），不碰别人的缓存', async () => {
    await sw.self.caches.open('aragon-offline-v1');
    await sw.self.caches.open('aragon-offline-v2');
    await sw.self.caches.open('someone-elses-cache');
    let done: Promise<unknown> = Promise.resolve();
    sw.listeners.activate!({ waitUntil: (p: Promise<unknown>) => (done = p) });
    await done;
    expect([...sw.caches.keys()].sort()).toEqual(['aragon-offline-v2', 'someone-elses-cache']);
  });
});

describe('lib/offline.purgeUserCache（前端侧）', () => {
  it('消息名与 sw.js 共用同一个字面量', () => {
    expect(PURGE_USER_CACHE_MESSAGE).toBe('purge-user-cache');
    expect(SOURCE).toContain(`'${PURGE_USER_CACHE_MESSAGE}'`);
  });

  it('有 SW 控制页面时 postMessage；没有时是 no-op', () => {
    const postMessage = vi.fn();
    Object.defineProperty(navigator, 'serviceWorker', { value: { controller: { postMessage } }, configurable: true });
    purgeUserCache();
    expect(postMessage).toHaveBeenCalledWith({ type: 'purge-user-cache' });

    Object.defineProperty(navigator, 'serviceWorker', { value: { controller: null }, configurable: true });
    expect(() => purgeUserCache()).not.toThrow();
    Object.defineProperty(navigator, 'serviceWorker', { value: undefined, configurable: true });
    expect(() => purgeUserCache()).not.toThrow();
  });
});
