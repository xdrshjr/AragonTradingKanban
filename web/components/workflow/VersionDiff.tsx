'use client';

import { Minus, PenLine, Plus } from 'lucide-react';

import { isEmptyDiff } from '@/lib/workflow/canvasModel';
import { canvasCopy } from '@/lib/workflow/copy.canvas';
import type { VersionDiffSummary } from '@/lib/workflow/types';

interface GroupProps {
  title: string;
  items: { id: string; title: string }[];
  icon: React.ReactNode;
  tone: string;
}

function Group({ title, items, icon, tone }: GroupProps) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title}>
      <h4 className="mb-0.5 flex items-center gap-1 text-[11px] font-medium" style={{ color: tone }}>
        {icon}
        {title} · {items.length}
      </h4>
      <ul className="ml-4 list-disc text-[12px] text-[var(--text-secondary)]">
        {items.map((item) => (
          <li key={item.id}>{item.title || canvasCopy.node.untitled}</li>
        ))}
      </ul>
    </section>
  );
}

/** 结构 diff：新增 / 删除 / 修改的节点列表 + 连线增减计数（图标 + 文字，不只靠颜色）。 */
export function VersionDiff({ diff, firstVersion = false }: { diff: VersionDiffSummary; firstVersion?: boolean }) {
  const d = canvasCopy.diff;
  const summary = d.summary(diff.addedNodes.length, diff.removedNodes.length, diff.changedNodes.length);
  return (
    <div className="flex flex-col gap-2" data-testid="wf-diff">
      <div className="flex items-center justify-between">
        <h3 className="text-[12px] font-medium text-[var(--text-primary)]">{d.title}</h3>
        <span className="font-mono text-[11px] text-[var(--text-tertiary)]">{summary}</span>
      </div>
      {firstVersion ? <p className="text-[12px] text-[var(--text-tertiary)]">{d.firstVersion}</p> : null}
      {isEmptyDiff(diff) && !firstVersion ? <p className="text-[12px] text-[var(--text-tertiary)]">{d.none}</p> : null}
      <Group title={d.addedNodes} items={diff.addedNodes} icon={<Plus aria-hidden="true" className="size-3" />} tone="var(--accent)" />
      <Group title={d.removedNodes} items={diff.removedNodes} icon={<Minus aria-hidden="true" className="size-3" />} tone="var(--danger)" />
      <Group title={d.changedNodes} items={diff.changedNodes} icon={<PenLine aria-hidden="true" className="size-3" />} tone="var(--warn)" />
      {diff.addedEdges + diff.removedEdges > 0 ? (
        <p className="text-[11.5px] text-[var(--text-secondary)]">
          {d.addedEdges} +{diff.addedEdges} · {d.removedEdges} −{diff.removedEdges}
        </p>
      ) : null}
    </div>
  );
}
