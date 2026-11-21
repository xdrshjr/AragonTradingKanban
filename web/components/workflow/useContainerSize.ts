'use client';

import { useEffect, useState, type RefObject } from 'react';

export interface Size {
  width: number;
  height: number;
}

/**
 * 元素尺寸（ResizeObserver）。首帧或 jsdom 里量不到（0）时回落到窗口尺寸，
 * 这样 SSR 之后的第一次渲染与单元测试里都有一个合理的布局，而不是 0 宽的塌缩态。
 */
export function useContainerSize(ref: RefObject<HTMLElement | null>): Size {
  const [size, setSize] = useState<Size>(() => ({
    width: typeof window === 'undefined' ? 1280 : window.innerWidth,
    height: typeof window === 'undefined' ? 800 : window.innerHeight,
  }));

  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = (): void => {
      const rect = element.getBoundingClientRect();
      const width = rect.width || window.innerWidth;
      const height = rect.height || window.innerHeight;
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [ref]);

  return size;
}
