import type { Metadata } from 'next';

import { SettingsForm } from '@/components/admin/SettingsForm';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.settings };

export default function AdminSettingsFormPage() {
  return <SettingsForm />;
}
