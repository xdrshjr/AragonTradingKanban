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

const CACHE_NAME = 'aragon-offline-v1';
/** 清理旧缓存时只碰自己的命名空间，绝不误删同源上别人的缓存。 */
const CACHE_PREFIX = 'aragon-offline-';
/** 预缓存的"应用壳"：落地页 HTML（离线打开 / 时至少有页面骨架）。 */
const SHELL_URL = '/';

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await self.caches.open(CACHE_NAME);
      try {
        // `cache: 'reload'` 绕过 HTTP 缓存，确保装进去的是这次部署的真实 HTML。
        await cache.add(new Request(SHELL_URL, { cache: 'reload' }));
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
    // 只缓存成功的同源 HTML；重定向后的结果由浏览器按 request 键复用。
    if (response && response.ok && response.type === 'basic') {
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
