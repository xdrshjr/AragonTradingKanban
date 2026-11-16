'use client';

import { FileText, Sparkles, Timer } from 'lucide-react';

import { copy } from '@/lib/copy';

import { GenerateButton } from '../generation/GenerateButton';

export interface EmptyReportStateProps {
  instrumentName: string;
  onGenerate: () => void;
  generating?: boolean;
  disabled?: boolean;
  estimatedCost?: string | null;
}

/**
 * Empty state that is not blank (spec §10.3): it says exactly what will be
 * produced, roughly how long it takes, and where the numbers come from.
 */
export function EmptyReportState({
  instrumentName,
  onGenerate,
  generating,
  disabled,
  estimatedCost,
}: EmptyReportStateProps) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-[440px] text-center">
        <div
          aria-hidden="true"
          className="mx-auto mb-4 flex size-11 items-center justify-center rounded-[var(--radius-md)] border border-[var(--border-strong)] bg-[var(--bg-surface-2)]"
        >
          <FileText className="size-5 text-[var(--text-tertiary)]" />
        </div>
        <h2 className="text-[16px] font-semibold tracking-tight text-[var(--text-primary)]">
          {copy.report.emptyTitle}
        </h2>
        <p className="mt-1 text-[12.5px] text-[var(--text-secondary)]">
          {instrumentName} 还没有投研报告。{copy.report.emptyLead}
        </p>
        <ul className="mx-auto mt-3 space-y-1.5 text-left">
          {copy.report.emptyBullets.map((item) => (
            <li key={item} className="flex items-start gap-2 text-[12px] leading-snug text-[var(--text-secondary)]">
              <Sparkles className="mt-0.5 size-3 shrink-0 text-[var(--accent)]" aria-hidden="true" />
              {item}
            </li>
          ))}
        </ul>
        <div className="mt-4 flex flex-col items-center gap-2">
          <GenerateButton onGenerate={onGenerate} generating={generating} disabled={disabled} size="lg">
            {copy.report.generate}
          </GenerateButton>
          <p className="flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
            <Timer className="size-3" aria-hidden="true" />
            {copy.report.emptyEta}
            {estimatedCost ? ` · ${estimatedCost}` : ''}
          </p>
        </div>
      </div>
    </div>
  );
}
