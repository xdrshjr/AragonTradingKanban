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
};

export default nextConfig;
