import type { Metadata } from 'next';

import { OverviewDashboard } from '@/components/admin/OverviewDashboard';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.overview };

export default function AdminOverviewPage() {
  return <OverviewDashboard />;
}
