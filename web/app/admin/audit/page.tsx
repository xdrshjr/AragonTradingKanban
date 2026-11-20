import type { Metadata } from 'next';

import { AuditTable } from '@/components/admin/AuditTable';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.audit };

interface PageProps {
  searchParams: Promise<{ action?: string | string[] }>;
}

/** `?action=…`：概览“安全事件”点击后的去向；审计页据此还原动作筛选（未知动作忽略，不报错）。 */
export default async function AdminAuditTablePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const action = typeof params.action === 'string' ? params.action : undefined;
  return <AuditTable initialAction={action} />;
}
