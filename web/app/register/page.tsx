import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AuthShell } from '@/components/auth/AuthShell';
import { RegisterPageClient } from '@/components/auth/AuthPageClients';
import { FALLBACK_AUTH_CONFIG } from '@/components/auth/RegisterForm';
import { copyAuthPages } from '@/lib/copy.authPages';
import { safeNext } from '@/lib/safeNext';
import { serverApi } from '@/lib/serverApi';
import type { AuthConfig } from '@/lib/types';

export const metadata: Metadata = { title: copyAuthPages.register.title };
export const dynamic = 'force-dynamic';

interface PageProps {
  searchParams: Promise<{ next?: string | string[] }>;
}

/**
 * `/register`：公开；`registrationMode=closed` 时由表单渲染“注册已关闭”与联系管理员提示。
 * 规则（用户名 / 密码 / 模式）来自 `GET /auth/config`——前端不写死第二份；取不到时按服务端默认兜底，
 * 最终裁决仍在服务端。
 */
export default async function RegisterPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const next = safeNext(params.next, '/');

  const api = await serverApi();
  const [me, config] = await Promise.all([
    api.me(2500).catch(() => null),
    api.authConfig().catch<AuthConfig>(() => FALLBACK_AUTH_CONFIG),
  ]);
  if (me?.authenticated) redirect(next);

  return (
    <AuthShell
      title={copyAuthPages.register.title}
      subtitle={copyAuthPages.register.subtitle}
      below={
        <Link href="/" className="underline-offset-2 hover:text-[var(--text-secondary)] hover:underline">
          {copyAuthPages.login.browseAsGuest}
        </Link>
      }
    >
      <RegisterPageClient config={config} next={next} />
    </AuthShell>
  );
}
