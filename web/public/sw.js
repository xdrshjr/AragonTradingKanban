/* global self */
/**
 * 离线只读 service worker（AC-37 / AC-45 / §18.3.7 v4 修订 V4-P1-9）。
 *
 * 手写，不引 next-pwa：整份需求只有"让已经看过的报告在断网时还能打开"这一件事，
 * 一个 120 行的缓存策略比一个构建期插件更好读、更好排查，也不会在下次 Next 升级时炸掉。
 *
 * 三个策略（顺序即优先级）：
 *   ① request.mode === 'navigate'  → network-first，失败回缓存
 *      —— 缓存对象是 `/[symbol]` 的 **HTML**。RSC 直出，整页里本来就含报告正文，
 *         所以"断网打开已读过的报告有完整内容"成立，且**不依赖任何响应头**。
 *   ② /_next/static/*              → cache-first（文件名含内容哈希，天然不可变）
 *   ③ 其它（含跨源 API）           → 直接放行，不缓存、不改写
 *
 * 为什么**不**缓存跨源 API 响应（这是 v4 修掉的 P1）：
 *   - 默认 `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000` 与页面**不同源**，
 *     而本 SW 只接管同源请求，那条报告 API 请求根本到不了这里；
 *   - 首屏报告由 RSC **服务端** fetch，浏览器侧 SW 既看不见也拦不住；
 *   - 即便硬缓存，`no-cors` 响应连 body 都读不到，更不可能重建一个带
 *     `X-From-Cache` 头的 Response —— 于是"命中缓存"这件事无法自证。
 *   所以离线时跨源 API 就让它**如实失败**，前端走既有错误态（有明确提示，不静默挂起）。
 *
 * 非 GET（POST/PATCH/DELETE）一律放行：离线时它们会失败，前端给"当前离线，无法生成"
 * 的提示，而不是排进队列假装成功。
 */

// ★X6（§24.3.12）：v1 → v2。`activate` 会清掉所有 `aragon-offline-` 前缀下的旧名字，
// 所以升级后**多用户之前缓存的任何页面**都被一并丢弃（它们可能是某个人登录态下渲染的）。
const CACHE_NAME = 'aragon-offline-v2';
/** 清理旧缓存时只碰自己的命名空间，绝不误删同源上别人的缓存。 */
const CACHE_PREFIX = 'aragon-offline-';
/** 预缓存的"应用壳"：落地页 HTML（离线打开 / 时至少有页面骨架）。 */
const SHELL_URL = '/';

/**
 * ★X6（§24.3.12 / AC-204）：**缓存隔离**——本文件最重要的一段。
 *
 * 页面一旦在服务端渲染了"当前用户是谁"（用户名、额度芯片、自选），把它缓存下来，下一个用同一个
 * 浏览器的人离线时就会看到上一个人的页面。三道缺一不可：
 *   1. `middleware.ts` 给每个页面响应打 `X-Aragon-Viewer: guest|user`（只看 `aragon_sid` Cookie 是否存在）；
 *   2. 本 SW **只缓存 `guest` 的导航响应**；登录态响应只走网络，不落缓存；
 *   3. 登出 / 会话失效时前端发 `purge-user-cache`，这里清空导航缓存（`/_next/static` 的不可变资源保留）。
 * 路径黑名单是纵深防御：就算头丢了，账号/后台/登录页也绝不入缓存。
 */
const VIEWER_HEADER = 'X-Aragon-Viewer';
const PURGE_MESSAGE = 'purge-user-cache';
const NEVER_CACHE_PREFIXES = ['/login', '/register', '/recover', '/account', '/admin'];

function isNeverCachePath(pathname) {
  return NEVER_CACHE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix + '/'),
  );
}

/** 这份导航响应能不能写缓存？只有：成功、同源 basic、路径不在黑名单、且明确标了 `guest`。 */
function shouldCacheNavigation(pathname, response) {
  if (!response || !response.ok || response.type !== 'basic') return false;
  if (isNeverCachePath(pathname)) return false;
  const viewer = response.headers && response.headers.get(VIEWER_HEADER);
  return viewer === 'guest';
}

