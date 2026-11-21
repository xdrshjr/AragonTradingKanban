'use client';

import { useCallback } from 'react';

import { navigationOrder } from '@/lib/workflow/canvasModel';
import { useWorkflowStore } from '@/lib/workflow/store';

import type { CanvasActions } from './useCanvasActions';

export interface KeyboardHandlers {
  actions: CanvasActions;
  onSearch: () => void;
  onRename: (id: string) => void;
  /** 把某节点滚入视野（方向键移动焦点后）。 */
  reveal: (id: string) => void;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

/** 事件源所在的画布节点 id（焦点在节点本体或其内部时）。 */
function nodeIdOf(target: EventTarget | null): string | null {
  if (!(target instanceof HTMLElement)) return null;
  return target.closest<HTMLElement>('.react-flow__node')?.dataset.id ?? null;
}

function focusNode(id: string): void {
  const el = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(id)}"]`);
  el?.focus();
}

/** 方向键在节点间移动焦点（按层级顺序）：→/↓ 下一个，←/↑ 上一个。 */
function moveFocus(direction: 1 | -1, handlers: KeyboardHandlers): void {
  const { doc, selection, setSelection } = useWorkflowStore.getState();
  if (!doc) return;
  const order = navigationOrder(doc);
  if (order.length === 0) return;
  const current = selection.nodes[selection.nodes.length - 1];
  const index = current ? order.indexOf(current) : -1;
  const next = order[(index + direction + order.length) % order.length];
  if (!next) return;
  setSelection({ nodes: [next], edges: [] });
  handlers.reveal(next);
  focusNode(next);
}

/** ⌘/Ctrl 组合键。返回 true = 已处理。 */
function handleModified(event: KeyboardEvent | React.KeyboardEvent, handlers: KeyboardHandlers): boolean {
  const store = useWorkflowStore.getState();
  const key = event.key.toLowerCase();
  if (key === 'z') {
    if (event.shiftKey) store.redo();
    else store.undo();
  } else if (key === 'y') {
    store.redo();
  } else if (key === 'd') {
    handlers.actions.duplicateSelection();
  } else if (key === 'a') {
    const doc = store.doc;
    if (doc) store.setSelection({ nodes: doc.nodes.map((node) => node.id), edges: [] });
  } else if (key === 'f') {
    handlers.onSearch();
  } else {
    return false;
  }
  return true;
}

function handlePlain(event: React.KeyboardEvent, handlers: KeyboardHandlers): boolean {
  const store = useWorkflowStore.getState();
  if (event.key === 'Delete' || event.key === 'Backspace') {
    handlers.actions.deleteSelection();
  } else if (event.key === 'Enter') {
    const id = nodeIdOf(event.target);
    if (id) store.setSelection({ nodes: [id], edges: [] });
  } else if (event.key === 'F2') {
    const id = store.selection.nodes[0];
    if (id) handlers.onRename(id);
  } else if (event.key === 'Escape') {
    store.setSelection({ nodes: [], edges: [] });
  } else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    moveFocus(1, handlers);
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    moveFocus(-1, handlers);
  } else {
    return false;
  }
  return true;
}

/** 画布外层的 `onKeyDown`：输入框内的按键一律放行；只读时只保留导航/搜索/选择类。 */
export function useCanvasKeyboard(handlers: KeyboardHandlers): (event: React.KeyboardEvent) => void {
  return useCallback(
    (event: React.KeyboardEvent) => {
      if (isTypingTarget(event.target)) return;
      const readOnly = useWorkflowStore.getState().readOnly;
      const modified = event.metaKey || event.ctrlKey;
      const mutating = modified ? ['z', 'y', 'd'].includes(event.key.toLowerCase()) : ['Delete', 'Backspace', 'F2'].includes(event.key);
      if (readOnly && mutating) return;
      const handled = modified ? handleModified(event, handlers) : handlePlain(event, handlers);
      if (handled) event.preventDefault();
    },
    [handlers],
  );
}
