'use client';

import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type Edge, type EdgeProps } from '@xyflow/react';
import { memo } from 'react';

export interface FlowEdgeData extends Record<string, unknown> {
  label: string;
}

type WorkflowFlowEdge = Edge<FlowEdgeData, 'wf'>;

/** 边：平滑折线 + 箭头（方向 = 先后），选中高亮，可带标签。 */
function EdgeLineView(props: EdgeProps<WorkflowFlowEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd } = props;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 10,
  });
  const label = data?.label ?? '';
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} interactionWidth={18} />
      {label ? (
        <EdgeLabelRenderer>
          <span
            className="nodrag nopan pointer-events-none absolute rounded-sm border border-[var(--border-subtle)] bg-[var(--bg-surface-2)] px-1 text-[10px] text-[var(--text-secondary)]"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              borderColor: selected ? 'var(--accent)' : undefined,
            }}
          >
            {label}
          </span>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const EdgeLine = memo(EdgeLineView);
