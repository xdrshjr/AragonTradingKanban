'use client';

import {
  Hand,
  LayoutGrid,
  Maximize2,
  MousePointer2,
  Plus,
  Redo2,
  Search,
  ShieldCheck,
  StickyNote,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';

import { cn } from '@/lib/cn';
import { canvasCopy } from '@/lib/workflow/copy.canvas';

export const DRAG_KIND_MIME = 'application/x-aragon-wf-kind';
export type AddableKind = 'step' | 'gate' | 'note';
export type CanvasMode = 'select' | 'pan';

export interface ToolbarProps {
  readOnly: boolean;
  mode: CanvasMode;
  canUndo: boolean;
  canRedo: boolean;
  onMode: (mode: CanvasMode) => void;
  onAdd: (kind: AddableKind) => void;
  onUndo: () => void;
  onRedo: () => void;
  onLayout: () => void;
  onFit: () => void;
  onZoom: (direction: 1 | -1) => void;
  onSearch: () => void;
}

interface ToolButtonProps {
  label: string;
  hint?: string;
  pressed?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId?: string;
}

function ToolButton({ label, hint, pressed, disabled, onClick, children, testId }: ToolButtonProps) {
  const title = hint ? `${label}（${hint}）` : label;
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={pressed}
      disabled={disabled}
      data-testid={testId}
      onClick={onClick}
      className={cn(
        'inline-flex h-6 min-w-6 items-center justify-center gap-1 rounded-[var(--radius-sm)] px-1.5 text-[12px] text-[var(--text-secondary)]',
        'transition-colors duration-[var(--dur-fast)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent',
        pressed && 'bg-[var(--bg-active)] text-[var(--text-primary)]',
      )}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <span aria-hidden="true" className="mx-0.5 h-4 w-px bg-[var(--border-subtle)]" />;
}

interface PaletteProps {
  kind: AddableKind;
  label: string;
  icon: React.ReactNode;
  disabled: boolean;
  onAdd: (kind: AddableKind) => void;
}

/** 可拖入画布也可点击落在视口中心的"零件"。 */
function PaletteItem({ kind, label, icon, disabled, onAdd }: PaletteProps) {
  return (
    <button
      type="button"
      draggable={!disabled}
      disabled={disabled}
      title={`${label}：${canvasCopy.toolbar.dragHint}`}
      aria-label={`${canvasCopy.toolbar.dragHint.split('，')[0]} ${label}`}
      data-testid={`wf-palette-${kind}`}
      onDragStart={(event) => {
        event.dataTransfer.setData(DRAG_KIND_MIME, kind);
        event.dataTransfer.effectAllowed = 'copy';
      }}
      onClick={() => onAdd(kind)}
      className={cn(
        'inline-flex h-6 cursor-grab items-center gap-1 rounded-[var(--radius-sm)] border border-transparent px-1.5 text-[12px]',
        'text-[var(--text-secondary)] transition-colors duration-[var(--dur-fast)] hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]',
        'disabled:cursor-not-allowed disabled:opacity-40',
      )}
    >
      <Plus aria-hidden="true" className="size-3" />
      {icon}
      {label}
    </button>
  );
}

export function Toolbar(props: ToolbarProps) {
  const { readOnly, mode, canUndo, canRedo } = props;
  const t = canvasCopy.toolbar;
  const s = canvasCopy.shortcut;
  return (
    <div
      role="toolbar"
      aria-label={canvasCopy.canvasLabel}
      className="flex h-8 shrink-0 items-center gap-0.5 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2"
    >
      <ToolButton label={t.select} pressed={mode === 'select'} onClick={() => props.onMode('select')}>
        <MousePointer2 className="size-3.5" />
      </ToolButton>
      <ToolButton label={t.pan} hint="Space + 拖" pressed={mode === 'pan'} onClick={() => props.onMode('pan')}>
        <Hand className="size-3.5" />
      </ToolButton>
      <Divider />
      <PaletteItem kind="step" label={t.addStep} icon={null} disabled={readOnly} onAdd={props.onAdd} />
      <PaletteItem
        kind="gate"
        label={t.addGate}
        icon={<ShieldCheck aria-hidden="true" className="size-3" />}
        disabled={readOnly}
        onAdd={props.onAdd}
      />
      <PaletteItem
        kind="note"
        label={t.addNote}
        icon={<StickyNote aria-hidden="true" className="size-3" />}
        disabled={readOnly}
        onAdd={props.onAdd}
      />
      <Divider />
      <ToolButton label={t.undo} hint={s.undo} disabled={readOnly || !canUndo} onClick={props.onUndo} testId="wf-undo">
        <Undo2 className="size-3.5" />
      </ToolButton>
      <ToolButton label={t.redo} hint={s.redo} disabled={readOnly || !canRedo} onClick={props.onRedo} testId="wf-redo">
        <Redo2 className="size-3.5" />
      </ToolButton>
      <Divider />
      <ToolButton label={t.layout} disabled={readOnly} onClick={props.onLayout} testId="wf-layout">
        <LayoutGrid className="size-3.5" />
        <span className="hidden xl:inline">{t.layout}</span>
      </ToolButton>
      <ToolButton label={t.fit} onClick={props.onFit}>
        <Maximize2 className="size-3.5" />
      </ToolButton>
      <ToolButton label={t.zoomOut} onClick={() => props.onZoom(-1)}>
        <ZoomOut className="size-3.5" />
      </ToolButton>
      <ToolButton label={t.zoomIn} onClick={() => props.onZoom(1)}>
        <ZoomIn className="size-3.5" />
      </ToolButton>
      <span className="flex-1" />
      <ToolButton label={t.search} hint={s.search} onClick={props.onSearch} testId="wf-search">
        <Search className="size-3.5" />
      </ToolButton>
    </div>
  );
}
