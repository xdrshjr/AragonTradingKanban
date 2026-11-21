'use client';

import { useCallback } from 'react';

import { layoutLevels } from '@/lib/workflow/layout';
import { cloneNode, makeEdge, makeNode, nodeById } from '@/lib/workflow/ops';
import type { Position, WorkflowDoc, WorkflowNode, WorkflowOp } from '@/lib/workflow/types';
import { useWorkflowStore } from '@/lib/workflow/store';

const isPresent = (node: WorkflowNode | undefined): node is WorkflowNode => node !== undefined;

/** 删除选中项的 op：锚点不可删；与已删节点相连的边由级联删除，不重复删。 */
export function buildDeleteOps(doc: WorkflowDoc, nodeIds: readonly string[], edgeIds: readonly string[]): WorkflowOp[] {
  const removable = nodeIds
    .map((id) => nodeById(doc, id))
    .filter(isPresent)
    .filter((node) => node.kind !== 'start' && node.kind !== 'end');
  const removedIds = new Set(removable.map((node) => node.id));
  const ops: WorkflowOp[] = [];
  for (const edgeId of edgeIds) {
    const edge = doc.edges.find((candidate) => candidate.id === edgeId);
    if (!edge || removedIds.has(edge.source) || removedIds.has(edge.target)) continue;
    ops.push({ op: 'delete_edge', id: edge.id, expectRev: edge.rev });
  }
  for (const node of removable) ops.push({ op: 'delete_node', id: node.id, expectRev: node.rev });
  return ops;
}

/** 断开某节点的全部连线。 */
export function buildDisconnectOps(doc: WorkflowDoc, nodeId: string): WorkflowOp[] {
  return doc.edges
    .filter((edge) => edge.source === nodeId || edge.target === nodeId)
    .map((edge): WorkflowOp => ({ op: 'delete_edge', id: edge.id, expectRev: edge.rev }));
}

/** 整理布局：只为位置变化的节点发 `move_node`（与 Python `layout_levels` 逐点一致）。 */
export function buildLayoutOps(doc: WorkflowDoc): WorkflowOp[] {
  const points = layoutLevels(doc);
  const ops: WorkflowOp[] = [];
  for (const node of doc.nodes) {
    const target = points[node.id];
    if (!target) continue;
    if (target.x === node.position.x && target.y === node.position.y) continue;
    ops.push({ op: 'move_node', id: node.id, position: { x: target.x, y: target.y } });
  }
  return ops;
}

export interface CanvasActions {
  addNode: (kind: 'step' | 'gate' | 'note', position: Position) => string | null;
  deleteSelection: () => boolean;
  duplicateSelection: () => void;
  disconnectNode: (id: string) => void;
  autoLayout: () => void;
  renameNode: (id: string, title: string) => void;
  connect: (source: string, target: string) => void;
}

/** 画布手势 → op 的唯一出口（每个手势一次 dispatch = 一条撤销历史）。 */
export function useCanvasActions(): CanvasActions {
  const dispatch = useWorkflowStore((state) => state.dispatch);

  const addNode = useCallback<CanvasActions['addNode']>(
    (kind, position) => {
      const node = makeNode(kind, position);
      if (!dispatch([{ op: 'add_node', node }])) return null;
      useWorkflowStore.getState().setSelection({ nodes: [node.id], edges: [] });
      return node.id;
    },
    [dispatch],
  );

  const deleteSelection = useCallback(() => {
    const { doc, selection, setSelection } = useWorkflowStore.getState();
    if (!doc) return false;
    const ops = buildDeleteOps(doc, selection.nodes, selection.edges);
    if (ops.length === 0) return false;
    const ok = dispatch(ops);
    if (ok) setSelection({ nodes: [], edges: [] });
    return ok;
  }, [dispatch]);

  const duplicateSelection = useCallback(() => {
    const { doc, selection, setSelection } = useWorkflowStore.getState();
    if (!doc) return;
    const copies = selection.nodes
      .map((id) => nodeById(doc, id))
      .filter(isPresent)
      .filter((node) => node.kind === 'step' || node.kind === 'gate' || node.kind === 'note')
      .map((node) => cloneNode(node));
    if (copies.length === 0) return;
    const ok = dispatch(copies.map((node): WorkflowOp => ({ op: 'add_node', node })));
    if (ok) setSelection({ nodes: copies.map((node) => node.id), edges: [] });
  }, [dispatch]);

  const disconnectNode = useCallback(
    (id: string) => {
      const { doc } = useWorkflowStore.getState();
      if (!doc) return;
      const ops = buildDisconnectOps(doc, id);
      if (ops.length > 0) dispatch(ops);
    },
    [dispatch],
  );

  const autoLayout = useCallback(() => {
    const { doc } = useWorkflowStore.getState();
    if (!doc) return;
    const ops = buildLayoutOps(doc);
    if (ops.length > 0) dispatch(ops);
  }, [dispatch]);

  const renameNode = useCallback(
    (id: string, title: string) => {
      const { doc } = useWorkflowStore.getState();
      const node = doc ? nodeById(doc, id) : undefined;
      const next = title.trim();
      if (!node || next === node.title || next === '') return;
      dispatch([{ op: 'update_node', id, expectRev: node.rev, patch: { title: next } }]);
    },
    [dispatch],
  );

  const connect = useCallback(
    (source: string, target: string) => {
      dispatch([{ op: 'add_edge', edge: makeEdge(source, target) }]);
    },
    [dispatch],
  );

  return { addNode, deleteSelection, duplicateSelection, disconnectNode, autoLayout, renameNode, connect };
}
