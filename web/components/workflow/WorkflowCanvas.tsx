'use client';

import '@xyflow/react/dist/style.css';
import './canvas.css';

import { Background, BackgroundVariant, MiniMap, ReactFlow, ReactFlowProvider, SelectionMode, useReactFlow } from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { issuesByEntity, planKeys } from '@/lib/workflow/canvasModel';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { Viewport } from '@/lib/workflow/types';

import { CanvasSearch, NodeContextMenu, RenameInput, type ContextMenuState } from './CanvasOverlays';
import { EdgeLine } from './EdgeLine';
import { buildFlowEdges, buildFlowNodes, type WfFlowNode } from './flowGraph';
import { AnchorNode } from './nodes/AnchorNode';
import { GateNode } from './nodes/GateNode';
import { NoteNode } from './nodes/NoteNode';
import { StepNode } from './nodes/StepNode';
import { DRAG_KIND_MIME, Toolbar, type AddableKind, type CanvasMode } from './Toolbar';
import { useCanvasActions } from './useCanvasActions';
import { useCanvasHandlers } from './useCanvasHandlers';
import { useCanvasKeyboard } from './useCanvasKeyboard';

const NODE_TYPES = { step: StepNode, gate: GateNode, note: NoteNode, anchor: AnchorNode };
const EDGE_TYPES = { wf: EdgeLine };
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 };
const HINT_MS = 3200;

function isAddableKind(value: string): value is AddableKind {
  return value === 'step' || value === 'gate' || value === 'note';
}

