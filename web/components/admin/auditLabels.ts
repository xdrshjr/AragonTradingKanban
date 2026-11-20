import { copyAdmin } from '@/lib/copy.admin';

/** 审计动作的中文标签（词表固定，§24.3.10）；未知动作原样显示，不隐瞒。 */
export function auditActionLabel(action: string): string {
  return copyAdmin.audit.actions[action] ?? action;
}

/** 筛选下拉用的全部已知动作（保持词表顺序）。 */
export const AUDIT_ACTIONS: readonly string[] = Object.keys(copyAdmin.audit.actions);
