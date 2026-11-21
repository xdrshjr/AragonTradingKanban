'use client';

import '@xyflow/react/dist/style.css';
import './canvas.css';

import { Background, BackgroundVariant, ReactFlow, ReactFlowProvider } from '@xyflow/react';
import { useMemo } from 'react';

import { planKeys } from '@/lib/workflow/canvasModel';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import type { WorkflowDoc } from '@/lib/workflow/types';

import { EdgeLine } from './EdgeLine';
import { buildFlowEdges, buildFlowNodes } from './flowGraph';
import { AnchorNode } from './nodes/AnchorNode';
import { GateNode } from './nodes/GateNode';
import { NoteNode } from './nodes/NoteNode';
import { StepNode } from './nodes/StepNode';

const NODE_TYPES = { step: StepNode, gate: GateNode, note: NoteNode, anchor: AnchorNode };
const EDGE_TYPES = { wf: EdgeLine };

/** 某个已发布版本的只读画布（版本页签「查看」）。不连 store：文档是版本里的快照。 */
export default function VersionCanvas({ doc }: { doc: WorkflowDoc }) {
  const nodes = useMemo(
    () => buildFlowNodes({ doc, keys: planKeys(doc), issues: {}, highlightIds: [], selectedNodes: [], readOnly: true }),
    [doc],
  );
  const edges = useMemo(() => buildFlowEdges(doc, []), [doc]);
  return (
    <div className="wf-canvas wf-readonly h-full w-full" role="img" aria-label={canvasCopy.canvasLabel}>
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
