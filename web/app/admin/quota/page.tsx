import type { Metadata } from 'next';

import { QuotaRequestsPanel } from '@/components/admin/QuotaRequestsPanel';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.quota };

export default function AdminQuotaRequestsPanelPage() {
  return <QuotaRequestsPanel />;
}
