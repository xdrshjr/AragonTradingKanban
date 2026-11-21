'use client';

import type { Connection, NodeChange, OnConnectEnd } from '@xyflow/react';
import { useCallback, type Dispatch, type SetStateAction } from 'react';

import { connectionVerdict } from '@/lib/workflow/canvasModel';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { Viewport } from '@/lib/workflow/types';

import type { WfFlowNode } from './flowGraph';
import type { CanvasActions } from './useCanvasActions';

/** 画布 → store 的选择同步：只取 xyflow 报告的 select 变化。 */
function selectionFromChanges(changes: NodeChange<WfFlowNode>[], current: string[]): string[] | null {
  const touched = changes.filter((change) => change.type === 'select');
  if (touched.length === 0) return null;
  const next = new Set(current);
  for (const change of touched) {
    if (change.type !== 'select') continue;
    if (change.selected) next.add(change.id);
    else next.delete(change.id);
  }
  return [...next];
}

/** `applyNodeChanges` 的最小实现：只处理本画布产生的 position / select / dimensions 变化。 */
function applyChanges(changes: NodeChange<WfFlowNode>[], current: WfFlowNode[]): WfFlowNode[] {
  let next = current;
  for (const change of changes) {
    if (change.type === 'position' && change.position) {
      const position = change.position;
      next = next.map((node) => (node.id === change.id ? { ...node, position } : node));
    } else if (change.type === 'select') {
      next = next.map((node) => (node.id === change.id ? { ...node, selected: change.selected } : node));
    } else if (change.type === 'dimensions' && change.dimensions) {
      const measured = change.dimensions;
      next = next.map((node) => (node.id === change.id ? { ...node, measured } : node));
    }
  }
  return next;
}

export interface CanvasHandlers {
  onNodesChange: (changes: NodeChange<WfFlowNode>[]) => void;
  onConnect: (connection: Connection) => void;
  isValidConnection: (connection: { source: string; target: string }) => boolean;
  onConnectEnd: OnConnectEnd;
  onNodeDragStop: (event: unknown, node: unknown, dragged: WfFlowNode[]) => void;
  onSelectionChange: (params: { edges: { id: string }[] }) => void;
  onMoveEnd: (event: unknown, viewport: Viewport) => void;
}

/** xyflow 事件 → store：选择同步、连线判定、拖动落手写回、视口持久化。 */
export function useCanvasHandlers(
  actions: CanvasActions,
  showHint: (text: string) => void,
  setNodes: Dispatch<SetStateAction<WfFlowNode[]>>,
): CanvasHandlers {
  const onNodesChange = useCallback(
    (changes: NodeChange<WfFlowNode>[]) => {
      setNodes((current) => applyChanges(changes, current));
      const next = selectionFromChanges(changes, useWorkflowStore.getState().selection.nodes);
      if (next) useWorkflowStore.getState().setSelection({ nodes: next, edges: useWorkflowStore.getState().selection.edges });
    },
    [setNodes],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const current = useWorkflowStore.getState().doc;
      if (!current || !connection.source || !connection.target) return;
      const verdict = connectionVerdict(current, connection.source, connection.target);
      if (verdict.ok) actions.connect(connection.source, connection.target);
      else showHint(canvasCopy.connect[verdict.reason === 'unknown' ? 'unknown' : verdict.reason]);
    },
    [actions, showHint],
  );

  const isValidConnection = useCallback((connection: { source: string; target: string }) => {
    const current = useWorkflowStore.getState().doc;
    return current ? connectionVerdict(current, connection.source, connection.target).ok : false;
  }, []);

  const onConnectEnd = useCallback<OnConnectEnd>(
    (_event, state) => {
      if (state.isValid !== false || !state.toNode || !state.fromNode) return;
      const current = useWorkflowStore.getState().doc;
      if (!current) return;
      const verdict = connectionVerdict(current, state.fromNode.id, state.toNode.id);
      if (!verdict.ok) showHint(canvasCopy.connect[verdict.reason]);
    },
    [showHint],
  );

  const onNodeDragStop = useCallback((_event: unknown, _node: unknown, dragged: WfFlowNode[]) => {
    const current = useWorkflowStore.getState().doc;
    if (!current) return;
    const ops = dragged
      .filter((moved) => {
        const original = current.nodes.find((node) => node.id === moved.id);
        return original && (original.position.x !== moved.position.x || original.position.y !== moved.position.y);
      })
      .map((moved) => ({
        op: 'move_node' as const,
        id: moved.id,
        position: { x: Math.round(moved.position.x), y: Math.round(moved.position.y) },
      }));
    if (ops.length > 0) useWorkflowStore.getState().dispatch(ops);
  }, []);

  const onSelectionChange = useCallback(({ edges: picked }: { edges: { id: string }[] }) => {
    const store = useWorkflowStore.getState();
    const ids = picked.map((edge) => edge.id);
    const same = ids.length === store.selection.edges.length && ids.every((id) => store.selection.edges.includes(id));
    if (!same) store.setSelection({ nodes: store.selection.nodes, edges: ids });
  }, []);

  const onMoveEnd = useCallback((_event: unknown, viewport: Viewport) => {
    const store = useWorkflowStore.getState();
    const saved = store.doc?.viewport;
    if (!store.doc || (saved && saved.x === viewport.x && saved.y === viewport.y && saved.zoom === viewport.zoom)) return;
    store.dispatch([{ op: 'set_viewport', viewport: { x: viewport.x, y: viewport.y, zoom: viewport.zoom } }], { record: false });
  }, []);

  return { onNodesChange, onConnect, isValidConnection, onConnectEnd, onNodeDragStop, onSelectionChange, onMoveEnd };
}
