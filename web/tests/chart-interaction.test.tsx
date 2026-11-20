import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChartInteraction } from '@/components/chart/ChartInteraction';
import type { ChartControllerApi } from '@/components/chart/ChartController';
import type { ChartDataset } from '@/lib/chartDataset';

function harness(tool: 'pan' | 'measure' | 'report-range' = 'pan') {
  const controller = {
    coordinateToIndex: () => 0, setGesturing: vi.fn(),
    setSelection: vi.fn(), releasePointer: vi.fn(),
    zoom: vi.fn(), fit: vi.fn(),
  } as unknown as ChartControllerApi;
  const onBrushRange = vi.fn();
  const onMeasurement = vi.fn();
  const onToolChange = vi.fn();
  const dataset = {
    bars: [{ ts: 1, o: 2, h: 4, l: 1, c: 3, v: 0 }],
    stats: [{ periodStart: '2026-11-01', periodEnd: '2026-11-01' }],
  } as unknown as ChartDataset;
  render(<ChartInteraction dataset={dataset} controller={controller}
    tool={tool} selection={{ index: null, origin: 'latest' }}
    rangeQueryEnabled={false} onToolChange={onToolChange}
    onMeasurement={onMeasurement} onBrushRange={onBrushRange}>
    <div>图形</div>
  </ChartInteraction>);
  return { controller, onBrushRange, onMeasurement, onToolChange };
}

describe('图表输入范围与退出层级', () => {
  it('只消费图表自身快捷键，保留Ctrl缩放与输入法', () => {
    const { controller } = harness();
    const region = screen.getByRole('region', { name: 'K 线图形' });
    fireEvent.keyDown(region, { key: '+', ctrlKey: true });
    fireEvent.keyDown(region, { key: '+', isComposing: true });
    expect(controller.zoom).not.toHaveBeenCalled();
    fireEvent.keyDown(region, { key: '+' });
    expect(controller.zoom).toHaveBeenCalledWith(.8);
    fireEvent.keyDown(region, { key: 'Home' });
    expect(controller.setSelection).toHaveBeenCalledWith(0, 'keyboard');
  });

  it('Escape先退出测量工具且不清除报告区间', () => {
    const { onToolChange, onBrushRange } = harness('measure');
    fireEvent.keyDown(screen.getByRole('region', { name: 'K 线图形' }),
      { key: 'Escape' });
    expect(onToolChange).toHaveBeenCalledWith('pan');
    expect(onBrushRange).not.toHaveBeenCalled();
  });

  it('失去捕获后下一次单指手势仍可启动', () => {
    class TestPointerEvent extends MouseEvent {
      pointerId: number;
      pointerType: string;
      constructor(type: string, options: PointerEventInit) {
        super(type, options);
        this.pointerId = options.pointerId ?? 1;
        this.pointerType = options.pointerType ?? 'mouse';
      }
    }
    vi.stubGlobal('PointerEvent', TestPointerEvent);
    const { controller } = harness('measure');
    const region = screen.getByRole('region', { name: 'K 线图形' });
    fireEvent.pointerDown(region, { button: 0, pointerId: 1, clientX: 0 });
    fireEvent.lostPointerCapture(region, { pointerId: 1 });
    fireEvent.pointerDown(region, { button: 0, pointerId: 2, clientX: 0 });
    expect(vi.mocked(controller.setGesturing).mock.calls
      .filter(([active]) => active)).toHaveLength(2);
    fireEvent.pointerUp(region, { pointerId: 2, clientX: 20 });
    vi.unstubAllGlobals();
  });
});
