import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AdminShell } from '@/components/admin/AdminShell';
import { AuthShell } from '@/components/auth/AuthShell';
import { copyAdmin } from '@/lib/copy.admin';
import { can } from '@/lib/permissions';
import { serverApi } from '@/lib/serverApi';
import type { Me } from '@/lib/types';

export const metadata: Metadata = { title: copyAdmin.title };
export const dynamic = 'force-dynamic';

const LINK_CLASS =
  'inline-flex h-8 items-center rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] px-3 text-[13px] font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-surface-3)]';

/**
 * `/admin/**` 的服务端守卫（§24.3.15b）：
 *
 * * guest（没有有效会话）→ `redirect('/login?next=/admin')`；
 * * 已登录但没有 `admin.overview.read`（普通 user）→ 渲染**403 视图**——**不是** 404，
 *   让人知道这是权限问题，而不是页面不存在；
 * * 强制改密态 → 先去改密。
 *
 * 这只是体验层的第一道门：每个 `/admin/*` 接口各自按会话与权限校验（默认拒绝的策略表），
 * 就算有人绕过这个布局，也读不到任何数据。
 */
export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const api = await serverApi();
  let me: Me | null = null;
  let failed = false;
  try {
    me = await api.me(2500);
  } catch {
    failed = true;
  }

  if (failed || !me) {
    // 认证服务没有响应 ≠ 没有权限：不重定向（会把管理员误弹去登录页），明说“暂时无法确认”。
    return (
      <AuthShell title={copyAdmin.unavailableTitle} subtitle={copyAdmin.unavailableBody} below={null}>
        <Link href="/admin" className={LINK_CLASS}>
          {copyAdmin.retry}
        </Link>
      </AuthShell>
    );
  }

  if (!me.authenticated) redirect('/login?next=/admin');
  if (me.user?.mustChangePassword) redirect('/account/password?forced=1&next=/admin');

  if (!can(me, 'admin.overview.read')) {
    return (
      <AuthShell title={copyAdmin.noAccessTitle} subtitle={copyAdmin.noAccessBody}>
        <div role="alert" data-testid="admin-forbidden" className="sr-only">
          403
        </div>
        <Link href="/" className={LINK_CLASS}>
          {copyAdmin.backHome}
        </Link>
      </AuthShell>
    );
  }

  return <AdminShell>{children}</AdminShell>;
}
