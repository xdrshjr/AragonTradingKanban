'use client';

import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { blockIdOf } from '@/lib/blocks';
import { cn } from '@/lib/cn';
import { copy, fill } from '@/lib/copy';
import type { Block, GroundingQuality, ReportDoc, Section } from '@/lib/types';

import { ReportOutline } from './ReportOutline';
import { ReportRenderer } from './ReportRenderer';

/**
 * 全文视图容器（spec §19.3.4 / AC-51 / AC-52 / AC-64 / AC-65）。
 *
 * **为什么不新写渲染器。** `ReportRenderer` 已经把 12 种块分派干净、`data-block-id`
 * 也挂好了（v4 成果）。全文视图只是**换容器**：把章节 Tab 换成一根连续长文，
 * 块组件一行不改 —— 两套渲染 = 两份真源 = 迟早分叉。
 *
 * **为什么键位只在这里。** ★v6 V6-P1-6：v5 曾写"大纲 `j/k` 上下"，而
 * `web/lib/keyboard.ts::HOTKEYS` 里 `J` 已经是 `nextSymbol`、`K` 是 `prevSymbol`、
 * `Esc` 是全局 `closeOverlay`。照字面做，读者在长文里按一下 `j`，正在读的报告会被
 * 切走 —— 这是"读长文时最不能出的错"。三条硬约束：
 *
 * 1. 大纲导航用 `↑/↓/Home/End`，**只在本文容器的 `onKeyDown` 里**生效，
 *    不写进 `HOTKEYS`（写进去等于任何模式任何页面都生效）；`j`/`k` 完全不碰。
 * 2. `Esc` 走**捕获阶段**：只有焦点真在大纲里、本组件真的消费了这次按键，
 *    才 `stopPropagation()`；否则原样放行给全局 `closeOverlay` —— 一次按键一个副作用。
 * 3. 声明的键位集合导出为 `READING_VIEW_KEYS`，供测试与仓库守卫断言
 *    "与 `HOTKEYS` 交集为空"（AC-65）。
 *
 * **焦点归还为什么没用 `useReturnFocus`。** 那个 hook 的契约是"面板打开/关闭 +
 * 在 **window** 上听 `Esc`"：把它挂在这里，任何一次 `Esc`（包括本该关闭浮层的那次）
 * 都会顺手把焦点抢到模式切换按钮上 —— 正是 AC-65 要消除的第二个副作用。
 * 本组件不是可开关的面板（挂着就等于开着），所以只归还**消费掉的那一次** `Esc` 的焦点，
 * 并且只调用 `.focus()`，不新增第三个全局监听。
 *
 * 进度线：`scrollTop / scrollHeight`；`prefers-reduced-motion: reduce` 下退化成
 * 5 档离散值（2px 的线本来就没什么动画，但"减少动画"是承诺，不是可选项）。
 */

/** 大纲声明支持的键（AC-65 守卫读取这一项；Esc 只在捕获阶段拦截）。 */
export const READING_VIEW_KEYS = ['ArrowUp', 'ArrowDown', 'Home', 'End', 'Enter', 'Escape'] as const;

export type ReadingViewKey = (typeof READING_VIEW_KEYS)[number];

/** 高亮时长：与 v4 的 `chart:focus` 时序一致（AC-51"高亮 2s"）。 */
const HIGHLIGHT_MS = 2000;

/** 减少动画偏好下的进度档数（0/20/40/60/80/100，也就是 5 段）。 */
const REDUCED_MOTION_STEPS = 5;

/** 判定"当前章"的顶部容差：章节标题越过容器顶部这么多就算读到了。 */
const ACTIVE_SECTION_OFFSET = 24;

const FOCUS_OPTIONS: FocusOptions = { preventScroll: true };

