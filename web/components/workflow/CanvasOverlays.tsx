'use client';

import { Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { canvasCopy, KIND_LABELS } from '@/lib/workflow/copy.canvas';
import type { PlanKeyInfo } from '@/lib/workflow/canvasModel';
import type { WorkflowDoc } from '@/lib/workflow/types';

// ── 搜索（⌘F）：按标题或键过滤并定位 ─────────────────────────────────────────

interface SearchProps {
  doc: WorkflowDoc;
  keys: Record<string, PlanKeyInfo>;
  onPick: (id: string) => void;
  onClose: () => void;
}

export function CanvasSearch({ doc, keys, onPick, onClose }: SearchProps) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return doc.nodes
      .filter((node) => {
        if (needle === '') return true;
        const key = keys[node.id]?.key ?? '';
        return node.title.toLowerCase().includes(needle) || key.toLowerCase() === needle;
      })
      .slice(0, 8);
  }, [doc.nodes, keys, query]);

  const onKeyDown = (event: React.KeyboardEvent): void => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((index) => Math.min(index + 1, matches.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter') {
      const hit = matches[active];
      if (hit) onPick(hit.id);
    }
  };

  return (
    <div
      role="search"
      aria-label={canvasCopy.toolbar.search}
      className="absolute left-1/2 top-2 z-20 w-[300px] -translate-x-1/2 rounded-[var(--radius-sm)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] shadow-[var(--shadow-pop)]"
      onKeyDown={onKeyDown}
    >
      <div className="flex items-center gap-1.5 border-b border-[var(--border-subtle)] px-2 py-1">
        <Search aria-hidden="true" className="size-3.5 text-[var(--text-tertiary)]" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          placeholder={canvasCopy.search.placeholder}
          aria-label={canvasCopy.search.placeholder}
          className="h-6 min-w-0 flex-1 bg-transparent text-[12px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-tertiary)]"
        />
        <button
          type="button"
          aria-label={canvasCopy.search.close}
          title={canvasCopy.search.close}
          onClick={onClose}
          className="rounded p-0.5 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <ul role="listbox" className="max-h-[220px] overflow-auto py-1">
        {matches.length === 0 ? (
          <li className="px-3 py-2 text-[12px] text-[var(--text-tertiary)]">{canvasCopy.search.none}</li>
        ) : (
          matches.map((node, index) => (
            <li
              key={node.id}
              role="option"
              aria-selected={index === active}
              onMouseEnter={() => setActive(index)}
              onClick={() => onPick(node.id)}
              className={`flex cursor-pointer items-center gap-2 px-3 py-1 text-[12px] ${
                index === active ? 'bg-[var(--bg-active)]' : ''
              }`}
            >
              <span className="w-7 font-mono text-[10.5px] text-[var(--text-tertiary)]">{keys[node.id]?.key ?? ''}</span>
              <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">{node.title}</span>
              <span className="text-[10px] text-[var(--text-tertiary)]">{KIND_LABELS[node.kind]}</span>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}

// ── 右键菜单 ─────────────────────────────────────────────────────────────────

export interface ContextMenuState {
  nodeId: string;
  x: number;
  y: number;
}

interface MenuProps {
  state: ContextMenuState;
  protectedNode: boolean;
  onRename: () => void;
  onDuplicate: () => void;
  onDisconnect: () => void;
  onDelete: () => void;
  onClose: () => void;
}

export function NodeContextMenu({ state, protectedNode, onRename, onDuplicate, onDisconnect, onDelete, onClose }: MenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus();
    const away = (event: MouseEvent): void => {
      if (!ref.current?.contains(event.target as Node)) onClose();
    };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [onClose]);

  const item = (label: string, action: () => void, options: { danger?: boolean; disabled?: boolean } = {}) => (
    <button
      type="button"
      role="menuitem"
      disabled={options.disabled}
      onClick={() => {
        action();
        onClose();
      }}
      className={`flex w-full items-center rounded-[var(--radius-sm)] px-2 py-1 text-left text-[12px] outline-none hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-hover)] disabled:opacity-40 ${
        options.danger ? 'text-[var(--danger)]' : 'text-[var(--text-secondary)]'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={canvasCopy.menu.rename}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onClose();
      }}
      className="fixed z-50 min-w-[150px] rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]"
      style={{ left: state.x, top: state.y }}
    >
      {item(`${canvasCopy.menu.rename}  ${canvasCopy.shortcut.rename}`, onRename)}
      {item(`${canvasCopy.menu.duplicate}  ${canvasCopy.shortcut.duplicate}`, onDuplicate, { disabled: protectedNode })}
      {item(canvasCopy.menu.disconnect, onDisconnect)}
      {item(`${canvasCopy.menu.delete}  ${canvasCopy.shortcut.delete}`, onDelete, { danger: true, disabled: protectedNode })}
    </div>
  );
}

// ── 就地重命名 ───────────────────────────────────────────────────────────────

interface RenameProps {
  nodeId: string;
  initial: string;
  onCommit: (title: string) => void;
  onCancel: () => void;
}

/** 浮在节点上的输入框：Enter/失焦提交，Esc 取消。位置取节点 DOM 的包围盒。 */
export function RenameInput({ nodeId, initial, onCommit, onCancel }: RenameProps) {
  const [value, setValue] = useState(initial);
  const inputRef = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  const rect = useMemo(() => {
    const el = document.querySelector(`.react-flow__node[data-id="${CSS.escape(nodeId)}"]`);
    return el?.getBoundingClientRect() ?? null;
  }, [nodeId]);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const finish = (commit: boolean): void => {
    if (done.current) return;
    done.current = true;
    if (commit) onCommit(value);
    else onCancel();
  };

  return (
    <input
      ref={inputRef}
      value={value}
      aria-label={canvasCopy.menu.rename}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Enter') finish(true);
        else if (event.key === 'Escape') finish(false);
      }}
      className="fixed z-50 h-7 rounded-[var(--radius-sm)] border border-[var(--accent)] bg-[var(--bg-surface-1)] px-2 text-[12.5px] text-[var(--text-primary)] outline-none ring-2 ring-[var(--accent-ring)]/50"
      style={{ left: rect?.left ?? 80, top: (rect?.top ?? 80) + 4, width: Math.max(160, rect?.width ?? 160) }}
    />
  );
}
