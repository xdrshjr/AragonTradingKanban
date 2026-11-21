'use client';

import { useEffect } from 'react';

import { useWorkflowStore } from '@/lib/workflow/store';

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

export interface ShortcutHandlers {
  onTogglePanel: () => void;
  onPublish: () => void;
}

/**
 * 编辑器外壳拥有的全局快捷键：`⌘Z` / `⌘⇧Z`（撤销重做）、`⌘J`（AI 面板）、`⌘⇧P`（发布）。
 * 画布内的 `Del / ⌘D / ⌘A / F2 / ⌘F / Esc / Space` 由画布组件自己处理，这里不重复。
 * 焦点在输入框里时 `⌘Z` 交还给浏览器原生撤销。
 */
export function useEditorShortcuts({ onTogglePanel, onPublish }: ShortcutHandlers): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      const { undo, redo } = useWorkflowStore.getState();
      if (key === 'j') {
        event.preventDefault();
        onTogglePanel();
      } else if (key === 'p' && event.shiftKey) {
        event.preventDefault();
        onPublish();
      } else if (key === 'z' && !isEditable(event.target)) {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if (key === 'y' && !isEditable(event.target)) {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onTogglePanel, onPublish]);
}