export interface ReadingViewProps {
  /** 报告 id —— 只用来做容器标识与换报告时归零，不是数据源。 */
  reportId: string;
  doc: ReportDoc;
  /** 初始/外部当前章（深链 `?view=full&section=`）；滚动后会由容器自己接管。 */
  activeSectionId?: string;
  /** 深链 `?view=full&block=`：滚到该块并高亮 2000ms。 */
  highlightBlockId?: string | null;
  onSectionChange?: (sectionId: string) => void;
  /** 刚刚落到了哪一段（父组件可用来同步 URL / 联动条高亮）。 */
  onJumpToBlock?: (blockId: string) => void;
  /** 高亮结束（2000ms 到点）—— 父组件据此把 `highlightBlockId` 复位。 */
  onHighlightClear?: () => void;
  /** 模式切换按钮：`Esc` 时把焦点还给它（AC-51）。 */
  returnFocusRef?: RefObject<HTMLElement | null>;
  /** ★v6 G1：`quality.grounding`，透传给 `ReportRenderer` 的块脚注与结构化组件。 */
  quality?: GroundingQuality | null;
  className?: string;
}

/** 章节内的一条渲染单元：块本体 + 派生 id（`lib/blocks.ts` 是唯一派生处）。 */
interface SectionItem {
  block: Block;
  blockId: string;
}

/**
 * 章节 → 渲染项。
 *
 * 与 `ReportPanel` 同一条约定：章节标题已经由容器打印成 `<h2>`，所以与标题同文的
 * `heading` 块丢掉；但 `blockId` 仍然按**文档原始下标**派生（先 map 再 filter），
 * 否则 `sectionId#index` 会错位，深链就会跳错段。
 */
function buildSectionItems(section: Section): SectionItem[] {
  return section.blocks
    .map((block, index) => ({ block, blockId: blockIdOf(section.id, index) }))
    .filter(({ block }) => !(block.type === 'heading' && block.text.trim() === section.title.trim()));
}

