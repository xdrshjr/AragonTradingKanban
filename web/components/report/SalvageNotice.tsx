'use client';

import { EyeOff, TriangleAlert } from 'lucide-react';
import { useState } from 'react';

import { copy, fill } from '@/lib/copy';
import type { QualitySalvage } from '@/lib/types';

export interface SalvageNoticeProps {
  salvage?: QualitySalvage | null;
}

/**
 * ★v9（K5 / §21.3.6）：**抢救过的报告必须可见**。
 *
 * 为什么不能悄悄抢救（裁决 K-11）：一份"少了两块"的报告被当成完整报告，会让用户拿它
 * 去做判断——这正是 v5 §19 立下"只标不改数"的同一个理由。所以这里：
 * 1. 顶部一条可展开的提示，说明"有 N 处格式不合法，已移除后展示，建议重新生成"；
 * 2. `N` **由服务端算好的两个数相加**（`droppedKeys + droppedBlocks + droppedSections`），
 *    前端不拼第二个真源；
 * 3. 展开后逐条列出 `reasons`（每条 ≤80 字符、最多 5 条，服务端已经截过）。
 *
 * `salvage` 缺失（老报告）或 `used === false` ⇒ 组件返回 `null`（不渲染任何东西，
 * 这是"可缺失"的读路径口径，不回填）。
 */
export function SalvageNotice({ salvage }: SalvageNoticeProps) {
  const [open, setOpen] = useState(false);
  if (!salvage || !salvage.used) return null;

  // 三个计数器相加在这里做，但它们各自只在服务端被数过一次（`SalvageReport`）；
  // 占位符用 `fill()` 填，与 `copy.ts` 里其余文案同一个写法 —— `.replace()` 链在
  // 少改一个键时不会报错，只会把 `{blocks}` 原样漏到界面上。
  const total = (salvage.droppedKeys ?? 0) + (salvage.droppedBlocks ?? 0) + (salvage.droppedSections ?? 0);
  const body = fill(copy.salvage.body, {
    total,
    blocks: salvage.droppedBlocks ?? 0,
    keys: salvage.droppedKeys ?? 0,
  });

  return (
    <div
      data-testid="salvage-notice"
      data-total={total}
      className="mb-2 rounded-[var(--radius-sm)] border border-[var(--warn)]/35 bg-[var(--warn-weak)] px-2.5 py-1.5 text-[11px] leading-snug text-[var(--warn)]"
    >
      <div className="flex items-start gap-1.5">
        <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p>{body}</p>
          {salvage.reasons?.length ? (
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              className="mt-0.5 inline-flex items-center gap-1 text-[10.5px] underline decoration-dotted"
              aria-expanded={open}
            >
              <EyeOff className="size-2.5" aria-hidden="true" />
              {copy.salvage.expand}
            </button>
          ) : null}
          {open ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[10.5px]">
              {salvage.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}
