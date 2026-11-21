/** 文档 → xyflow 节点/边的纯转换（画布只读渲染；写入走 store.dispatch）。 */

import { MarkerType, type Edge, type Node } from '@xyflow/react';

import type { NodeIssueCount, PlanKeyInfo } from '@/lib/workflow/canvasModel';
import { canvasCopy, KIND_LABELS } from '@/lib/workflow/copy.canvas';
import type { WorkflowDoc } from '@/lib/workflow/types';

import type { FlowEdgeData } from './EdgeLine';
import type { FlowNodeData } from './nodes/nodeData';

export type WfFlowNode = Node<FlowNodeData>;
export type WfFlowEdge = Edge<FlowEdgeData>;

/** xyflow 的 `type` 键：start/end 共用锚点组件。 */
export const NODE_TYPE: Record<string, string> = {
  start: 'anchor',
  end: 'anchor',
  step: 'step',
  gate: 'gate',
  note: 'note',
};

export interface FlowBuildInput {
  doc: WorkflowDoc;
  keys: Record<string, PlanKeyInfo>;
  issues: Record<string, NodeIssueCount>;
  highlightIds: readonly string[];
  selectedNodes: readonly string[];
  readOnly: boolean;
}

export function buildFlowNodes(input: FlowBuildInput): WfFlowNode[] {
  const { doc, keys, issues, highlightIds, selectedNodes, readOnly } = input;
  const highlighted = new Set(highlightIds);
  const selected = new Set(selectedNodes);
  return doc.nodes.map((node) => {
    const info = keys[node.id];
    const count = issues[node.id];
    const title = node.title.trim() === '' ? canvasCopy.node.untitled : node.title;
    return {
      id: node.id,
      type: NODE_TYPE[node.kind] ?? 'step',
      position: { x: node.position.x, y: node.position.y },
      selected: selected.has(node.id),
      deletable: node.kind !== 'start' && node.kind !== 'end',
      ariaRole: 'button',
      ariaLabel: `${KIND_LABELS[node.kind] ?? node.kind} ${info?.key ?? ''} ${title}`.replace(/\s+/g, ' ').trim(),
      data: {
        node,
        planKey: info?.key ?? null,
        errors: count?.errors ?? 0,
        warnings: count?.warnings ?? 0,
        highlighted: highlighted.has(node.id),
        readOnly,
      },
    };
  });
}

export function buildFlowEdges(doc: WorkflowDoc, selectedEdges: readonly string[]): WfFlowEdge[] {
  const selected = new Set(selectedEdges);
  return doc.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'wf',
    selected: selected.has(edge.id),
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    ariaLabel: edge.label ? `连线 ${edge.label}` : '连线',
    data: { label: edge.label },
  }));
}
