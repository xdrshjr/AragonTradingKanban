'use client';

import { Info, Lightbulb, TriangleAlert } from 'lucide-react';

import { cn } from '@/lib/cn';
import type { Tone } from '@/lib/types';

const STYLES: Record<Tone, { bar: string; bg: string; icon: React.ReactNode; label: string }> = {
  info: {
    bar: 'bg-[var(--info)]',
    bg: 'bg-[var(--info-weak)]',
    icon: <Info className="size-3.5" />,
    label: '说明',
  },
  warn: {
    bar: 'bg-[var(--warn)]',
    bg: 'bg-[var(--warn-weak)]',
    icon: <TriangleAlert className="size-3.5" />,
    label: '注意',
  },
  insight: {
    bar: 'bg-[var(--accent)]',
    bg: 'bg-[var(--accent-weak)]',
    icon: <Lightbulb className="size-3.5" />,
    label: '洞察',
  },
};

const TEXT: Record<Tone, string> = {
  info: 'text-[var(--info)]',
  warn: 'text-[var(--warn)]',
  insight: 'text-[var(--accent)]',
};

export function CalloutCard({
  tone,
  title,
  text,
  renderText,
}: {
  tone: Tone;
  title: string;
  text: string;
  renderText?: (value: string) => React.ReactNode;
}) {
  const style = STYLES[tone] ?? STYLES.info;
  return (
    <aside
      className={cn(
        'relative overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)] pl-3 pr-3 py-2.5',
        style.bg,
      )}
    >
      <span aria-hidden="true" className={cn('absolute inset-y-0 left-0 w-[3px]', style.bar)} />
      <div className="flex items-start gap-2">
        <span className={cn('mt-[3px] shrink-0', TEXT[tone])} aria-hidden="true">
          {style.icon}
        </span>
        <div className="min-w-0">
          <p className={cn('text-[12.5px] font-semibold leading-snug', TEXT[tone])}>{title}</p>
          <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--text-secondary)]">
            {renderText ? renderText(text) : text}
          </p>
        </div>
      </div>
    </aside>
  );
}