export function ReadingView({
  reportId,
  doc,
  activeSectionId,
  highlightBlockId,
  onSectionChange,
  onJumpToBlock,
  onHighlightClear,
  returnFocusRef,
  quality,
  className,
}: ReadingViewProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const outlineRef = useRef<HTMLDivElement | null>(null);
  const rootRef = useRef<HTMLElement | null>(null);

  const sections = doc.sections;
  const firstSectionId = sections[0]?.id ?? '';

  const [currentSectionId, setCurrentSectionId] = useState<string>(activeSectionId ?? firstSectionId);
  const [progress, setProgress] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);

  /** 回调放 ref：定时器/滚动监听只认最新一次，但不因父组件内联箭头函数而重建。 */
  const onHighlightClearRef = useRef(onHighlightClear);
  const onJumpToBlockRef = useRef(onJumpToBlock);

  useEffect(() => {
    onHighlightClearRef.current = onHighlightClear;
    onJumpToBlockRef.current = onJumpToBlock;
  }, [onHighlightClear, onJumpToBlock]);

  /* ── 章节内容（块 id 先派生再过滤，见 buildSectionItems 注释）──────────────── */
  const sectionItems = useMemo(
    () => sections.map((section) => ({ section, items: buildSectionItems(section) })),
    [sections],
  );

  const blocksInDoc = useMemo(
    () => sectionItems.reduce((total, entry) => total + entry.items.length, 0),
    [sectionItems],
  );

  /* ── 减少动画偏好 ──────────────────────────────────────────────────────────── */
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(query.matches);
    const listener = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
    query.addEventListener('change', listener);
    return () => query.removeEventListener('change', listener);
  }, []);

  /* ── DOM 查找（不用 CSS.escape 拼选择器：id 里可能带 `.`/`#`）──────────────── */
  const outlineItems = useCallback(
    (): HTMLButtonElement[] =>
      Array.from(outlineRef.current?.querySelectorAll<HTMLButtonElement>('[data-outline-item]') ?? []),
    [],
  );

  const findSectionEl = useCallback((sectionId: string): HTMLElement | null => {
    const nodes = Array.from(
      scrollRef.current?.querySelectorAll<HTMLElement>('[data-reading-section]') ?? [],
    );
    return nodes.find((node) => node.dataset.readingSection === sectionId) ?? null;
  }, []);

  const findBlockEl = useCallback((blockId: string): HTMLElement | null => {
    const pick = (root: ParentNode | null): HTMLElement | null => {
      if (!root) return null;
      const nodes = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]'));
      return nodes.find((node) => node.dataset.blockId === blockId) ?? null;
    };
    return pick(scrollRef.current) ?? pick(typeof document === 'undefined' ? null : document);
  }, []);

  const isInsideOutline = useCallback((target: EventTarget | null): boolean => {
    if (!(target instanceof Node)) return false;
    return outlineRef.current?.contains(target) ?? false;
  }, []);

  /* ── 导航 ──────────────────────────────────────────────────────────────────── */
  const navigateTo = useCallback(
    (sectionId: string) => {
      setCurrentSectionId(sectionId);
      onSectionChange?.(sectionId);
      findSectionEl(sectionId)?.scrollIntoView({
        block: 'start',
        behavior: reducedMotion ? 'auto' : 'smooth',
      });
    },
    [findSectionEl, onSectionChange, reducedMotion],
  );

  /* ── 焦点：切入全文 → 大纲首项（AC-51）────────────────────────────────────── */
  const didFocusOutlineRef = useRef(false);
  useEffect(() => {
    if (didFocusOutlineRef.current) return;
    const first = outlineItems()[0];
    if (!first) return;
    didFocusOutlineRef.current = true;
    first.focus(FOCUS_OPTIONS);
  }, [outlineItems, reportId]);

  /** 换报告：滚动归零，进度重新算（不能把上一份的进度留在新报告上）。 */
  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    scroller.scrollTop = 0;
    setProgress(0);
  }, [reportId]);

  /** 外部（深链 / URL）指定当前章时跟随；滚动高亮随后可以接管。 */
  useEffect(() => {
    if (!activeSectionId) return;
    setCurrentSectionId(activeSectionId);
    findSectionEl(activeSectionId)?.scrollIntoView({ block: 'start', behavior: 'auto' });
  }, [activeSectionId, findSectionEl]);

  /* ── 进度线 + 滚动高亮当前章 ──────────────────────────────────────────────── */
  const syncActiveSection = useCallback(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const nodes = Array.from(
      scroller.querySelectorAll<HTMLElement>('[data-reading-section]'),
    );
    const first = nodes[0];
    if (!first) return;
    const containerTop = scroller.getBoundingClientRect().top;
    let activeId = first.dataset.readingSection ?? '';
    for (const node of nodes) {
      if (node.getBoundingClientRect().top - containerTop <= ACTIVE_SECTION_OFFSET) {
        activeId = node.dataset.readingSection ?? activeId;
      }
    }
    // 同值返回 prev：滚动一次不必然触发一次重渲染。
    setCurrentSectionId((prev) => (prev === activeId ? prev : activeId));
  }, []);

  const handleScroll = useCallback(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const scrollable = scroller.scrollHeight - scroller.clientHeight;
    const ratio = scrollable > 0 ? scroller.scrollTop / scrollable : 0;
    const raw = Number.isFinite(ratio) ? Math.max(0, Math.min(1, ratio)) * 100 : 0;
    const step = 100 / REDUCED_MOTION_STEPS;
    setProgress(reducedMotion ? Math.round(raw / step) * step : raw);
    syncActiveSection();
  }, [reducedMotion, syncActiveSection]);

  /* ── 深链：滚到块 + 临时高亮 2000ms（AC-51）──────────────────────────────── */
  useEffect(() => {
    if (!highlightBlockId) return;
    const target = findBlockEl(highlightBlockId);
    if (!target) return;

    target.scrollIntoView({ block: 'center', behavior: reducedMotion ? 'auto' : 'smooth' });
    // 复用 v4 已有的样式钩子：`ReportRenderer` 的块容器带
    // `data-[highlighted]:bg-[var(--warn-weak)]`，所以这里只需把属性打开/关掉。
    target.setAttribute('data-highlighted', 'true');
    onJumpToBlockRef.current?.(highlightBlockId);

    const timer = setTimeout(() => {
      target.removeAttribute('data-highlighted');
      onHighlightClearRef.current?.();
    }, HIGHLIGHT_MS);

    return () => {
      clearTimeout(timer);
      target.removeAttribute('data-highlighted');
    };
    // 依赖只放稳定值：`doc` 的引用每次渲染都可能变，放进依赖会让 2 秒计时器永远重来。
  }, [highlightBlockId, reportId, sections.length, blocksInDoc, findBlockEl, reducedMotion]);

  /* ── 键盘（AC-65：只在容器内、且与 HOTKEYS 零重叠）────────────────────────── */
  const handleKeyDownCapture = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      if (event.key !== 'Escape') return;
      // 焦点不在大纲 → 这次 Esc 不属于我们，原样冒泡给全局 `closeOverlay`。
      if (!isInsideOutline(event.target)) return;
      // 单键单义：消费掉，别让全局 `closeOverlay` 再关一次浮层。
      event.stopPropagation();
      const back = returnFocusRef?.current;
      if (back?.isConnected) back.focus(FOCUS_OPTIONS);
    },
    [isInsideOutline, returnFocusRef],
  );

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      if (event.defaultPrevented) return;
      // 带修饰键的组合属于浏览器/全局快捷键，一律让行。
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      // 明确不处理 `j`/`k`（`HOTKEYS` 的切标的），也不处理 `?`/`Esc` 之外的全局键。
      if (!(READING_VIEW_KEYS as readonly string[]).includes(event.key)) return;
      if (event.key === 'Escape') return; // 捕获阶段已经处理
      // 焦点在正文里时，方向键属于"滚动"，不属于大纲。
      if (!isInsideOutline(event.target)) return;

      const items = outlineItems();
      if (items.length === 0) return;
      const active = typeof document === 'undefined' ? null : document.activeElement;
      const index = items.findIndex((item) => item === active);

      if (event.key === 'Enter') {
        const item = index >= 0 ? items[index] : undefined;
        const sectionId = item?.dataset.outlineItem ?? '';
        if (!sectionId) return;
        event.preventDefault();
        navigateTo(sectionId);
        return;
      }

      const last = items.length - 1;
      let next: number;
      switch (event.key) {
        case 'ArrowUp':
          next = index <= 0 ? 0 : index - 1;
          break;
        case 'ArrowDown':
          next = index < 0 ? 0 : Math.min(last, index + 1);
          break;
        case 'Home':
          next = 0;
          break;
        default:
          next = last;
          break;
      }
      event.preventDefault();
      items[next]?.focus(FOCUS_OPTIONS);
    },
    [isInsideOutline, navigateTo, outlineItems],
  );

  const progressLabel = fill(copy.reading.progress, { pct: Math.round(progress) });

  return (
    <section
      ref={rootRef}
      aria-label={copy.reading.full}
      data-reading-view={reportId}
      onKeyDown={handleKeyDown}
      onKeyDownCapture={handleKeyDownCapture}
      className={cn(
        'relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-[var(--bg-canvas)]',
        className,
      )}
    >
      {/* 顶部 2px 进度线：纯视觉，读屏器读 `progressbar` 的数值。 */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 h-[2px] bg-[var(--bg-surface-2)]">
        <div
          role="progressbar"
          aria-label={progressLabel}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
          aria-valuetext={progressLabel}
          className={cn(
            'h-[2px] bg-[var(--accent)]',
            reducedMotion ? undefined : 'transition-[width] duration-[var(--dur-fast)]',
          )}
          style={{ width: `${progress}%` }}
        />
      </div>

      <div ref={scrollRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[900px] items-start gap-4 px-4 pb-8 pt-4">
          <ReportOutline
            ref={outlineRef}
            sections={sections}
            activeId={currentSectionId}
            onNavigate={navigateTo}
            className="sticky top-2 hidden max-h-[calc(100vh-96px)] w-[176px] shrink-0 overflow-y-auto md:block"
          />

          <article className="min-w-0 flex-1 space-y-6">
            {sectionItems.map(({ section, items }) => (
              <section
                key={section.id}
                // 与分页视图同一个 id 约定（`sec-<id>`），这样 `?section=` 两种模式一致。
                id={`sec-${section.id}`}
                data-reading-section={section.id}
                className="scroll-mt-4"
              >
                <h2 className="mb-2 border-b border-[var(--border-subtle)] pb-1 text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
                  {section.title}
                </h2>
                {items.length === 0 ? (
                  <p className="text-[12px] text-[var(--text-tertiary)]">{copy.reading.emptySection}</p>
                ) : (
                  // 块渲染一行不改：分派表、引用角标、`data-block-id` 全是既有资产。
                  // 也**不**给块容器加 `tabIndex`（§19.3.4：读屏器不该把整块当控件）。
                  <ReportRenderer
                    blocks={items.map((item) => item.block)}
                    blockIds={items.map((item) => item.blockId)}
                    sources={doc.sources}
                    quality={quality}
                    onJumpToBlock={onJumpToBlock}
                  />
                )}
              </section>
            ))}
          </article>
        </div>
      </div>
    </section>
  );
}
