import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ReadingView, READING_VIEW_KEYS } from '@/components/report/ReadingView';
import { copy } from '@/lib/copy';
import { HOTKEYS } from '@/lib/keyboard';
import type { ReportDoc } from '@/lib/types';

/**
 * 全文视图（§19.3.4 / AC-51 / AC-65）。
 *
 * 这份用例守三条规矩（其余交给 e2e 与 bundle 脚本）：
 *
 * 1. **连续长文**：每一章都在同一根列里渲染出来，块 id 不错位（`thesis#1` 仍是
 *    第二块 —— 与标题同文的 `heading` 被丢掉但 id 不缩水）。
 * 2. **键位零重叠**（AC-65 / ★V6-P1-6）：`READING_VIEW_KEYS ∩ HOTKEYS.keys = ∅`，
 *    所以 `J`/`K` 仍然切标的、`Esc` 仍归全局 `closeOverlay`；`Esc` 只有焦点真在
 *    大纲里时才被本容器消费（捕获阶段 `stopPropagation()`），否则照常冒泡。
 * 3. **深链高亮**：`highlightBlockId` 滚到该块、挂 2000ms 临时高亮后自动清除。
 */

const DOC: ReportDoc = {
  schema: 'aragon.research.report/v1',
  instrument: { symbol: '600519.SH', name: '贵州茅台', market: 'CN', currency: 'CNY' },
  asOf: '2026-02-10T00:00:00Z',
  headline: '趋势偏强，估值处于中性区间',
  rating: { stance: 'buy', conviction: 3, horizon: '6-12m' },
  summary: '示例摘要',
  keyMetrics: [],
  sections: [
    {
      id: 'thesis',
      title: '核心逻辑',
      blocks: [
        // 与章节标题同文：容器已经打印成 `<h2>`，这个块会被丢掉（但 `thesis#1` 仍是下一块）。
        { type: 'heading', level: 2, text: '核心逻辑' },
        { type: 'paragraph', text: '营收同比 +15.2%，主要来自直营渠道改革 [S1]。' },
      ],
    },
    { id: 'fundamentals', title: '基本面', blocks: [{ type: 'bullets', items: [{ text: '直营占比 46%' }] }] },
    { id: 'valuation', title: '估值', blocks: [{ type: 'paragraph', text: '当前 PE 处于近三年中位。' }] },
    { id: 'empty', title: '空章节', blocks: [] },
  ],
  risks: [],
  sources: [
    { id: 'S1', title: '行情与估值快照', publisher: 'akshare', publishedAt: '2026-02-10', url: null },
  ],
  disclaimer: '仅供研究参考。',
};

/** 大纲项（`role="link"` 的按钮，按文档顺序）。 */
function outlineLinks(): HTMLElement[] {
  return screen.getAllByRole('link');
}

/** 父级监听器：用来证明"外泄 / 不外泄"。 */
function Harness({ onParentKeyDown }: { onParentKeyDown: () => void }) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  return (
    <div onKeyDown={onParentKeyDown}>
      <button type="button" ref={triggerRef}>
        阅读模式
      </button>
      <ReadingView reportId="rp_1" doc={DOC} returnFocusRef={triggerRef} />
    </div>
  );
}

