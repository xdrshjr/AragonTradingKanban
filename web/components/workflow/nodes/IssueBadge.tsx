'use client';

import { AlertTriangle, XOctagon } from 'lucide-react';

import { canvasCopy } from '@/lib/workflow/copy.canvas';

/** 校验角标：图标 + 数字 + 读屏文本（不只靠颜色，AC-W35）。 */
export function IssueBadge({ errors, warnings }: { errors: number; warnings: number }) {
  if (errors === 0 && warnings === 0) return null;
  const isError = errors > 0;
  const count = isError ? errors : warnings;
  const label = isError ? canvasCopy.node.errors(errors) : canvasCopy.node.warnings(warnings);
  const Icon = isError ? XOctagon : AlertTriangle;
  return (
    <span
      title={label}
      className="inline-flex items-center gap-0.5 rounded px-1 text-[10px] font-medium leading-4"
      style={{
        color: isError ? 'var(--danger)' : 'var(--warn)',
        background: isError ? 'var(--danger-weak)' : 'var(--warn-weak)',
      }}
    >
      <Icon aria-hidden="true" className="size-3" />
      <span aria-hidden="true">{count}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}
