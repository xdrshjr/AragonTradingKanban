'use client';

/**
 * 「正在生成」这一条（spec §23.3.7 / U6）。
 *
 * 为什么它必须存在：用户点了生成之后最想问的是"我刚才那一次在跑吗"。列表里没有它，
 * 就只能靠右下角的 overlay 猜。`active` 的行来自 `runs` 表里 `status in ACTIVE_STATUSES`
 * 的那几行（★P2-8 逐字段对齐既有列），最多 3 条——再多就不是"正在生成"而是"排队事故"。
 */

import Link from 'next/link';
import { Loader2 } from 'lucide-react';

import { copy } from '@/lib/copy';
import type { ActiveRunItem } from '@/lib/types';

export interface ActiveRunsStripProps {
  active: ActiveRunItem[];
}

export function ActiveRunsStrip({ active }: ActiveRunsStripProps) {
  if (active.length === 0) return null;
  return (
    <ul aria-live="polite" className="mb-2 flex flex-col gap-1">
      {active.map((run) => (
        <li key={run.runId}>
          <Link
            href={`/${encodeURIComponent(run.symbol)}`}
            className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--accent)]/25 bg-[var(--accent-weak)] px-2 py-1.5 text-[11.5px] text-[var(--accent)] hover:brightness-110"
          >
            <Loader2 className="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <span className="truncate">
              {copy.library.generating} {run.name || run.symbol}
              {run.stage ? ` · ${run.stage}` : ''} {run.pct}%
            </span>
            <span className="ml-auto shrink-0 text-[10.5px] underline">{copy.library.generatingOpen}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
