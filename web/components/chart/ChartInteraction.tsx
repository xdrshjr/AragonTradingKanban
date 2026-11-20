'use client';

import {
  useCallback, useEffect, useRef, useState,
  type ReactNode, type PointerEvent as ReactPointerEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';

import {
  calculateMeasurement, type ChartDataset, type ChartSelection,
  type ChartTool, type Measurement,
} from '@/lib/chartDataset';
import { isEditableTarget } from '@/lib/keyboard';
import type { ChartControllerApi } from './ChartController';

interface Draft {
  pointerId: number;
  start: number;
  end: number;
  x: number;
  mode: 'measure' | 'report-range';
  target: HTMLElement;
}
interface TouchCandidate {
  pointerId: number;
  x: number;
  y: number;
  target: HTMLElement;
}
export interface ChartInteractionProps {
  dataset: ChartDataset;
  controller: ChartControllerApi | null;
  tool: ChartTool;
  selection: ChartSelection;
  rangeQueryEnabled: boolean;
  onToolChange: (tool: ChartTool) => void;
  onMeasurement: (measurement: Measurement) => void;
  onBrushRange?: (range: { from: string; to: string } | null) => void;
  escapeRef?: { current: ((event: KeyboardEvent) => boolean) | null };
  children: ReactNode;
}

function useGestureState(props: ChartInteractionProps) {
  const root = useRef<HTMLDivElement>(null);
  const draft = useRef<Draft | null>(null);
  const touch = useRef<TouchCandidate | null>(null);
  const pointers = useRef(new Set<number>());
  const [rectangle, setRectangle] = useState<{
    from: number; to: number;
  } | null>(null);
  const controller = props.controller;
  const cancel = useCallback(() => {
    const active = draft.current;
    draft.current = null; touch.current = null;
    if (active && root.current?.hasPointerCapture?.(active.pointerId)) {
      root.current.releasePointerCapture(active.pointerId);
    }
    controller?.setGesturing(false); setRectangle(null);
  }, [controller]);
  return { props, root, draft, touch, pointers,
    rectangle, setRectangle, cancel, controller };
}
type GestureState = ReturnType<typeof useGestureState>;
type PointerInput = ReactPointerEvent<HTMLDivElement>;

function useGestureEscape(state: GestureState) {
  const { props, draft, cancel, controller } = state;
  const { tool, selection, onToolChange, escapeRef } = props;
  const consumeEscape = useCallback((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.isComposing
      || event.ctrlKey || event.metaKey || event.altKey
      || isEditableTarget(event.target)) return false;
    if (draft.current) cancel();
    else if (tool !== 'pan') onToolChange('pan');
    else if (selection.origin !== 'latest') {
      controller?.setSelection(null, 'latest');
    } else return false;
    event.preventDefault(); event.stopPropagation(); return true;
  }, [cancel, controller, draft, onToolChange, selection.origin, tool]);
  useEffect(() => {
    if (escapeRef) escapeRef.current = consumeEscape;
    return () => { if (escapeRef) escapeRef.current = null; };
  }, [consumeEscape, escapeRef]);
  return consumeEscape;
}

function useGestureLifecycle(state: GestureState) {
  const { root, draft, touch, controller, cancel } = state;
  const version = state.props.dataset.datasetVersion;
  useEffect(() => {
    const element = root.current;
    cancel();
    return () => {
      const active = draft.current;
      draft.current = null; touch.current = null;
      if (active && element?.hasPointerCapture?.(active.pointerId)) {
        element.releasePointerCapture(active.pointerId);
      }
      controller?.setGesturing(false);
    };
  }, [version, controller, cancel, root, draft, touch]);
}

