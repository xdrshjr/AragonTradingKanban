import type { Metadata } from 'next';

import { WorkflowEditorLoader } from '@/components/workflow/WorkflowEditorLoader';
import { copyAdmin } from '@/lib/copy.admin';

export const metadata: Metadata = { title: copyAdmin.nav.workflows };

/** 编辑器页壳：`AdminShell` 按路径判定全出血；数据与交互全部在客户端组件里。 */
export default async function AdminWorkflowEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkflowEditorLoader templateId={id} />;
}
