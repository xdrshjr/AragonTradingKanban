import type { Metadata } from 'next';

import { WorkflowListClient } from '@/components/workflow/WorkflowListClient';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.workflows };

export default function AdminWorkflowsPage() {
  return <WorkflowListClient />;
}
