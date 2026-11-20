'use client';

import { forwardRef, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

export interface ChartPaneProps {
  name: 'main' | 'volume' | 'oscillator';
  label: string;
  children?: ReactNode;
  className?: string;
}

/** Provides a stable canvas host and an independently readable pane legend. */
export const ChartPane = forwardRef<HTMLDivElement, ChartPaneProps>(
  function ChartPane({ name, label, children, className }, ref) {
    return (
      <section
        className={cn('chart-pane', `chart-pane-${name}`, className)}
        aria-label={label}
        data-chart-pane={name}
      >
        <div className="chart-pane-legend" aria-label={`${label}图例`}>
          {children ?? label}
        </div>
        <div ref={ref} className="chart-pane-canvas" />
      </section>
    );
  },
);
