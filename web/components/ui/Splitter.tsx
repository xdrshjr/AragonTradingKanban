'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/cn';
import { dragKline, dragRail } from '@/lib/layout';
import { useUiStore } from '@/lib/store';

/**
 * Draggable divider (spec §6.3 / §11.1).
 *
 * Contract: `role="separator"`, arrow keys move ±16px, `Home`/`End` jump to the
 * limits, double-click restores the default, and `aria-valuenow` announces the
 * current percentage so the control is usable without a mouse.
 */

const STEP_PX = 16;

export interface SplitterProps {
  orientation?: 'vertical';
  /** Current position as a percentage of the container (0-100). */
  value: number;
  min?: number;
  max?: number;
  onDelta: (deltaPx: number) => void;
  onReset: () => void;
  label: string;
  className?: string;
}

export function Splitter({
  value,
  min = 8,
  max = 80,
  onDelta,
  onReset,
  label,
  className,
}: SplitterProps) {
  const [dragging, setDragging] = useState(false);
  const startX = useRef(0);
  const moved = useRef(false);

  const handlePointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    setDragging(true);
    startX.current = event.clientX;
    moved.current = false;
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  }, []);

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      const delta = event.clientX - startX.current;
      if (Math.abs(delta) < 1) return;
      moved.current = true;
      startX.current = event.clientX;
      onDelta(delta);
    },
    [dragging, onDelta],
  );

  const stopDragging = useCallback(() => setDragging(false), []);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      switch (event.key) {
        case 'ArrowLeft':
          event.preventDefault();
          onDelta(-STEP_PX);
          break;
        case 'ArrowRight':
          event.preventDefault();
          onDelta(STEP_PX);
          break;
        case 'Home':
          event.preventDefault();
          onDelta(-10_000);
          break;
        case 'End':
          event.preventDefault();
          onDelta(10_000);
          break;
        case 'Enter':
        case ' ':
          event.preventDefault();
          onReset();
          break;
        default:
          break;
      }
    },
    [onDelta, onReset],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={stopDragging}
      onPointerCancel={stopDragging}
      onDoubleClick={onReset}
      onKeyDown={handleKeyDown}
      title="拖拽调整 · 双击复位"
      data-dragging={dragging || undefined}
      className={cn(
        'group relative z-10 w-px shrink-0 cursor-col-resize bg-[var(--border-subtle)] outline-none',
        'transition-colors duration-[var(--dur-fast)] hover:bg-[var(--accent)]/60',
        'data-[dragging]:bg-[var(--accent)]',
        'focus-visible:bg-[var(--accent)]',
        className,
      )}
    >
      {/* Wider invisible hit area: 1px is impossible to grab. */}
      <span aria-hidden="true" className="absolute inset-y-0 -left-1.5 -right-1.5" />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 top-1/2 h-8 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--border-strong)] opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
      />
    </div>
  );
}

/**
 * Wires both splitters to the store and the CSS tokens (single source of truth,
 * ★P2-06) — the layout arithmetic lives in `lib/layout.ts`.
 */
export function useSplitterHandlers() {
  const layout = useUiStore((state) => state.layout);
  const patchLayout = useUiStore((state) => state.patchLayout);
  const resetLayout = useUiStore((state) => state.resetLayout);
  const viewportRef = useRef<number>(typeof window === 'undefined' ? 1440 : window.innerWidth);

  useEffect(() => {
    const onResize = () => {
      viewportRef.current = window.innerWidth;
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return {
    layout,
    resetLayout,
    handleRailDelta: (delta: number) => {
      const next = dragRail(layout, delta, viewportRef.current);
      patchLayout({ railPx: next.railPx });
    },
    handleChartDelta: (delta: number) => {
      const next = dragKline(layout, delta, viewportRef.current);
      patchLayout({ klineFraction: next.klineFraction });
    },
  };
}
