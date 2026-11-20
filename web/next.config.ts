import type { NextConfig } from 'next';

const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: true },
  // The repo has two lockfiles (root workspace + web). Pin the tracing root so
  // Next does not guess and emit a warning on every build.
  outputFileTracingRoot: process.cwd(),
  // Same-origin deploy (optional): with ENABLE_PY_REWRITE=1 the browser talks to
  // Next and Next proxies to FastAPI, which keeps SSE on one origin behind
  // restrictive networks. Default is a direct CORS call to the API.
  async rewrites() {
    if (process.env.ENABLE_PY_REWRITE !== '1') return [];
    return [{ source: '/py/:path*', destination: `${apiBase}/:path*` }];
  },
  // The service worker must never be served stale: a cached `sw.js` would pin every
  // visitor to an old cache name forever, and no amount of new deploys would fix it
  // (A5 / §18.4.5).
  //
  // ★X6（§24.4.2）：站点级安全响应头。页面渲染着"当前用户"，不应被嵌进别人的 iframe
  // （点击劫持），也不应把带路径的 Referer 发给第三方。`X-Frame-Options` 之外不设 CSP：
  // 现有页面有内联 bootstrap 脚本（主题 / 布局防闪烁），收紧 CSP 是另一项独立的工作。
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'same-origin' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
        ],
      },
      {
        source: '/sw.js',
        headers: [
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
          { key: 'Service-Worker-Allowed', value: '/' },
        ],
      },
    ];
  },
};

export default nextConfig;
