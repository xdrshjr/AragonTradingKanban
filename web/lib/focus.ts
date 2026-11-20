/**
 * 焦点归还（AC-36 / §18.3.6 v4 修订 V4-P2-3）。
 *
 * 为什么需要它：`components/ui/Dialog.tsx` 用的 Radix Dialog **自带**焦点管理，
 * Dialog 形态的面板不需要本模块；而 `AskPanel` 抽屉、`CompareView` 路由态这类
 * **非 Dialog** 面板没有现成机制，`web/lib/store.ts` 里也**没有** `lastFocused`
 * （v3 曾误以为有）。所以这里给一个最小的、按面板实例作用域的 hook，
 * 而不是往 store 里塞一个全局"最后焦点"（那是双源）。
 *
 * 契约：
 * - 打开期间按 `Esc` → 先 `onClose()`，再把焦点还给触发元素；
 * - `isOpen` 从 true 翻成 false（不论怎么关的）→ 同样归还焦点；
 * - 挂载时 `isOpen === false` 什么都不做（绝不抢焦点）；
 * - 单个 keydown 监听，effect 依赖变化时先卸载再挂载，**不会叠加**；
 * - SSR 安全：模块作用域内不碰 `document` / `window`。
 */

'use client';

import { useEffect, useRef } from 'react';

/** 把焦点还给元素；元素已卸载或不可聚焦时安静跳过（不抛、不降级到 body）。 */
export function restoreFocus(target: HTMLElement | null | undefined): void {
  if (!target || !target.isConnected || target.matches(':disabled')) return;
  if (target.getClientRects().length === 0) return;
  try {
    target.focus({ preventScroll: true });
  } catch {
    /* 元素是 disabled / 已被移除：保持沉默，绝不把焦点丢给 body */
  }
}

/**
 * @param triggerRef 触发面板的那个元素（通常是按钮）。可以是 `useRef(null)`。
 * @param isOpen     面板是否打开。
 * @param onClose    `Esc` 时调用的关闭回调；用 ref 保存最新值，避免因内联箭头函数
 *                   每次渲染都重建监听（那才是"监听器堆叠"的根源）。
 */
export function useReturnFocus(
  triggerRef: React.RefObject<HTMLElement | null>,
  isOpen: boolean,
  onClose?: () => void,
  options: { handleEscape?: boolean; returnFocus?: boolean } = {},
): void {
  const triggerRefRef = useRef(triggerRef);
  const onCloseRef = useRef(onClose);
  /** 上一轮是否处于打开态 —— 用来区分"刷新"与"真正的关闭"。 */
  const wasOpenRef = useRef(false);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  // 永远指向最新值：这两个 effect 不注册监听，只同步 ref。
  useEffect(() => {
    triggerRefRef.current = triggerRef;
  }, [triggerRef]);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    if (!isOpen) {
      // 只在"打开过"之后归还焦点；初次挂载 (false → false) 不动焦点。
      if (wasOpenRef.current && optionsRef.current.returnFocus !== false) {
        if (optionsRef.current.handleEscape === false &&
          document.activeElement?.closest('[data-assistant-no-focus-return]')) {
          wasOpenRef.current = false;
          return;
        }
        if (optionsRef.current.handleEscape === false) {
          requestAnimationFrame(() => {
            if (optionsRef.current.returnFocus !== false) {
              restoreFocus(triggerRefRef.current.current);
            }
          });
        } else restoreFocus(triggerRefRef.current.current);
      }
      wasOpenRef.current = false;
      return;
    }

    wasOpenRef.current = true;
    const listener = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' && event.key !== 'Esc') return;
      // A layered surface on top (source drawer, dialog) may have handled this key
      // already. Closing both on one Esc is the classic double-close bug.
      if (event.defaultPrevented) return;
      if (event.isComposing || optionsRef.current.handleEscape === false) return;
      event.preventDefault();
      wasOpenRef.current = false;
      onCloseRef.current?.();
      if (optionsRef.current.returnFocus !== false) {
        restoreFocus(triggerRefRef.current.current);
      }
    };

    window.addEventListener('keydown', listener);
    // 只有一个监听，且卸载时精确移除同一个函数引用。
    return () => window.removeEventListener('keydown', listener);
  }, [isOpen]);
}
