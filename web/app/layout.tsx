import type { Metadata, Viewport } from 'next';

import { Providers } from '@/components/shell/Providers';
import { serverApi } from '@/lib/serverApi';
import { layoutBootstrapScript, themeBootstrapScript } from '@/lib/theme';
import type { Me } from '@/lib/types';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Aragon Research Terminal · 投研报告工作台',
    template: '%s · Aragon Research Terminal',
  },
  description:
    '一次生成、反复查看的投研报告工作台：结构化文档、K 线联动、版本化管理，AI 只在生成时消耗一次成本。',
  applicationName: 'Aragon Research Terminal',
  icons: {
    icon: [
      { url: '/brand/aragon-icon-32.png', sizes: '32x32' },
      { url: '/brand/aragon-icon-192.png', sizes: '192x192' },
    ],
    apple: [{ url: '/brand/aragon-apple-180.png', sizes: '180x180' }],
  },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: '#0b0c0e' },
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
  ],
};

/**
 * ★X6（spec §24.3.15a / R-A2）：服务端取一次 `Me`（转发 `aragon_sid`），作为 `['me']` 的 `initialData`。
 *
 * 为什么在布局里取：首屏 HTML 就带着正确的身份（用户名、额度芯片），不会"先游客后用户"地闪一下，
 * 也不会出现 SSR 与 hydrate 后数据不一致。**失败即降级**——API 冷启动、超时、5xx 一律当作"取不到"，
 * 传 `null`，站点按游客渲染并由客户端随后重试；认证接口的一次失败绝不能让整站白屏。
 * 超时刻意比通用的 8 s 短：这是每个页面渲染都要等的那一次请求。
 */
async function loadInitialMe(): Promise<Me | null> {
  try {
    const api = await serverApi();
    return await api.me(2500);
  } catch {
    return null;
  }
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const initialMe = await loadInitialMe();
  return (
    <html lang="zh-CN" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Both scripts run before paint: no theme flash, no grid jump (§6.3). */}
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
        <script dangerouslySetInnerHTML={{ __html: layoutBootstrapScript }} />
      </head>
      <body>
        <Providers initialMe={initialMe}>{children}</Providers>
      </body>
    </html>
  );
}
