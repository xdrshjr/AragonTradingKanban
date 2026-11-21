'use client';

import { useCallback } from 'react';

import { nodeById } from '@/lib/workflow/ops';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { ConfigPatch } from '@/lib/workflow/types';

export interface NodePatcher {
  setTitle: (title: string) => void;
  setConfig: (patch: ConfigPatch) => void;
}

/**
 * 检查器写节点的唯一出口：一次提交 = 一个 `update_node`，`expectRev` 取**提交那一刻**的最新 rev
 * （连续两次字段提交之间 rev 已被本地应用抬高，不能用渲染时捕获的旧值）。
 */
export function useNodePatch(nodeId: string): NodePatcher {
  const dispatch = useWorkflowStore((state) => state.dispatch);
  const send = useCallback(
    (patch: { title?: string; config?: ConfigPatch }) => {
      const doc = useWorkflowStore.getState().doc;
      const node = doc ? nodeById(doc, nodeId) : undefined;
      if (!node) return;
      dispatch([{ op: 'update_node', id: nodeId, expectRev: node.rev, patch }]);
    },
    [dispatch, nodeId],
  );
  const setTitle = useCallback((title: string) => send({ title }), [send]);
  const setConfig = useCallback((patch: ConfigPatch) => send({ config: patch }), [send]);
  return { setTitle, setConfig };
}
