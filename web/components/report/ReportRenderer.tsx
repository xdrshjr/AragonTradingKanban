'use client';

import { AlertTriangle } from 'lucide-react';
import { memo } from 'react';

import type { Block, SourceRef } from '@/lib/types';

import { RichText } from './CitationChip';
import { CalloutCard } from './blocks/CalloutBlock';
import { ChartView } from './blocks/ChartBlock';
import { BulletList, Comparables, QuoteCard, ScenarioTable, Timeline } from './blocks/MiscBlocks';
import { MetricGrid } from './blocks/MetricsBlock';
import { DataTable } from './blocks/TableBlock';

/**
 * Block dispatcher (spec §5.3 / §6.3).
 *
 * `switch` on `type` with an exhaustive never-check, plus a runtime fallback for
 * unknown types so a future schema addition renders a labelled card instead of
 * crashing the page (§5.2 "未知块 → 通用降级卡片").
 */

export interface ReportRendererProps {
  blocks: Block[];
  sources?: SourceRef[];
  onUnknown?: (type: string) => void;
}

const HEADING_CLASS: Record<number, string> = {
  2: 'text-[17px] font-semibold tracking-tight text-[var(--text-primary)] mt-5 first:mt-0',
  3: 'text-[15px] font-semibold text-[var(--text-primary)] mt-4 first:mt-0',
  4: 'text-[13.5px] font-semibold uppercase tracking-wide text-[var(--text-secondary)] mt-3 first:mt-0',
};

function Heading({ level, text }: { level: 2 | 3 | 4; text: string }) {
  const className = HEADING_CLASS[level] ?? HEADING_CLASS[3];
  const id = `h-${slug(text)}`;
  if (level === 2) return <h2 id={id} className={className}>{text}</h2>;
  if (level === 4) return <h4 id={id} className={className}>{text}</h4>;
  return <h3 id={id} className={className}>{text}</h3>;
}

function slug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
}

export function UnknownBlock({ type }: { type: string }) {
  return (
    <div
      data-testid="unknown-block"
      className="flex items-start gap-2 rounded-[var(--radius-md)] border border-dashed border-[var(--border-strong)] bg-[var(--bg-surface-1)] px-2.5 py-2"
    >
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-[var(--warn)]" aria-hidden="true" />
      <p className="text-[11.5px] leading-snug text-[var(--text-tertiary)]">
        未识别的块类型 <code className="mono text-[var(--text-secondary)]">{type || 'unknown'}</code>
        ，已跳过渲染（不影响其余内容）。
      </p>
    </div>
  );
}

function BlockView({ block, sources }: { block: Block; sources: SourceRef[] }) {
  switch (block.type) {
    case 'paragraph':
      return (
        <p className="text-[15px] leading-[1.8] text-[var(--text-primary)]">
          <RichText text={block.text} sources={sources} />
        </p>
      );
    case 'heading':
      return <Heading level={block.level} text={block.text} />;
    case 'bullets':
      return <BulletList items={block.items} sources={sources} />;
    case 'callout':
      return (
        <CalloutCard
          tone={block.tone}
          title={block.title}
          text={block.text}
          renderText={(value: string) => <RichText text={value} sources={sources} />}
        />
      );
    case 'metrics':
      return <MetricGrid items={block.items} compact />;
    case 'table':
      return <DataTable block={block} />;
    case 'chart':
      return <ChartView chart={block.chart} />;
    case 'quote':
      return <QuoteCard text={block.text} attribution={block.attribution} />;
    case 'timeline':
      return <Timeline events={block.events} sources={sources} />;
    case 'comparables':
      return <Comparables peers={block.peers} />;
    case 'scenario':
      return <ScenarioTable scenarios={block.scenarios} />;
    case 'divider':
      return <hr className="my-4 border-0 border-t border-[var(--border-subtle)]" />;
    default: {
      // Exhaustiveness check: if a new block type lands in `Block` and is not
      // handled above, TypeScript fails HERE rather than at runtime.
      const exhaustive: never = block;
      const unknown = exhaustive as { type?: string };
      return <UnknownBlock type={String(unknown?.type ?? 'unknown')} />;
    }
  }
}

export const ReportRenderer = memo(function ReportRenderer({ blocks, sources = [] }: ReportRendererProps) {
  return (
    <div className="report-prose space-y-3">
      {blocks.map((block, index) => (
        <BlockView key={index} block={block} sources={sources} />
      ))}
    </div>
  );
});
