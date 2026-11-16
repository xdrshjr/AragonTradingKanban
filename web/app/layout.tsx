import type { Metadata, Viewport } from 'next';

import { Providers } from '@/components/shell/Providers';
import { layoutBootstrapScript, themeBootstrapScript } from '@/lib/theme';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Aragon Research Terminal · 投研报告工作台',
    template: '%s · Aragon Research Terminal',
  },
  description:
    '一次生成、反复查看的投研报告工作台：结构化文档、K 线联动、版本化管理，AI 只在生成时消耗一次成本。',
  applicationName: 'Aragon Research Terminal',
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Both scripts run before paint: no theme flash, no grid jump (§6.3). */}
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
        <script dangerouslySetInnerHTML={{ __html: layoutBootstrapScript }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
