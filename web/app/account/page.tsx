import type { Metadata } from 'next';
import { redirect } from 'next/navigation';

import { AccountClient } from '@/components/auth/AccountClient';
import { copyAuthPages } from '@/lib/copy.authPages';
import { serverApi } from '@/lib/serverApi';

export const metadata: Metadata = { title: copyAuthPages.account.title };
export const dynamic = 'force-dynamic';

/**
 * `/account`：登录后可见。`middleware.ts` 已在“没有 Cookie”时挡掉；这里再按**有效会话**校验一次
 * （Cookie 存在但已失效 → 回登录页并带上 `next`）。授权不依赖前端——接口各自按会话鉴权。
 */
export default async function AccountPage() {
  const api = await serverApi();
  const me = await api.me(2500).catch(() => null);
  if (!me?.authenticated) redirect('/login?next=/account');
  if (me.user?.mustChangePassword) redirect('/account/password?forced=1&next=/account');
  return <AccountClient />;
}
