import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AuthShell } from '@/components/auth/AuthShell';
import { RecoverPageClient } from '@/components/auth/AuthPageClients';
import { FALLBACK_AUTH_CONFIG } from '@/components/auth/RegisterForm';
import { copyAuthPages } from '@/lib/copy.authPages';
import { serverApi } from '@/lib/serverApi';
import type { AuthConfig } from '@/lib/types';

export const metadata: Metadata = { title: copyAuthPages.recover.title };
export const dynamic = 'force-dynamic';

/**
 * `/recover`（spec §25.3.3）：公开；已登录的用户没有“找回”的必要，直接去账号页（那里可以改密 / 换恢复码）。
 * 密码规则来自 `GET /auth/config`；站点关闭自助找回时由表单渲染“联系管理员”面板。
 */
export default async function RecoverPage() {
  const api = await serverApi();
  const [me, config] = await Promise.all([
    api.me(2500).catch(() => null),
    api.authConfig().catch<AuthConfig>(() => FALLBACK_AUTH_CONFIG),
  ]);
  if (me?.authenticated) redirect('/account');

  return (
    <AuthShell
      title={copyAuthPages.recover.title}
      subtitle={copyAuthPages.recover.subtitle}
      below={
        <Link href="/" className="underline-offset-2 hover:text-[var(--text-secondary)] hover:underline">
          {copyAuthPages.login.browseAsGuest}
        </Link>
      }
    >
      <RecoverPageClient policy={config.passwordPolicy} disabled={config.recoveryEnabled === false} />
    </AuthShell>
  );
}