describe('ReadingView · 全文视图', () => {
  it('把每一章都渲染进同一根连续列里', () => {
    const { container } = render(<ReadingView reportId="rp_1" doc={DOC} />);

    expect(container.querySelectorAll('[data-reading-section]')).toHaveLength(DOC.sections.length);
    expect(screen.getByRole('heading', { level: 2, name: '核心逻辑' })).toBeInTheDocument();
    expect(screen.getByText(/营收同比 \+15\.2%/)).toBeInTheDocument();
    expect(screen.getByText('直营占比 46%')).toBeInTheDocument();
    expect(screen.getByText('当前 PE 处于近三年中位。')).toBeInTheDocument();
    // 空章节明说"没有内容"，而不是渲染一片空白让人以为坏了。
    expect(screen.getByText(copy.reading.emptySection)).toBeInTheDocument();

    // 块 id 仍然按文档原始下标派生：过滤标题块不影响 `thesis#1`。
    expect(container.querySelector('[data-block-id="thesis#1"]')).not.toBeNull();
    expect(container.querySelector('[data-block-id="thesis#0"]')).toBeNull();
  });

  it('大纲用 aria-current 标出当前章', () => {
    render(<ReadingView reportId="rp_1" doc={DOC} activeSectionId="valuation" />);

    expect(screen.getByRole('link', { name: /估值/ })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('link', { name: /核心逻辑/ })).not.toHaveAttribute('aria-current');
    // 大纲声明了键位（AC-51），并且每项都带上"块数"。
    expect(screen.getByRole('navigation', { name: copy.reading.outline })).toHaveAttribute(
      'aria-keyshortcuts',
      'ArrowUp ArrowDown Home End Enter Escape',
    );
    expect(screen.getByRole('link', { name: /核心逻辑/ })).toHaveTextContent('2 块');
  });

  it('test_outline_keys_do_not_collide', () => {
    const hotkeyKeys = new Set(
      HOTKEYS.flatMap((definition) => definition.keys.map((key) => key.toLowerCase())),
    );
    const overlap = READING_VIEW_KEYS.filter((key) => hotkeyKeys.has(key.toLowerCase()));

    // 交集必须为空：`J`/`K`/`?`/`Esc` 的归属不变（AC-65 / C-17）。
    expect(overlap).toEqual([]);
    // 大纲键位就是这 6 个，Esc 只是写成 `Escape`（与 `HOTKEYS` 的 `Esc` 不同字面量）。
    expect([...READING_VIEW_KEYS]).toEqual([
      'ArrowUp',
      'ArrowDown',
      'Home',
      'End',
      'Enter',
      'Escape',
    ]);
    expect(hotkeyKeys.has('j')).toBe(true);
    expect(hotkeyKeys.has('k')).toBe(true);
  });

  it('↓ 移动大纲焦点，Enter 触发 onSectionChange', async () => {
    const user = userEvent.setup();
    const onSectionChange = vi.fn();
    render(<ReadingView reportId="rp_1" doc={DOC} onSectionChange={onSectionChange} />);

    const links = outlineLinks();
    // 切入全文 → 焦点在首项（AC-51）。
    expect(links[0]).toHaveFocus();

    await user.keyboard('{ArrowDown}');
    expect(links[1]).toHaveFocus();

    await user.keyboard('{End}');
    expect(links[3]).toHaveFocus();

    await user.keyboard('{Home}');
    expect(links[0]).toHaveFocus();

    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');
    expect(onSectionChange).toHaveBeenCalledWith('fundamentals');
  });

  it('Esc 在大纲内只归焦，在大纲外照常冒泡', async () => {
    const user = userEvent.setup();
    const onParentKeyDown = vi.fn();
    render(<Harness onParentKeyDown={onParentKeyDown} />);

    // 焦点在大纲里：本容器消费这次 Esc，父级监听器听不到。
    expect(outlineLinks()[0]).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(onParentKeyDown).not.toHaveBeenCalled();
    // 并且焦点回到模式切换按钮。
    expect(screen.getByRole('button', { name: '阅读模式' })).toHaveFocus();

    // 焦点在正文（引用角标）里：不归我们管，原样冒泡给全局 closeOverlay。
    const chip = screen.getByRole('button', { name: /查看来源 S1/ });
    await user.click(chip);
    expect(chip).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(onParentKeyDown).toHaveBeenCalledTimes(1);
  });

  it('highlightBlockId 滚到该块，2000ms 后清除高亮', () => {
    vi.useFakeTimers();
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView');
    const onHighlightClear = vi.fn();
    const onJumpToBlock = vi.fn();
    try {
      const { container } = render(
        <ReadingView
          reportId="rp_1"
          doc={DOC}
          highlightBlockId="thesis#1"
          onHighlightClear={onHighlightClear}
          onJumpToBlock={onJumpToBlock}
        />,
      );

      const block = container.querySelector('[data-block-id="thesis#1"]');
      expect(block).not.toBeNull();
      expect(block?.textContent).toContain('营收同比');
      expect(scrollSpy).toHaveBeenCalled();
      expect(block).toHaveAttribute('data-highlighted', 'true');
      expect(onJumpToBlock).toHaveBeenCalledWith('thesis#1');
      expect(onHighlightClear).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(2000);
      });

      expect(block).not.toHaveAttribute('data-highlighted');
      expect(onHighlightClear).toHaveBeenCalledTimes(1);
    } finally {
      scrollSpy.mockRestore();
      vi.useRealTimers();
    }
  });
});
