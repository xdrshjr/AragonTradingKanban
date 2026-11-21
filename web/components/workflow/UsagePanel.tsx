'use client';

import { useEffect, useState } from 'react';

import { adminApi } from '@/lib/api.admin';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import { useWorkflowStore } from '@/lib/workflow/store';
import type { UsageRow } from '@/lib/workflow/types';

/** 近 30 天各版本的生成数、成败、遵循度分布与回退次数（`GET …/usage`）。 */
export function UsageTable({ rows }: { rows: UsageRow[] }) {
  const u = canvasCopy.usage;
  if (rows.length === 0) return <p className="px-3 py-3 text-[12px] text-[var(--text-tertiary)]">{u.empty}</p>;
  return (
    <table className="w-full text-[12px]">
      <thead className="sticky top-0 bg-[var(--bg-surface-2)] text-left text-[11px] text-[var(--text-tertiary)]">
        <tr>
          {[u.version, u.runs, u.succeeded, u.failed, u.adherence, u.fallbacks].map((label) => (
            <th key={label} scope="col" className="px-3 py-1 font-medium">
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.version} className="border-t border-[var(--border-subtle)]">
            <th scope="row" className="px-3 py-1 text-left font-mono font-medium">
              v{row.version}
            </th>
            <td className="px-3 py-1 tabular-nums">{row.runs}</td>
            <td className="px-3 py-1 tabular-nums">{row.succeeded}</td>
            <td className="px-3 py-1 tabular-nums">{row.failed}</td>
            <td className="px-3 py-1 tabular-nums text-[var(--text-secondary)]">
              {u.full} {row.adherence.full} · {u.partial} {row.adherence.partial} · {u.none} {row.adherence.none}
            </td>
            <td className="px-3 py-1 tabular-nums">{row.fallbacks}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function UsagePanel() {
  const templateId = useWorkflowStore((state) => state.templateId);
  const [rows, setRows] = useState<UsageRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!templateId) return;
    let alive = true;
    adminApi.workflows
      .usage(templateId, 30)
      .then((response) => alive && setRows(response.byVersion))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [templateId]);

  if (failed) return <p role="alert" className="px-3 py-3 text-[12px] text-[var(--danger)]">{canvasCopy.plan.failed}</p>;
  if (!rows) return <p role="status" className="px-3 py-3 text-[12px] text-[var(--text-tertiary)]">{canvasCopy.usage.loading}</p>;
  return <UsageTable rows={rows} />;
}
