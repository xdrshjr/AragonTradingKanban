'use client';

import { useMemo, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { indexSamples, sampleLabel } from '@/lib/grounding';
import type { GroundingQuality, GroundingSample } from '@/lib/types';

/**
 * 块级数字脚注（spec §19.3.2 / D2 / AC-49）。
 *
 * 为什么需要它：徽标只说"这份报告有几个数字没依据"，而段落里那句话本身没有可标注的位置
 * （O-7：正文不引入第二套渲染路径，段落不会被改写成逐 token 组件）。于是退一步 ——
 * 在**块下方**加一行细字："12 个数字 · 1 个未接地"，展开后列出失配的原值，点一下跳到该处。
 *
 * 保守裁决（宁可少说，不可乱说）：
 * - `quality` 缺席（早期报告）→ `null`。没有核验记录不等于"零问题"。
 * - 该块没有任何样本 → `null`。客户端只拿得到**失配样本**，拿不到"该块 claim 总数"，
 *   所以"这个块有没有 claim"只能由样本推断；没有样本就闭嘴，不猜。
 *
 * 因此脚注里的计数只覆盖"我们真看得见的那些数字"：`count` 与 `missing` 同源。
 * 要把"12 个数字"说准，需要后端暴露每块 claim 计数 —— 本轮没有，也不在此编造。
 */

export interface BlockFootnoteProps {
  /** 当前版本的 `report.quality.grounding`。 */
  quality?: GroundingQuality | null;
  /** 该块在单一派生规则下的 id（`lib/blocks.ts`）。 */
  blockId: string;
  /** 点样本时回调；由报告面板负责滚动与高亮。 */
  onJump?: (blockId: string) => void;
  /**
   * 可选的现成样本（调用方已经按 blockId 索引过时传进来，避免重复查表）。
   * 传 `[]` 与不传等价：都表示"这个块没有失配样本"。
   */
  samples?: GroundingSample[];
}

export function BlockFootnote({ quality, blockId, onJump, samples }: BlockFootnoteProps) {
  const [open, setOpen] = useState(false);

  // 有现成样本就用现成的；否则查一次索引（`indexSamples` 对同一份载荷有 WeakMap 缓存）。
  const own = useMemo<GroundingSample[]>(() => {
    if (samples) return samples;
    if (!quality || !blockId) return [];
    return indexSamples(quality).get(blockId) ?? [];
  }, [samples, quality, blockId]);

  if (!quality || !blockId || own.length === 0) return null;

  const missing = own.length;
  // 客户端只知道失配条数；`count` 与 `missing` 同源（见文件头的保守裁决）。
  const text =
    missing > 0
      ? fill(copy.trust.footnoteMissing, { count: own.length, missing })
      : fill(copy.trust.footnoteAll, { count: own.length });

  return (
    <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10.5px] leading-[15px]">
      <span className="text-[var(--warn)]">{text}</span>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          'shrink-0 rounded px-1 py-px text-[10px] text-[var(--text-tertiary)] transition-colors',
          'hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]',
        )}
      >
        {open ? copy.trust.collapse : copy.trust.expand}
      </button>

      {open ? (
        <ul
          data-testid="block-footnote-samples"
          className="mt-0.5 w-full space-y-0.5 border-l border-[var(--border-strong)] pl-1.5"
        >
          {own.map((sample, index) => (
            <li key={`${sample.path}-${index}`} className="flex items-baseline gap-1.5">
              {onJump ? (
                <button
                  type="button"
                  role="link"
                  aria-label={sampleLabel(sample)}
                  title={copy.trust.jump}
                  onClick={() => onJump(blockId)}
                  className="mono shrink-0 rounded px-0.5 text-[10.5px] font-semibold text-[var(--warn)] underline decoration-dotted underline-offset-2"
                >
                  {sample.raw}
                </button>
              ) : (
                // 没有跳转回调时**不**渲染一个点了没反应的按钮，退化成纯文本。
                <span className="mono shrink-0 text-[10.5px] font-semibold text-[var(--warn)]">{sample.raw}</span>
              )}
              <span className="truncate text-[10.5px] text-[var(--text-tertiary)]">{sample.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
