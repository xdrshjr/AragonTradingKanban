'use client';

import { forwardRef } from 'react';

import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import type { Section } from '@/lib/types';

/**
 * 左浮层章节大纲（spec §19.3.4 / AC-51）。
 *
 * **为什么这里不处理键盘。** ★v6 V6-P1-6 明确裁决：大纲的 `↑/↓/Home/End/Enter/Esc`
 * 只在 `ReadingView` 容器的 `onKeyDown` 里生效，**绝不写进 `web/lib/keyboard.ts` 的
 * `HOTKEYS`**（写进去就等于在任何模式、任何页面上都生效；而 `J`/`K` 已被"切标的"
 * 占用，读者按一下 `j` 报告就没了）。所以本组件只做三件事，不再多一件：
 *
 * 1. 章节渲染成 `<button role="link">` —— 不用 `<a>`，避免点击触发 `next/link` 路由；
 * 2. 用 `data-outline-item={section.id}` 交出"可聚焦项 + 它对应哪一章"的坐标，
 *    父容器靠它做焦点移动（只此一个真源，不新增第二处键位映射）；
 * 3. 用 `aria-current="true"` 表达当前章（滚动高亮由父容器算，本组件不猜）。
 *
 * `ref` 指向外层导航容器：父容器拿它在 DOM 里取 `[data-outline-item]`。
 *
 * 文案全部来自 `copy.reading`（`mode/tabs/full` 属于报告头的模式切换，不在这里）。
 */

export interface ReportOutlineProps {
  sections: Section[];
  /** 当前章 —— 由父容器（滚动高亮 / 深链）决定，本组件只负责标记。 */
  activeId?: string;
  onNavigate: (sectionId: string) => void;
  className?: string;
}

export const ReportOutline = forwardRef<HTMLDivElement, ReportOutlineProps>(function ReportOutline(
  { sections, activeId, onNavigate, className },
  ref,
) {
  return (
    <div
      ref={ref}
      role="navigation"
      aria-label={copy.reading.outline}
      data-report-outline=""
      // 键位在此一并声明（AC-51）：读屏器能播报"这个导航支持哪些键"，
      // 而真正的事件处理在 ReadingView —— 声明与实现不放在同一层是有意的。
      aria-keyshortcuts="ArrowUp ArrowDown Home End Enter Escape"
      className={cn(
        'flex flex-col gap-0.5 rounded-[var(--radius-md)] border border-[var(--border-subtle)]',
        'bg-[var(--bg-surface-1)]/95 p-1.5 backdrop-blur',
        className,
      )}
    >
      <p className="px-1.5 pb-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
        {copy.reading.outline}
      </p>

      <ol className="flex flex-col gap-px">
        {sections.map((section) => {
          const active = section.id === activeId;
          return (
            <li key={section.id}>
              <button
                type="button"
                // `role="link"` + `<button>`：读屏器按"跳转"播报，但点下去只滚动、
                // 不改路由（`<a href>` 会触发导航，全文视图里那是错的）。
                role="link"
                data-outline-item={section.id}
                aria-current={active ? 'true' : undefined}
                onClick={() => onNavigate(section.id)}
                className={cn(
                  'flex w-full items-baseline justify-between gap-2 rounded-[var(--radius-sm)] px-1.5 py-1',
                  'text-left text-[11.5px] transition-colors duration-[var(--dur-fast)]',
                  active
                    ? 'bg-[var(--bg-surface-3)] text-[var(--text-primary)]'
                    : 'text-[var(--text-tertiary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-secondary)]',
                )}
              >
                <span className="min-w-0 truncate">{section.title}</span>
                <span className="shrink-0 text-[10px] tabular-nums text-[var(--text-tertiary)]">
                  {fill(copy.reading.outlineCount, { count: section.blocks.length })}
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      <p className="px-1.5 pt-0.5 text-[10px] leading-snug text-[var(--text-tertiary)]">
        {copy.reading.outlineHint}
      </p>
    </div>
  );
});