/** 清导航缓存：删除**非** `/_next/static/` 的全部条目。返回删除条数（测试用）。 */
async function purgeNavigationEntries(cache) {
  const requests = await cache.keys();
  let removed = 0;
  for (const request of requests) {
    const { pathname } = new URL(request.url);
    if (pathname.startsWith('/_next/static/')) continue;
    if (await cache.delete(request)) removed += 1;
  }
  return removed;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await self.caches.open(CACHE_NAME);
      try {
        // `cache: 'reload'` 绕过 HTTP 缓存，确保装进去的是这次部署的真实 HTML。
        // ★X6：安装期的 fetch 会带上浏览器当前的 Cookie——如果此刻已登录，拿到的是**某个人的**首页。
        // 所以不能用 `cache.add()`：先看 `X-Aragon-Viewer`，只有 guest 才入缓存。
        const response = await fetch(new Request(SHELL_URL, { cache: 'reload' }));
        if (shouldCacheNavigation(SHELL_URL, response)) await cache.put(SHELL_URL, response);
      } catch {
        // 壳没拿到不算安装失败：导航请求是 network-first，下次联网会补上。
        // 这里若 reject，整个 SW 都不会激活，离线能力直接归零。
      }
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await self.caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
          .map((name) => self.caches.delete(name)),
      );
      // 立即接管已打开的标签页，避免"新版已部署但旧 SW 还在服务"。
      await self.clients.claim();
    })(),
  );
});

/** 断网且没缓存时的兜底页：明确告知，而不是白屏或无限 loading（AC-45）。 */
function offlineNotice() {
  const html = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>当前离线 · Aragon</title>
<style>
  html,body{margin:0;height:100%;background:#0b0c0e;color:#edeef0;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  main{max-width:34rem;margin:0 auto;padding:14vh 1.5rem;line-height:1.7}
  h1{font-size:1.125rem;margin:0 0 .75rem}
  p{margin:.25rem 0;color:#a0a5ad}
  strong{color:#e8b64c}
</style></head>
<body><main role="main">
  <h1><strong>离线副本不可用</strong></h1>
  <p>这份报告还没有离线副本。</p>
  <p>联网后打开过一次，就会自动缓存到本地。</p>
  <p>已读过的报告仍可查看；生成、追问与检索需要联网。</p>
</main></body></html>`;
  return new Response(html, {
    status: 503,
    statusText: 'Offline',
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
  });
}

/** ① 导航请求：network-first，失败回缓存（缓存里就是那份完整的 `/[symbol]` HTML）。 */
async function handleNavigate(request) {
  const cache = await self.caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    // ★X6：只缓存成功的同源 **guest** HTML（`shouldCacheNavigation`）；登录态响应只走网络。
    if (shouldCacheNavigation(new URL(request.url).pathname, response)) {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    // 也试一次"忽略查询串"的匹配：/600519.SH?tab=risks 与 /600519.SH 是一份文档。
    const fallback = await cache.match(new URL(request.url).pathname);
    if (fallback) return fallback;
    return offlineNotice();
  }
}

/** ② `/_next/static/*`：cache-first，命中即返回，未命中才联网并写缓存。 */
async function handleStatic(request) {
  const cache = await self.caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.ok) {
    cache.put(request, response.clone()).catch(() => {});
  }
  return response;
}

// 登出 / 会话失效：前端 `purgeUserCache()` 发来的消息。
self.addEventListener('message', (event) => {
  if (!event.data || event.data.type !== PURGE_MESSAGE) return;
  event.waitUntil(
    (async () => {
      const names = await self.caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX))
          .map(async (name) => purgeNavigationEntries(await self.caches.open(name))),
      );
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // 非 GET：不缓存、不排队、不改写 —— 离线时如实失败。
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  // 只接管同源请求；跨源（含默认的 http://127.0.0.1:8000 API）彻底放行。
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(handleNavigate(request));
    return;
  }

  if (url.pathname.startsWith('/_next/static/')) {
    event.respondWith(handleStatic(request));
    return;
  }

  // ③ 其余同源请求（含 /api/* 若有、导出、SSE）：直接走网络，不缓存。
  //    离线时由前端错误态兜底，本 SW 不伪造响应。
});

// 仅供单元测试（`tests/sw-isolation.test.ts`）：浏览器的 Worker 里没有 `module`，这一段是 no-op。
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    CACHE_NAME,
    CACHE_PREFIX,
    VIEWER_HEADER,
    PURGE_MESSAGE,
    NEVER_CACHE_PREFIXES,
    isNeverCachePath,
    shouldCacheNavigation,
    purgeNavigationEntries,
  };
}
