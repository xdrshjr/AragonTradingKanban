import { beforeEach, describe, expect, it } from 'vitest';

import {
  CANVAS_MIN_H,
  CANVAS_MIN_W,
  DOCK_H,
  INSPECTOR_W,
  LEFT_MIN_PX,
  RIGHT_MIN_PX,
  SPLIT_DEFAULT,
  SPLIT_MAX,
  SPLIT_MIN,
  SPLIT_STORAGE_KEY,
  STRUCTURE_W,
  clampPct,
  computeInner,
  computeSplit,
  dragSplit,
  loadPanePrefs,
  loadSplit,
  saveSplit,
  toCssVariables,
} from '@/lib/workflow/layout';

describe('computeSplit — 70/30 外层（AC-W26）', () => {
  it('1280px：左栏 70%±1%，右栏 ≥ 320px', () => {
    const w = computeSplit(1280);
    expect(w.leftPct).toBeGreaterThanOrEqual(69);
    expect(w.leftPct).toBeLessThanOrEqual(71);
    expect(w.rightPx).toBeGreaterThanOrEqual(RIGHT_MIN_PX);
    expect(w.leftPx + w.rightPx).toBe(1280);
  });

  it('1100px：30% = 330 ≥ 320，不触发像素兜底', () => {
    const w = computeSplit(1100);
    expect(w.rightPx).toBe(330);
    expect(w.leftPx).toBe(770);
  });

  it('1920px：严格按百分比', () => {
    const w = computeSplit(1920);
    expect(w.rightPx).toBe(576);
    expect(w.leftPx).toBe(1344);
  });

  it('更窄时右栏固定 320px、左栏取余（像素兜底）', () => {
    const w = computeSplit(900);
    expect(w.rightPx).toBe(RIGHT_MIN_PX);
    expect(w.leftPx).toBe(900 - RIGHT_MIN_PX);
    expect(w.leftPx).toBeGreaterThanOrEqual(LEFT_MIN_PX);
  });

  it('百分比被夹在 [55, 80]，非法值回落到 70', () => {
    expect(clampPct(10)).toBe(SPLIT_MIN);
    expect(clampPct(99)).toBe(SPLIT_MAX);
    expect(clampPct(Number.NaN)).toBe(SPLIT_DEFAULT);
  });

  it('拖动换算：向右拖增大左栏并被夹取', () => {
    expect(dragSplit(1000, 70, 50)).toBeCloseTo(75, 5);
    expect(dragSplit(1000, 70, 10_000)).toBe(SPLIT_MAX);
    expect(dragSplit(1000, 70, -10_000)).toBe(SPLIT_MIN);
    expect(dragSplit(0, 70, 5)).toBe(70);
  });

  it('CSS 变量写回 --wf-left-w / --wf-right-w', () => {
    expect(toCssVariables(computeSplit(1280))).toEqual({ '--wf-left-w': '896px', '--wf-right-w': '384px' });
  });
});

describe('持久化（localStorage 损坏时回落）', () => {
  beforeEach(() => window.localStorage.clear());

  it('读不到 → 70；写入后可读回', () => {
    expect(loadSplit()).toBe(70);
    saveSplit(64);
    expect(loadSplit()).toBe(64);
  });

  it('损坏的值 → 70；越界的值被夹取', () => {
    window.localStorage.setItem(SPLIT_STORAGE_KEY, 'not-a-number');
    expect(loadSplit()).toBe(70);
    window.localStorage.setItem(SPLIT_STORAGE_KEY, '5');
    expect(loadSplit()).toBe(SPLIT_MIN);
  });

  it('面板偏好：损坏的 JSON 与非布尔值被忽略', () => {
    window.localStorage.setItem('aragon.workflow.panes.v1', '{oops');
    expect(loadPanePrefs()).toEqual({});
    window.localStorage.setItem('aragon.workflow.panes.v1', JSON.stringify({ structure: false, dock: 'yes' }));
    expect(loadPanePrefs()).toEqual({ structure: false });
  });
});

describe('computeInner — 左栏内层让位（RW-5）', () => {
  const H = 700;

  it('1280×800：左栏 896 → 三栏并排，画布 ≥ 400，底部工具窗默认收起', () => {
    const inner = computeInner({ leftPx: 896, heightPx: H });
    expect(inner.structure).toBe('docked');
    expect(inner.inspector).toBe('docked');
    expect(inner.dock).toBe('collapsed');
    expect(inner.canvasW).toBe(896 - STRUCTURE_W - INSPECTOR_W);
    expect(inner.canvasW).toBeGreaterThanOrEqual(CANVAS_MIN_W);
  });

  it('1100px：左栏 770 → 结构树收为图标条，检查器仍并排', () => {
    const inner = computeInner({ leftPx: 770, heightPx: H });
    expect(inner.structure).toBe('icon');
    expect(inner.inspector).toBe('docked');
    expect(inner.canvasW).toBeGreaterThanOrEqual(CANVAS_MIN_W);
  });

  it('左栏 < 688 → 检查器变浮层，画布独占宽度', () => {
    const inner = computeInner({ leftPx: 650, heightPx: H });
    expect(inner.inspector).toBe('overlay');
    expect(inner.canvasW).toBe(650 - 28);
  });

  it('偏好不得突破 CANVAS_MIN_W：想展开也照样让位', () => {
    const wanted = { structure: true, inspector: true };
    expect(computeInner({ leftPx: 700, heightPx: H, prefs: wanted }).structure).toBe('icon');
    for (const leftPx of [560, 600, 700, 770, 888, 1000]) {
      const result = computeInner({ leftPx, heightPx: H, prefs: wanted });
      if (result.inspector === 'docked') expect(result.canvasW).toBeGreaterThanOrEqual(CANVAS_MIN_W);
    }
  });

  it('用户收起 → 即使空间够也收起', () => {
    const inner = computeInner({ leftPx: 1000, heightPx: H, prefs: { structure: false, inspector: false } });
    expect(inner.structure).toBe('icon');
    expect(inner.inspector).toBe('overlay');
  });

  it('底部工具窗：用户展开或有错误时展开；高度不够降为浮层', () => {
    expect(computeInner({ leftPx: 900, heightPx: H, prefs: { dock: true } }).dock).toBe('docked');
    expect(computeInner({ leftPx: 900, heightPx: H, hasErrors: true }).dock).toBe('docked');
    expect(computeInner({ leftPx: 900, heightPx: H, hasErrors: true, prefs: { dock: false } }).dock).toBe('collapsed');
    expect(computeInner({ leftPx: 900, heightPx: DOCK_H + CANVAS_MIN_H - 1, prefs: { dock: true } }).dock).toBe(
      'overlay',
    );
  });
});