function startPointer(state: GestureState, event: PointerInput) {
  const { props, pointers, controller, touch, draft, cancel } = state;
  pointers.current.add(event.pointerId);
  if (pointers.current.size > 1) { cancel(); return; }
  if (event.button !== 0 || !controller) return;
  const target = event.target as HTMLElement;
  const index = controller.coordinateToIndex(target,
    event.clientX, false, event.clientY);
  const mode = event.shiftKey && props.rangeQueryEnabled
    ? 'report-range' : props.tool;
  if (mode === 'pan') {
    if (event.pointerType === 'touch') touch.current = {
      pointerId: event.pointerId, x: event.clientX, y: event.clientY, target,
    };
    return;
  }
  if (index === null || mode === 'report-range'
    && !props.rangeQueryEnabled) return;
  event.preventDefault(); event.stopPropagation();
  controller.setGesturing(true);
  draft.current = { pointerId: event.pointerId, start: index,
    end: index, x: event.clientX, mode, target };
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

function movePointer(state: GestureState, event: PointerInput) {
  const { controller, touch, draft } = state;
  const candidate = touch.current;
  if (candidate && Math.hypot(event.clientX - candidate.x,
    event.clientY - candidate.y) >= 8) touch.current = null;
  const active = draft.current;
  if (!active || active.pointerId !== event.pointerId || !controller) return;
  event.preventDefault(); event.stopPropagation();
  const index = controller.coordinateToIndex(active.target,
    event.clientX, true);
  if (index !== null) active.end = index;
  const offset = event.currentTarget.getBoundingClientRect().left;
  state.setRectangle({ from: active.x - offset, to: event.clientX - offset });
  controller.setSelection(active.end, 'pointer');
}

function commitDraft(props: ChartInteractionProps, active: Draft) {
  const { dataset } = props;
  const start = Math.min(active.start, active.end);
  const end = Math.max(active.start, active.end);
  if (active.mode === 'measure') {
    props.onMeasurement(calculateMeasurement(dataset, start, end));
  } else props.onBrushRange?.({ from: dataset.stats[start]!.periodStart,
    to: dataset.stats[end]!.periodEnd });
}

function endPointer(state: GestureState, event: PointerInput) {
  const { controller, pointers, cancel } = state;
  const candidate = state.touch.current;
  const active = state.draft.current;
  if (candidate && pointers.current.size === 1 && controller) {
    const index = controller.coordinateToIndex(candidate.target,
      event.clientX, false, event.clientY);
    if (Math.hypot(event.clientX - candidate.x,
      event.clientY - candidate.y) < 8) {
      controller.setSelection(index, index === null ? 'latest' : 'touch');
    }
  }
  if (active && active.pointerId === event.pointerId) {
    event.preventDefault(); event.stopPropagation();
    if (Math.abs(event.clientX - active.x) >= 8) {
      commitDraft(state.props, active);
    }
  }
  pointers.current.delete(event.pointerId); cancel();
}

function handleKey(state: GestureState,
  consumeEscape: (event: KeyboardEvent) => boolean,
  event: ReactKeyboardEvent<HTMLDivElement>) {
  const { controller, props } = state;
  if (event.defaultPrevented || event.nativeEvent.isComposing
    || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey
    || (event.shiftKey && event.key !== '+')
    || isEditableTarget(event.target)) return;
  if (event.key === 'Escape') { consumeEscape(event.nativeEvent); return; }
  if (event.target !== event.currentTarget) return;
  const n = props.dataset.bars.length;
  const index = props.selection.index ?? n - 1;
  const commands: Record<string, () => void> = {
    ArrowLeft: () => controller?.setSelection(
      Math.max(0, index - 1), 'keyboard'),
    ArrowRight: () => controller?.setSelection(
      Math.min(n - 1, index + 1), 'keyboard'),
    Home: () => controller?.setSelection(0, 'keyboard'),
    End: () => controller?.setSelection(n - 1, 'keyboard'),
    '+': () => controller?.zoom(.8), '=': () => controller?.zoom(.8),
    '-': () => controller?.zoom(1.25), '0': () => controller?.fit(),
  };
  if (!n || !commands[event.key]) return;
  event.preventDefault(); event.stopPropagation(); commands[event.key]!();
}

/** Captures tools before the canvas can begin its native pan gesture. */
export function ChartInteraction(props: ChartInteractionProps) {
  const state = useGestureState(props);
  const consumeEscape = useGestureEscape(state);
  useGestureLifecycle(state);
  const cancelPointer = (event: PointerInput) => {
    state.pointers.current.delete(event.pointerId); state.cancel();
  };
  const blockNative = (event: React.SyntheticEvent) => {
    if (state.draft.current) {
      event.preventDefault(); event.stopPropagation();
    }
  };
  return <div ref={state.root} className="chart-interaction" role="region"
    aria-label="K 线图形" aria-describedby="chart-keyboard-help" tabIndex={0}
    style={{ touchAction: props.tool === 'pan' ? 'pan-y' : 'none' }}
    onKeyDown={event => handleKey(state, consumeEscape, event)}
    onPointerDownCapture={event => startPointer(state, event)}
    onPointerMoveCapture={event => movePointer(state, event)}
    onPointerUpCapture={event => endPointer(state, event)}
    onPointerCancel={cancelPointer} onLostPointerCapture={cancelPointer}
    onMouseDownCapture={blockNative} onTouchStartCapture={blockNative}
    onPointerLeave={() => props.controller?.releasePointer()}>
    <p id="chart-keyboard-help" className="sr-only">
      左右键逐根检视，Home和End首尾，+和-缩放，0适配，Escape逐层退出工具和检视。
    </p>
    {props.children}
    {state.rectangle ? <div className="chart-range-draft" style={{
      left: Math.min(state.rectangle.from, state.rectangle.to),
      width: Math.abs(state.rectangle.to - state.rectangle.from),
    }} /> : null}
  </div>;
}
