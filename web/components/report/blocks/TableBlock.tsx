'use client';

import { Check, Copy } from 'lucide-react';
import { useCallback, useState } from 'react';

import { cn } from '@/lib/cn';
import { normalizeNumberToken, ungroundedValues } from '@/lib/grounding';
import type { GroundingQuality, TableBlock as TableBlockType } from '@/lib/types';

import { IconButton } from '../../ui/Button';
import { GroundingDot } from '../GroundingDot';

/**
 * Compare tables: numbers right-aligned, sticky header, TSV copy, horizontal scroll.
 *
 * ★v6（D2 / AC-49）：单元格带 `data-grounded`，未接地的格子挂 `GroundingDot`。
 * 财务表格是纯事实区，所以它是"数字能不能点回去"最该生效的地方。
 */
export function DataTable({
  block,
  quality,
  blockId,
}: {
  block: TableBlockType;
  /** ★v6：`quality.grounding`；缺席则完全不渲染可信度痕迹。 */
  quality?: GroundingQuality | null;
  /** ★v6：块的派生 id（`lib/blocks.ts`）。 */
  blockId?: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const missing = ungroundedValues(quality, blockId);

  const copyTsv = useCallback(async () => {
    const header = block.columns.map((column) => column.label).join('\t');
    const rows = block.rows.map((row) =>
      block.columns.map((column) => String(row[column.key] ?? '')).join('\t'),
    );
    const tsv = [header, ...rows].join('\n');
    try {
      await navigator.clipboard.writeText(tsv);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }, [block]);

  return (
    <figure className="m-0">
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-subtle)]">
        {block.caption ? (
          <figcaption className="flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-1)] px-2.5 py-1.5">
            <span className="text-[11.5px] text-[var(--text-secondary)]">{block.caption}</span>
            <IconButton label={copied ? '已复制' : '复制表格'} onClick={copyTsv} className="!size-6">
              {copied ? <Check className="size-3 text-[var(--accent)]" /> : <Copy className="size-3" />}
            </IconButton>
          </figcaption>
        ) : null}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="bg-[var(--bg-surface-1)]">
                {block.columns.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    className={cn(
                      'whitespace-nowrap border-b border-[var(--border-subtle)] px-2.5 py-1.5',
                      'text-[11px] font-medium text-[var(--text-tertiary)]',
                      alignClass(column.align),
                    )}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="transition-colors hover:bg-[var(--bg-hover)]">
                  {block.columns.map((column) => {
                    const cell = formatCell(row[column.key]);
                    const ungrounded = missing.size > 0 && missing.has(normalizeNumberToken(cell));
                    return (
                      <td
                        key={column.key}
                        data-grounded={ungrounded ? 'false' : undefined}
                        className={cn(
                          'border-b border-[var(--border-subtle)] px-2.5 py-1.5 text-[var(--text-secondary)]',
                          alignClass(column.align ?? 'left'),
                          (column.align ?? 'left') === 'right' && 'num',
                        )}
                      >
                        {ungrounded ? (
                          <span className="inline-flex items-baseline gap-1">
                            {cell}
                            <GroundingDot value={cell} grounded={false} />
                          </span>
                        ) : (
                          cell
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </figure>
  );
}

function alignClass(align?: 'left' | 'right' | 'center' | null): string {
  if (align === 'right') return 'text-right';
  if (align === 'center') return 'text-center';
  return 'text-left';
}

function formatCell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return String(value);
}
