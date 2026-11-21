/** 画布节点的 `data` 载荷：把文档节点与派生展示信息一起交给 xyflow（只读，写入走 store）。 */

import type { WorkflowNode } from '@/lib/workflow/types';

export interface FlowNodeData extends Record<string, unknown> {
  node: WorkflowNode;
  /** `S1` / `G1`；start/end/note 无键。 */
  planKey: string | null;
  errors: number;
  warnings: number;
  /** AI 变更高亮（脉冲在 reduced-motion 下关闭）。 */
  highlighted: boolean;
  readOnly: boolean;
}

export const ROLE_TONE: Record<string, string> = {
  data: 'var(--info)',
  analysis: 'var(--accent)',
  synthesis: 'var(--warn)',
  review: 'var(--danger)',
  custom: 'var(--text-tertiary)',
};
