'use client';

import { ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import { jumpHref, trustState, trustText, type TrustState } from '@/lib/grounding';
import type { GroundingQuality, GroundingSample } from '@/lib/types';

/**
 * 报告头的数字可信度徽标（spec §19.3.2 / D2 / AC-50）。
 *
 * 为什么必须是一个"先给结论、再给坐标"的控件：
 * 一份降级报告里读者真正会问两件事 —— "有几个数字没依据"和"是哪几个"。所以徽标本身只说
 * 一句话（三态文案由 `lib/grounding.ts` 的 `trustState()` 唯一裁决），展开后每条样本都是
 * 一个可点的 `role="link"` 按钮，直接跳到那个数字所在的块。
 *
 * 三条纪律：
 * 1. **没有 grounding 数据 → 返回 `null`**（`absent`），不渲染"暂无数据"的空徽标。
 *    早期报告真的没有这份记录，渲染一个空壳只会让人以为"检查过了，零问题"。
 * 2. **数据只来自调用方传入的当前版本 `quality`**，不读 meta、不发请求（I-31：徽标
 *    说的版本必须和页面显示的版本是同一份数据）。
 * 3. 文案全部来自 `copy.trust`，组件不自己拼句子；`skipped` 优先级由 `trustState` 处理，
 *    这里**没有第二个 `if`** —— 否则"数字太少，未评估"与"已核验 3/3"会各说各话。
 */

export interface TrustBadgeProps {
  /** 当前版本的 `report.quality.grounding`；`null`/`undefined` 时整块不渲染。 */
  quality?: GroundingQuality | null;
  /** 深链目标 (`?report=`) —— 样本按钮带上它，右键"在新标签打开"才不会丢版本。 */
  reportId: string;
  /** 点样本时回调：由 `ReportPanel` 负责滚动 + 高亮（组件自己不动 DOM）。 */
  onJump?: (blockId: string) => void;
}

/** 三态语义色。琥珀只留给"未接地"，已核验用上涨色，样本不足保持中性（不下判断）。 */
const TONE: Record<Exclude<TrustState['kind'], 'absent'>, string> = {
  insufficient: 'border-[var(--border-strong)] text-[var(--text-secondary)] bg-[var(--bg-surface-2)]',
  verified: 'border-[var(--chart-up)]/40 text-[var(--chart-up)] bg-[var(--chart-up)]/10',
  missing: 'border-[var(--warn)]/40 text-[var(--warn)] bg-[var(--warn-weak)]',
};

export function TrustBadge({ quality, reportId, onJump }: TrustBadgeProps) {
  const [open, setOpen] = useState(false);
  const state = trustState(quality);

  // 样本必须稳定引用：每次渲染都 `quality.samples ?? []` 会让下面 useMemo 全部失效。
  const samples = useMemo<GroundingSample[]>(
    () => (quality && Array.isArray(quality.samples) ? quality.samples : []),
    [quality],
  );

  // `absent` 就是"不渲染"，不是"渲染成 0 问题"。
  if (!quality || state.kind === 'absent') return null;

  const text = trustText(state) ?? '';
  const tone = TONE[state.kind];

  // 展开区里的次要说明：只在数量 > 0 时出现（0 行没有信息量，会把面板撑空）。
  // `copy.trust.none` 只在**真的一处未接地都没有**时才成立：I-40 把分支②放宽成
  // `ratio >= 0.9`，于是 48/51（3 个数字未接地）也是 `verified`——此时再补一句
  // "所有数字都能在快照里找到依据"就是自相矛盾的假话。
  const notes: string[] = [];
  if (state.kind === 'verified' && state.ok === state.total) notes.push(copy.trust.none);
  if (quality.totalMissing > samples.length) {
    notes.push(fill(copy.trust.more, { shown: samples.length, total: quality.totalMissing }));
  }
  if (quality.projectionClaims > 0) {
    notes.push(fill(copy.trust.projections, { count: quality.projectionClaims }));
  }
  if (quality.identifierClaims > 0) {
    notes.push(fill(copy.trust.identifiers, { count: quality.identifierClaims }));
  }
  if (quality.derivedClaims > 0) {
    notes.push(fill(copy.trust.derived, { count: quality.derivedClaims }));
  }

  const expandable = samples.length > 0 || notes.length > 0;

  return (
    <div className="relative inline-flex">
      <button
        type="button"
        data-testid="trust-badge"
        // 可访问名 = 标签 + 结论（读屏用户听到"数字可信度：3 个数字未找到依据"，而不是只有"按钮"）。
        aria-label={`${copy.trust.label}：${text}`}
        // 没有可展开内容时**不**声称自己是可展开控件（`undefined` 而不是 `false`），
        // 否则读屏用户会被告知"已折叠"然后点开一片空白。
        aria-expanded={expandable ? open : undefined}
        onClick={() => {
          if (!expandable) return;
          setOpen((value) => !value);
        }}
        className={cn(
          'inline-flex items-center gap-1 rounded-full border px-1.5 py-px',
          'text-[10.5px] font-medium leading-[16px] whitespace-nowrap transition-colors',
          tone,
          expandable ? 'cursor-pointer hover:brightness-110' : 'cursor-default',
        )}
      >
        <span>{text}</span>
        {expandable ? (
          <ChevronDown
            className={cn('size-2.5 transition-transform', open ? 'rotate-180' : null)}
            aria-hidden="true"
          />
        ) : null}
      </button>

      {expandable && open ? (
        <div
          data-testid="trust-badge-panel"
          className={cn(
            'absolute left-0 top-6 z-30 w-[min(360px,80vw)] rounded-[var(--radius-md)]',
            'border border-[var(--border-strong)] bg-[var(--bg-surface-2)] p-1 shadow-[var(--shadow-pop)]',
          )}
        >
          {samples.length > 0 ? (
            <ul className="max-h-[260px] overflow-y-auto">
              {samples.map((sample, index) => (
                <li key={`${sample.blockId}-${sample.path}-${index}`}>
                  <button
                    type="button"
                    role="link"
                    // 深链目标：`onJump` 存在时点它滚动高亮，不存在时按钮仍是普通链接语义。
                    data-href={jumpHref(reportId, sample.blockId)}
                    title={copy.trust.jump}
                    onClick={() => onJump?.(sample.blockId)}
                    className={cn(
                      'block w-full rounded-[var(--radius-sm)] px-1.5 py-1 text-left transition-colors',
                      'hover:bg-[var(--bg-hover)] focus-visible:bg-[var(--bg-hover)]',
                    )}
                  >
                    <span className="flex items-baseline gap-1.5">
                      <span className="mono text-[11px] font-semibold text-[var(--warn)]">{sample.raw}</span>
                      <span className="mono truncate text-[10px] text-[var(--text-tertiary)]">{sample.blockId}</span>
                      <span className="mono truncate text-[10px] text-[var(--text-tertiary)]">{sample.path}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-[11.5px] text-[var(--text-secondary)]">
                      {sample.text}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}

          {notes.length > 0 ? (
            <ul
              className={cn(
                'space-y-0.5 px-1.5 py-1 text-[10.5px] text-[var(--text-tertiary)]',
                samples.length > 0 ? 'border-t border-[var(--border-subtle)]' : null,
              )}
            >
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
