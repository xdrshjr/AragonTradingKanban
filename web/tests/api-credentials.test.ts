import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, sep } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { alignLoopbackHost, apiBase, ApiError, api, createApi } from '@/lib/api';
import { adminApi } from '@/lib/api.admin';
import { authEventType } from '@/lib/authEvents';

/**
 * AC-206（RV-1）+ §24.3.2：浏览器带凭据、服务端转发 Cookie 且 no-store、回环别名对齐、
 * 令牌不下发浏览器。fetch 全部是 mock，不依赖后端。
 */

const ROOT = process.cwd();
const TOKEN_NAME = ['NEXT_PUBLIC', 'API', 'TOKEN'].join('_');

function walk(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name.startsWith('.')) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

describe('AC-206①：令牌不进浏览器可达的源码', () => {
  it('web/lib、web/components、web/app 对该公开环境变量的引用数为 0', () => {
    let hits = 0;
    const offenders: string[] = [];
    for (const dir of ['lib', 'components', 'app']) {
      for (const file of walk(join(ROOT, dir))) {
        if (!/\.(ts|tsx|js|mjs)$/.test(file)) continue;
        const count = readFileSync(file, 'utf8').split(TOKEN_NAME).length - 1;
        if (count > 0) offenders.push(file);
        hits += count;
      }
    }
    expect(offenders).toEqual([]);
    expect(hits).toBe(0);
  });

  // ★Y2(d)：admin 客户端拆到 `lib/api.admin.ts`——凭据面必须与 `api.ts` 一致（AC-206 扫描同时覆盖新文件）。
  it.each(['api.ts', 'api.admin.ts'])('%s 不导出 authHeaders，也不拼 Authorization 头', (name) => {
    const source = readFileSync(join(ROOT, 'lib', name), 'utf8');
    expect(source).not.toMatch(/authHeaders/);
    expect(source).not.toMatch(/authorization\s*:/i);
    expect(source).not.toMatch(/Bearer\s*\$\{/);
  });

  it('AC-216：api.admin 只被 components/admin 与 app/admin 引用（首屏代码不 import 它）', () => {
    const importers: string[] = [];
    for (const dir of ['lib', 'components', 'app']) {
      for (const file of walk(join(ROOT, dir))) {
        if (!/\.(ts|tsx)$/.test(file)) continue;
        if (/api\.admin\.ts$/.test(file)) continue;
        if (/['"]@\/lib\/api\.admin['"]|['"]\.\/api\.admin['"]/.test(readFileSync(file, 'utf8'))) {
          importers.push(file.replace(ROOT, '').split(sep).join('/'));
        }
      }
    }
    expect(importers.length).toBeGreaterThan(0);
    for (const file of importers) {
      expect(file, `${file} 不应在首屏路径里 import api.admin`).toMatch(/^\/(components\/admin\/|app\/admin\/)/);
    }
  });

  it('AC-216：已存在的 next build 产物里，/[symbol] 的首屏 chunk 不含任何 admin 端点（没有构建产物时跳过）', (ctx) => {
    const manifestPath = join(ROOT, '.next', 'app-build-manifest.json');
    if (!existsSync(manifestPath)) return ctx.skip();
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { pages: Record<string, string[]> };
    const files = manifest.pages['/[symbol]/page'];
    if (!files) return ctx.skip();
    const offenders = files
      .filter((file) => file.endsWith('.js'))
      .filter((file) => {
        const text = readFileSync(join(ROOT, '.next', file), 'utf8');
        return text.includes('/admin/users/bulk-quota') || text.includes('export.csv') || text.includes('/admin/overview');
      });
    expect(offenders).toEqual([]);
  });

  it('AC-206②：已存在的 next build 产物 .next/static 不含该字符串（没有构建产物时跳过）', (ctx) => {
    const staticDir = join(ROOT, '.next', 'static');
    if (!existsSync(staticDir)) return ctx.skip();
    const offenders = walk(staticDir)
      .filter((file) => /\.js$/.test(file))
      .filter((file) => readFileSync(file, 'utf8').includes(TOKEN_NAME));
    expect(offenders).toEqual([]);
  });
});

/* ── fetch 夹具 ──────────────────────────────────────────────────────────── */

function okJson(body: unknown = {}) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
}

function errJson(status: number, code: string, detail: Record<string, unknown> = {}) {
  return new Response(JSON.stringify({ error: { code, message: 'm', requestId: 'r', detail } }), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => okJson({ items: [] }));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function lastInit(): RequestInit & { next?: unknown; cache?: string } {
  return fetchMock.mock.calls.at(-1)![1] as RequestInit & { next?: unknown; cache?: string };
}

describe('浏览器侧 request()', () => {
  it("带 credentials: 'include'，且没有 Authorization 头", async () => {
    await api.watchlist();
    const init = lastInit();
    expect(init.credentials).toBe('include');
    const headers = (init.headers ?? {}) as Record<string, string>;
    expect(Object.keys(headers).map((key) => key.toLowerCase())).not.toContain('authorization');
    expect(Object.keys(headers).map((key) => key.toLowerCase())).not.toContain('cookie');
  });

  it('POST 带 content-type 与 JSON 体', async () => {
    await api.login({ username: 'u', password: 'p' });
    const init = lastInit();
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['content-type']).toBe('application/json');
    expect(JSON.parse(init.body as string)).toEqual({ username: 'u', password: 'p' });
  });

  it('浏览器侧保留 revalidate 语义（next 选项），不强加 no-store', async () => {
    await api.capabilities(30);
    expect(lastInit().next).toEqual({ revalidate: 30 });
    expect(lastInit().cache).toBeUndefined();
  });

  it('createApi({cookie}) 在浏览器里**不**手写 Cookie 头（浏览器自己带）', async () => {
    await createApi({ cookie: 'aragon_sid=abc' }).me();
    expect(((lastInit().headers ?? {}) as Record<string, string>).cookie).toBeUndefined();
  });
});

describe('服务端实例（RV-6：脱敏响应不得进 Next Data Cache）', () => {
  beforeEach(() => {
    // `typeof window === 'undefined'` 即服务端。
    vi.stubGlobal('window', undefined);
  });

  it('转发 aragon_sid，一律 cache: no-store，且**不含** next.revalidate', async () => {
    const server = createApi({ cookie: 'aragon_sid=x' });
    const calls: Array<() => Promise<unknown>> = [
      () => server.capabilities(30),
      () => server.probeStatus(60),
      () => server.watchlist(),
      () => server.me(),
      () => server.reportLibrary({ scope: 'latest' }),
      () => server.latestReport('CN:600519.SH'),
    ];
    for (const call of calls) {
      await call();
      const init = lastInit();
      expect(init.cache).toBe('no-store');
      expect(init.next).toBeUndefined();
      expect((init.headers as Record<string, string>).cookie).toBe('aragon_sid=x');
      expect(init.credentials).toBeUndefined();
    }
  });

  it('没有 Cookie 时不带 cookie 头（游客渲染）', async () => {
    await createApi({}).me();
    expect((lastInit().headers as Record<string, string>).cookie).toBeUndefined();
  });

  it('服务端不广播 401 事件（它没有 window）', async () => {
    fetchMock.mockResolvedValueOnce(errJson(401, 'unauthorized', { reason: 'expired' }));
    await expect(createApi({}).watchlist()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('回环别名对齐（§24.3.2(d)，六种组合）', () => {
  it.each([
    ['http://127.0.0.1:8000', 'localhost', 'http://localhost:8000'],
    ['http://localhost:8000', '127.0.0.1', 'http://127.0.0.1:8000'],
    ['http://127.0.0.1:8000', '[::1]', 'http://[::1]:8000'],
    ['http://[::1]:8000', 'localhost', 'http://localhost:8000'],
    ['http://localhost:8000', '[::1]', 'http://[::1]:8000'],
    ['http://[::1]:8000', '127.0.0.1', 'http://127.0.0.1:8000'],
  ])('API %s + 页面 %s → %s', (base, page, expected) => {
    expect(alignLoopbackHost(base, page)).toBe(expected);
  });

  it('同主机、非回环（生产同源）、无页面主机：原样返回', () => {
    expect(alignLoopbackHost('http://127.0.0.1:8000', '127.0.0.1')).toBe('http://127.0.0.1:8000');
    expect(alignLoopbackHost('http://127.0.0.1:8000', 'aragontrading.online')).toBe('http://127.0.0.1:8000');
    expect(alignLoopbackHost('https://api.example.com', 'localhost')).toBe('https://api.example.com');
    expect(alignLoopbackHost('http://127.0.0.1:8000', undefined)).toBe('http://127.0.0.1:8000');
    expect(alignLoopbackHost('not a url', 'localhost')).toBe('not a url');
  });

  it('保留端口、协议与路径前缀', () => {
    expect(alignLoopbackHost('https://127.0.0.1:8443/prefix', 'localhost')).toBe('https://localhost:8443/prefix');
  });

  it('apiBase() 在浏览器里按 location.hostname 对齐（jsdom 的页面主机是 localhost）', () => {
    expect(apiBase()).toBe('http://localhost:8000');
  });
});

describe('全局认证事件（§24.3.15a）', () => {
  function listen(name: 'unauthorized' | 'password-change-required') {
    const handler = vi.fn();
    window.addEventListener(authEventType(name), handler);
    return { handler, off: () => window.removeEventListener(authEventType(name), handler) };
  }

  it('401 unauthorized 且 reason ≠ no_session ⇒ 广播“会话失效”', async () => {
    const { handler, off } = listen('unauthorized');
    fetchMock.mockResolvedValueOnce(errJson(401, 'unauthorized', { reason: 'expired' }));
    await expect(api.watchlist()).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    off();
  });

  it('reason=no_session（本来就没登录）不广播——那是调用方自己的分支', async () => {
    const { handler, off } = listen('unauthorized');
    fetchMock.mockResolvedValueOnce(errJson(401, 'unauthorized', { reason: 'no_session' }));
    await expect(api.watchlist()).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
    off();
  });

  it('登录失败的 401 invalid_credentials 不广播', async () => {
    const { handler, off } = listen('unauthorized');
    fetchMock.mockResolvedValueOnce(errJson(401, 'invalid_credentials'));
    await expect(api.login({ username: 'a', password: 'b' })).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
    off();
  });

  it('403 password_change_required ⇒ 广播强制改密', async () => {
    const { handler, off } = listen('password-change-required');
    fetchMock.mockResolvedValueOnce(errJson(403, 'password_change_required'));
    await expect(api.watchlist()).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    off();
  });
});

describe('端点表（M52 复用）', () => {
  it('admin / quota / auth 端点的方法与路径', async () => {
    const calls: Array<[() => Promise<unknown>, string, string]> = [
      [() => adminApi.adminOverview(), 'GET', '/api/v1/admin/overview'],
      [() => adminApi.adminUsers({ q: 'ab', role: 'user', limit: 20 }), 'GET', '/api/v1/admin/users?q=ab&role=user&limit=20'],
      [() => adminApi.adminCreateUser({ username: 'bob' }), 'POST', '/api/v1/admin/users'],
      [() => adminApi.adminPatchUser('u1', { status: 'disabled' }), 'PATCH', '/api/v1/admin/users/u1'],
      [() => adminApi.adminResetPassword('u1'), 'POST', '/api/v1/admin/users/u1/reset-password'],
      [() => adminApi.adminRevokeSessions('u1'), 'POST', '/api/v1/admin/users/u1/revoke-sessions'],
      [() => adminApi.adminUnlockUser('u1'), 'POST', '/api/v1/admin/users/u1/unlock'],
      [() => adminApi.adminAdjustQuota('u1', { grant: 3 }), 'POST', '/api/v1/admin/users/u1/quota'],
      [() => adminApi.adminDeleteUser('u1', 'bob'), 'DELETE', '/api/v1/admin/users/u1?confirm=bob'],
      [() => adminApi.adminInvites(), 'GET', '/api/v1/admin/invites'],
      [() => adminApi.adminRevokeInvite('i1'), 'DELETE', '/api/v1/admin/invites/i1'],
      [() => adminApi.adminQuotaRequests('pending'), 'GET', '/api/v1/admin/quota-requests?status=pending'],
      [() => adminApi.adminResolveQuotaRequest('q1', { decision: 'approve', grant: 3 }), 'POST', '/api/v1/admin/quota-requests/q1/resolve'],
      [() => adminApi.adminAudit({ action: 'auth.login.success' }), 'GET', '/api/v1/admin/audit?action=auth.login.success&limit=50'],
      [() => adminApi.adminSettings(), 'GET', '/api/v1/admin/settings'],
      [() => adminApi.adminPatchSettings({ siteDailyCap: 10 }), 'PATCH', '/api/v1/admin/settings'],
      [() => adminApi.adminFeatured(), 'GET', '/api/v1/admin/featured-watchlist'],
      [() => adminApi.adminRemoveFeatured('CN:600519.SH'), 'DELETE', '/api/v1/admin/featured-watchlist/CN%3A600519.SH'],
      [() => api.myQuota(), 'GET', '/api/v1/me/quota'],
      [() => api.quotaLedger({ limit: 50 }), 'GET', '/api/v1/me/quota-ledger?limit=50'],
      [() => api.createQuotaRequest({ resource: 'report', amount: 3, reason: 'x' }), 'POST', '/api/v1/me/quota-requests'],
      [() => api.sessions(), 'GET', '/api/v1/auth/sessions'],
      [() => api.revokeOtherSessions(), 'POST', '/api/v1/auth/sessions/revoke-others'],
      [() => api.changePassword({ currentPassword: 'a', newPassword: 'b' }), 'POST', '/api/v1/auth/password'],
      [() => api.captcha('register'), 'GET', '/api/v1/auth/captcha?purpose=register'],
      [() => api.logout(), 'POST', '/api/v1/auth/logout'],
      // ★Y2–Y5（§25.5）
      [() => api.captcha('recover'), 'GET', '/api/v1/auth/captcha?purpose=recover'],
      [
        () => api.recover({ username: 'a', recoveryKey: 'K', newPassword: 'p', captchaId: 'c', captchaAnswer: 'x' }),
        'POST',
        '/api/v1/auth/recover',
      ],
      [() => api.rotateRecoveryKey('pw'), 'POST', '/api/v1/auth/recovery-key'],
      [() => api.deleteAccount({ password: 'pw', confirm: 'alice' }), 'DELETE', '/api/v1/auth/account'],
      [() => adminApi.adminBulkQuota({ userIds: ['u1'], grant: 5 }), 'POST', '/api/v1/admin/users/bulk-quota'],
      [() => adminApi.adminUsers({ locked: true }), 'GET', '/api/v1/admin/users?locked=1&limit=50'],
    ];
    for (const [call, method, path] of calls) {
      fetchMock.mockClear();
      fetchMock.mockResolvedValueOnce(okJson({}));
      await call();
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(init.method, path).toBe(method);
      expect(url.endsWith(path), `${url} should end with ${path}`).toBe(true);
    }
  });

  it('请求体：recover / deleteAccount / bulk-quota 的 JSON 形状（recoveryKey 只出现在请求里）', async () => {
    fetchMock.mockImplementation(async () => okJson({}));
    await api.recover({ username: 'a', recoveryKey: 'K', newPassword: 'p', captchaId: 'c', captchaAnswer: 'x' });
    await api.deleteAccount({ password: 'pw', confirm: 'alice' });
    await adminApi.adminBulkQuota({ userIds: ['u1', 'u2'], grant: -5, note: 'n' });
    const bodies = fetchMock.mock.calls.map((call) => JSON.parse(String((call[1] as RequestInit).body)));
    expect(bodies[0]).toEqual({ username: 'a', recoveryKey: 'K', newPassword: 'p', captchaId: 'c', captchaAnswer: 'x' });
    expect(bodies[1]).toEqual({ password: 'pw', confirm: 'alice' });
    expect(bodies[2]).toEqual({ userIds: ['u1', 'u2'], grant: -5, note: 'n' });
  });

  it('204 不解析 body（logout 幂等）', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.logout()).resolves.toBeUndefined();
  });
});
