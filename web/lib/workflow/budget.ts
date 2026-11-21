/**
 * 回合预算的**估算**（与服务端 `budget_tight` 同一启发式，仅用于状态栏展示；权威值以校验报告为准）。
 * 文案必须写成"估算"，不是承诺（R-W2）。
 */

import { EFFORT_WEIGHTS, GATE_WEIGHT, GLOBAL_CEILING } from './contract';
import type { WorkflowDoc } from './types';

/** `1`（读取 workflow.md 本身）+ Σ 步骤权重 + 每个检查点 1 回合。 */
export function estimateTurns(doc: WorkflowDoc): number {
  let total = 1;
  for (const node of doc.nodes) {
    if (node.kind === 'step') total += EFFORT_WEIGHTS[node.config.effort ?? 'normal'] ?? 4;
    else if (node.kind === 'gate') total += GATE_WEIGHT;
  }
  return total;
}

/** 流程策略只能收紧全站上限。 */
export function effectiveMaxTurns(doc: WorkflowDoc): number {
  const site = GLOBAL_CEILING.aragonMaxTurns;
  const own = doc.policy.maxTurns;
  return own === null ? site : Math.min(own, site);
}
