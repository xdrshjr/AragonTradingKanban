import type { Metadata } from 'next';
import { redirect } from 'next/navigation';

import { AuthShell } from '@/components/auth/AuthShell';
import { ForcedPasswordClient } from '@/components/auth/AuthPageClients';
import { copyAuthPages } from '@/lib/copy.authPages';
import { safeNext } from '@/lib/safeNext';
import { serverApi } from '@/lib/serverApi';

export const metadata: Metadata = { title: copyAuthPages.changePassword.title };
export const dynamic = 'force-dynamic';

interface PageProps {
  searchParams: Promise<{ forced?: string | string[]; next?: string | string[] }>;
}

/**
 * `/account/password`：改密独立页。`?forced=1` 是**强制改密**态（初始 / 临时密码，B-3）：
 * 全屏、只有改密表单与退出登录，不显示站点其它出口——服务端此时对其它接口一律 `403 password_change_required`。
 */
export default async function PasswordPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const forcedParam = Array.isArray(params.forced) ? params.forced[0] : params.forced;
  const next = safeNext(params.next, '/account');

  const api = await serverApi();
  const me = await api.me(2500).catch(() => null);
  if (!me?.authenticated || !me.user) redirect(`/login?next=${encodeURIComponent('/account/password')}`);

  const forced = forcedParam === '1' || me.user.mustChangePassword;
  return (
    <AuthShell
      bare={forced}
      title={forced ? copyAuthPages.changePassword.forcedTitle : copyAuthPages.changePassword.title}
      subtitle={forced ? copyAuthPages.changePassword.forcedBody : undefined}
    >
      <ForcedPasswordClient username={me.user.username} next={next} forced={forced} />
    </AuthShell>
  );
}
