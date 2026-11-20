import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AuthShell } from '@/components/auth/AuthShell';
import { LoginPageClient } from '@/components/auth/AuthPageClients';
import { copyAuthPages } from '@/lib/copy.authPages';
import { safeNext } from '@/lib/safeNext';
import { serverApi } from '@/lib/serverApi';

export const metadata: Metadata = { title: copyAuthPages.login.title };
export const dynamic = 'force-dynamic';

interface PageProps {
  searchParams: Promise<{ next?: string | string[]; recovered?: string | string[] }>;
}

/**
 * `/login`（spec §24.3.15b）：公开；已登录则直接跳 `next`（缺省 `/`）。
 * `?next=` 只允许站内相对路径（`safeNext`，开放重定向守卫）——不合格的值静默退回 `/`。
 */
export default async function LoginPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const next = safeNext(params.next, '/');

  const api = await serverApi();
  const [me, config] = await Promise.all([
    api.me(2500).catch(() => null),
    // 取不到配置时保守地显示入口（服务端关闭时恢复接口自会 403，页面给出联系管理员的话术）。
    api.authConfig().catch(() => null),
  ]);
  if (me?.authenticated) {
    if (me.user?.mustChangePassword) redirect(`/account/password?forced=1&next=${encodeURIComponent(next)}`);
    redirect(next);
  }

  return (
    <AuthShell
      title={copyAuthPages.login.title}
      subtitle={copyAuthPages.login.subtitle}
      below={
        <Link href="/" className="underline-offset-2 hover:text-[var(--text-secondary)] hover:underline">
          {copyAuthPages.login.browseAsGuest}
        </Link>
      }
    >
      <LoginPageClient
        next={next}
        recoveryEnabled={config?.recoveryEnabled !== false}
        recovered={params.recovered === '1'}
      />
    </AuthShell>
  );
}
