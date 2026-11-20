import type { Metadata } from 'next';

import { UsersTable } from '@/components/admin/UsersTable';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.users };

interface PageProps {
  searchParams: Promise<{ locked?: string | string[] }>;
}

/** `?locked=1`：概览“被锁定账号”卡的去向，还原成用户表顶部的可清除筛选（§25.3.2c）。 */
export default async function AdminUsersTablePage({ searchParams }: PageProps) {
  const params = await searchParams;
  return <UsersTable initialLocked={params.locked === '1'} />;
}
