'use client';

import { useEffect, useState, type CSSProperties } from 'react';

export interface VisualViewportSize {
  width: number;
  height: number;
  offsetTop: number;
  offsetLeft: number;
  layoutWidth: number;
}

/** Tracks the visible viewport without retaining listeners after unmount. */
export function useVisualViewport(enabled = true) {
  const [viewport, setViewport] = useState<VisualViewportSize | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let frame = 0;
    const visible = window.visualViewport;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setViewport({
        width: visible?.width ?? window.innerWidth,
        height: visible?.height ?? window.innerHeight,
        offsetTop: visible?.offsetTop ?? 0,
        offsetLeft: visible?.offsetLeft ?? 0,
        layoutWidth: window.innerWidth,
      }));
    };
    update();
    window.addEventListener('resize', update);
    visible?.addEventListener('resize', update);
    visible?.addEventListener('scroll', update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', update);
      visible?.removeEventListener('resize', update);
      visible?.removeEventListener('scroll', update);
    };
  }, [enabled]);
  return viewport;
}

/** CSS variables keep safe-area arithmetic in CSS on every platform. */
export function assistantViewportStyle(
  viewport: VisualViewportSize | null,
): CSSProperties {
  if (!viewport) return {};
  return {
    '--assistant-vh': `${viewport.height}px`,
    '--assistant-vw': `${viewport.width}px`,
    '--assistant-top': `${viewport.offsetTop}px`,
    '--assistant-right': `${Math.max(0, viewport.layoutWidth
      - viewport.offsetLeft - viewport.width)}px`,
  } as CSSProperties;
}
