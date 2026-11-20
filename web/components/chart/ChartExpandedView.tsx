'use client';

import { useCallback, type RefCallback } from 'react';
import { isEditableTarget } from '@/lib/keyboard';

import { Dialog, DialogContent } from '../ui/Dialog';

export interface ChartExpandedViewProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTarget: RefCallback<HTMLDivElement>;
  consumeEscape?: (event: KeyboardEvent) => boolean;
  returnFocus?: () => HTMLElement | null;
}

/** Hosts the sole workbench portal without owning queries or charts. */
export function ChartExpandedView({
  open, onOpenChange, onTarget, consumeEscape, returnFocus,
}: ChartExpandedViewProps) {
  const restoreFocus = useCallback((event: Event) => {
    const target = returnFocus?.();
    if (!target?.isConnected) return;
    event.preventDefault();
    target.focus();
  }, [returnFocus]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="扩展 K 线工作台"
        description="行情、指标与区间工具；Escape 先退出当前图表操作。"
        className="chart-expanded !h-[96dvh] !w-[96vw] !max-w-[1600px]"
        hideClose
        onEscapeKeyDown={(event) => {
          if (isEditableTarget(event.target)) {
            event.preventDefault(); event.stopPropagation(); return;
          }
          if (!consumeEscape?.(event)) return;
          event.preventDefault();
          event.stopPropagation();
        }}
        onCloseAutoFocus={restoreFocus}
      >
        <div ref={onTarget} className="chart-expanded-target h-full min-h-0" />
      </DialogContent>
    </Dialog>
  );
}
