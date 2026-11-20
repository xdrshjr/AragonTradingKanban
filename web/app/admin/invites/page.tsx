import type { Metadata } from 'next';

import { InvitesPanel } from '@/components/admin/InvitesPanel';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.invites };

export default function AdminInvitesPanelPage() {
  return <InvitesPanel />;
}