function CanvasInner() {
  const doc = useWorkflowStore((state) => state.doc);
  const readOnly = useWorkflowStore((state) => state.readOnly);
  const selection = useWorkflowStore((state) => state.selection);
  const validation = useWorkflowStore((state) => state.validation);
  const highlightIds = useWorkflowStore((state) => state.highlightIds);
  const announce = useWorkflowStore((state) => state.announce);
  const canUndo = useWorkflowStore((state) => state.canUndo);
  const canRedo = useWorkflowStore((state) => state.canRedo);
  const aiBusy = useWorkflowStore((state) => state.aiBusy);

  const flow = useReactFlow<WfFlowNode>();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const actions = useCanvasActions();
  const [mode, setMode] = useState<CanvasMode>('select');
  const [searching, setSearching] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const keys = useMemo(() => (doc ? planKeys(doc) : {}), [doc]);
  const issues = useMemo(() => issuesByEntity(validation), [validation]);
  const [nodes, setNodes] = useState<WfFlowNode[]>([]);

  // 文档/选择/问题/高亮变化 → 重新派生节点；拖动中的本地位置由 xyflow 持有，落手后写回文档。
  useEffect(() => {
    if (!doc) return;
    setNodes(buildFlowNodes({ doc, keys, issues, highlightIds, selectedNodes: selection.nodes, readOnly }));
  }, [doc, keys, issues, highlightIds, selection.nodes, readOnly]);
  const edges = useMemo(() => (doc ? buildFlowEdges(doc, selection.edges) : []), [doc, selection.edges]);

  const showHint = useCallback((text: string) => {
    setHint(text);
    window.setTimeout(() => setHint((current) => (current === text ? null : current)), HINT_MS);
    useWorkflowStore.setState({ announce: canvasCopy.announce.rejected(text) });
  }, []);

  const addAt = useCallback(
    (kind: AddableKind, point?: { x: number; y: number }) => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const screen = point ?? { x: (rect?.left ?? 0) + (rect?.width ?? 0) / 2, y: (rect?.top ?? 0) + (rect?.height ?? 0) / 2 };
      const position = flow.screenToFlowPosition(screen);
      if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
      actions.addNode(kind, { x: Math.round(position.x - 100), y: Math.round(position.y - 30) });
    },
    [actions, flow],
  );

  const reveal = useCallback(
    (id: string) => {
      const node = flow.getNode(id);
      if (!node) return;
      void flow.setCenter(node.position.x + 100, node.position.y + 30, { zoom: Math.max(flow.getZoom(), 0.8), duration: 200 });
    },
    [flow],
  );

  const keyboard = useCanvasKeyboard({
    actions,
    onSearch: () => setSearching(true),
    onRename: (id) => !readOnly && setRenaming(id),
    reveal,
  });

  const handlers = useCanvasHandlers(actions, showHint, setNodes);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      const kind = event.dataTransfer.getData(DRAG_KIND_MIME);
      if (!isAddableKind(kind) || readOnly) return;
      event.preventDefault();
      addAt(kind, { x: event.clientX, y: event.clientY });
    },
    [addAt, readOnly],
  );

  const pickSearch = useCallback(
    (id: string) => {
      useWorkflowStore.getState().setSelection({ nodes: [id], edges: [] });
      reveal(id);
      setSearching(false);
    },
    [reveal],
  );

  if (!doc) return null;
  const menuNode = menu ? doc.nodes.find((node) => node.id === menu.nodeId) : undefined;
  const protectedMenu = menuNode?.kind === 'start' || menuNode?.kind === 'end';
  const renameNode = renaming ? doc.nodes.find((node) => node.id === renaming) : undefined;
  const hasDefaultViewport = doc.viewport.x === DEFAULT_VIEWPORT.x && doc.viewport.y === DEFAULT_VIEWPORT.y && doc.viewport.zoom === DEFAULT_VIEWPORT.zoom;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="wf-canvas-root">
      <Toolbar
        readOnly={readOnly}
        mode={mode}
        canUndo={canUndo}
        canRedo={canRedo}
        onMode={setMode}
        onAdd={(kind) => addAt(kind)}
        onUndo={() => useWorkflowStore.getState().undo()}
        onRedo={() => useWorkflowStore.getState().redo()}
        onLayout={actions.autoLayout}
        onFit={() => void flow.fitView({ padding: 0.2, duration: 200 })}
        onZoom={(direction) => void (direction > 0 ? flow.zoomIn({ duration: 120 }) : flow.zoomOut({ duration: 120 }))}
        onSearch={() => setSearching(true)}
      />
      <div
        ref={wrapperRef}
        className={cn('wf-canvas relative min-h-0 flex-1', readOnly && 'wf-readonly')}
        data-testid="wf-canvas-surface"
        onKeyDown={keyboard}
        onDragOver={(event) => {
          if (!readOnly) event.preventDefault();
        }}
        onDrop={onDrop}
      >
        <ReactFlow<WfFlowNode>
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onNodesChange={handlers.onNodesChange}
          onConnect={handlers.onConnect}
          onConnectEnd={handlers.onConnectEnd}
          isValidConnection={handlers.isValidConnection}
          onNodeDragStop={handlers.onNodeDragStop}
          onSelectionChange={handlers.onSelectionChange}
          onMoveEnd={handlers.onMoveEnd}
          onNodeDoubleClick={(_event, node) => !readOnly && setRenaming(node.id)}
          onNodeContextMenu={(event, node) => {
            event.preventDefault();
            setMenu({ nodeId: node.id, x: event.clientX, y: event.clientY });
          }}
          onPaneClick={() => setMenu(null)}
          onInit={(instance) => {
            if (hasDefaultViewport) void instance.fitView({ padding: 0.2 });
          }}
          defaultViewport={doc.viewport}
          minZoom={0.2}
          maxZoom={2.5}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          deleteKeyCode={null}
          disableKeyboardA11y
          selectionKeyCode={null}
          multiSelectionKeyCode={['Meta', 'Control', 'Shift']}
          panOnDrag={mode === 'pan' ? true : [1, 2]}
          selectionOnDrag={mode === 'select'}
          selectionMode={SelectionMode.Partial}
          panActivationKeyCode="Space"
          connectionRadius={28}
          proOptions={{ hideAttribution: true }}
          aria-label={canvasCopy.canvasLabel}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <MiniMap pannable zoomable ariaLabel="缩略图" className="!bottom-2 !right-2 !h-[84px] !w-[128px] !rounded-[var(--radius-sm)] !border !border-[var(--border-subtle)]" />
        </ReactFlow>

        {aiBusy ? (
          <div role="status" className="absolute right-3 top-3 z-10 rounded-full border border-[var(--accent)]/40 bg-[var(--accent-weak)] px-2.5 py-0.5 text-[11.5px] text-[var(--accent)]">
            {canvasCopy.aiBusy}
          </div>
        ) : null}
        {hint ? (
          <div role="alert" className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-[var(--radius-sm)] border border-[var(--danger)]/40 bg-[var(--danger-weak)] px-3 py-1 text-[12px] text-[var(--danger)]">
            {hint}
          </div>
        ) : null}
        {searching ? <CanvasSearch doc={doc} keys={keys} onPick={pickSearch} onClose={() => setSearching(false)} /> : null}
        <div className="sr-only" role="status" aria-live="polite" data-testid="wf-live">
          {announce}
        </div>
      </div>
      {menu ? (
        <NodeContextMenu
          state={menu}
          protectedNode={protectedMenu}
          onRename={() => !readOnly && setRenaming(menu.nodeId)}
          onDuplicate={() => {
            useWorkflowStore.getState().setSelection({ nodes: [menu.nodeId], edges: [] });
            actions.duplicateSelection();
          }}
          onDisconnect={() => actions.disconnectNode(menu.nodeId)}
          onDelete={() => {
            useWorkflowStore.getState().setSelection({ nodes: [menu.nodeId], edges: [] });
            actions.deleteSelection();
          }}
          onClose={() => setMenu(null)}
        />
      ) : null}
      {renameNode ? (
        <RenameInput
          nodeId={renameNode.id}
          initial={renameNode.title}
          onCommit={(title) => {
            actions.renameNode(renameNode.id, title);
            setRenaming(null);
          }}
          onCancel={() => setRenaming(null)}
        />
      ) : null}
    </div>
  );
}

/** 画布入口：无 props，一切状态读 store。必须经 `next/dynamic({ ssr: false })` 引入。 */
export default function WorkflowCanvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
