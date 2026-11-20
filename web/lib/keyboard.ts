/**
 * Keyboard-first interaction (§11.1 / AC-16). Nothing here may fire while the
 * user is typing in a field, and nothing may swallow a browser shortcut.
 */

'use client';

import { useEffect } from 'react';

export type HotkeyId =
  | 'commandPalette'
  | 'nextSymbol'
  | 'prevSymbol'
  | 'generate'
  | 'regenerate'
  | 'cycleTheme'
  | 'toggleChart'
  | 'openSources'
  | 'closeOverlay'
  | 'toggleHelp';

export interface HotkeyDefinition {
  id: HotkeyId;
  keys: string[];
  label: string;
  group: 'navigation' | 'action' | 'view';
}

export const HOTKEYS: readonly HotkeyDefinition[] = [
  { id: 'commandPalette', keys: ['⌘K', 'Ctrl+K'], label: '命令面板', group: 'navigation' },
  { id: 'nextSymbol', keys: ['J'], label: '下一个标的', group: 'navigation' },
  { id: 'prevSymbol', keys: ['K'], label: '上一个标的', group: 'navigation' },
  { id: 'generate', keys: ['G'], label: '生成最新版本', group: 'action' },
  { id: 'regenerate', keys: ['R'], label: '重新生成', group: 'action' },
  { id: 'toggleChart', keys: ['\\'], label: '切换 K 线面板', group: 'view' },
  { id: 'cycleTheme', keys: ['T'], label: '切换明暗主题', group: 'view' },
  { id: 'openSources', keys: ['S'], label: '打开来源', group: 'view' },
  { id: 'closeOverlay', keys: ['Esc'], label: '关闭浮层', group: 'navigation' },
  { id: 'toggleHelp', keys: ['?'], label: '快捷键帮助', group: 'navigation' },
];

const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (EDITABLE_TAGS.has(target.tagName)) return true;
  return target.isContentEditable;
}

export function matchHotkey(event: KeyboardEvent): HotkeyId | null {
  const meta = event.metaKey || event.ctrlKey;
  const key = event.key;

  if (meta && key.toLowerCase() === 'k') return 'commandPalette';
  if (event.altKey) return null;

  switch (key) {
    case 'j':
    case 'J':
      return 'nextSymbol';
    case 'k':
    case 'K':
      return 'prevSymbol';
    case 'g':
    case 'G':
      return 'generate';
    case 'r':
    case 'R':
      return 'regenerate';
    case 't':
    case 'T':
      return 'cycleTheme';
    case 's':
    case 'S':
      return 'openSources';
    case '\\':
      return 'toggleChart';
    case '?':
      return 'toggleHelp';
    case 'Escape':
      return 'closeOverlay';
    default:
      return null;
  }
}

export interface HotkeyHandlers {
  onCommandPalette?: () => void;
  onNextSymbol?: () => void;
  onPrevSymbol?: () => void;
  onGenerate?: () => void;
  onRegenerate?: () => void;
  onCycleTheme?: () => void;
  onToggleChart?: () => void;
  onOpenSources?: () => void;
  onCloseOverlay?: () => void;
  onToggleHelp?: () => void;
  enabled?: boolean;
}

export function useHotkeys(handlers: HotkeyHandlers): void {
  useEffect(() => {
    if (handlers.enabled === false) return;

    const listener = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      const id = matchHotkey(event);
      if (!id) return;
      // Scope check: a letter key inside a text field is text, not a command.
      const modifierOnly = id === 'commandPalette' || id === 'closeOverlay';
      if (!modifierOnly && isEditableTarget(event.target)) return;

      const map: Record<HotkeyId, (() => void) | undefined> = {
        commandPalette: handlers.onCommandPalette,
        nextSymbol: handlers.onNextSymbol,
        prevSymbol: handlers.onPrevSymbol,
        generate: handlers.onGenerate,
        regenerate: handlers.onRegenerate ?? handlers.onGenerate,
        cycleTheme: handlers.onCycleTheme,
        toggleChart: handlers.onToggleChart,
        openSources: handlers.onOpenSources,
        closeOverlay: handlers.onCloseOverlay,
        toggleHelp: handlers.onToggleHelp,
      };
      const action = map[id];
      if (!action) return;
      event.preventDefault();
      action();
    };

    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [handlers]);
}

export function hotkeyLabel(id: HotkeyId): string {
  const definition = HOTKEYS.find((item) => item.id === id);
  return definition ? definition.keys[0]! : '';
}

/** `⌘` on Apple platforms, `Ctrl` elsewhere — for hint text only. */
export function isApplePlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /Mac|iPhone|iPad/.test(navigator.platform);
}

export function modifierLabel(): string {
  return isApplePlatform() ? '⌘' : 'Ctrl';
}
