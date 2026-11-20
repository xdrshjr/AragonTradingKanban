import { describe, expect, it, vi } from 'vitest';
import * as download from '@/lib/download';
import {
  chartExportFilename,
  composeChartPng,
  downloadChartPng,
  encodeChartCsv,
} from '@/lib/chartExport';
import { buildChartDataset } from '@/lib/chartDataset';
import {
  formatAmount, formatPercent, formatPrice,
  formatVolume, pricePrecision,
} from '@/lib/chartFormat';
import type { BarsResponse } from '@/lib/types';
import { INDICATOR_KEYS } from '@/lib/chartDataset';

function chartResponse(): BarsResponse {
  const bars = [100, 105, 103].map((close, index) => ({
    ts: 1794873600 + index * 86400,
    o: close, h: close + 5, l: close - 5, c: close,
    v: index === 0 ? 0 : 100, amount: index === 2 ? null : 1000,
  }));
  return {
    instrument: {
      id: 'CN:TEST', symbol: 'TEST', currency: 'CNY',
    } as BarsResponse['instrument'],
    bars,
    indicators: Object.fromEntries(INDICATOR_KEYS.map(key => [
      key, bars.map(() => null),
    ])),
    meta: {
      source: 'akshare', barsCount: 3,
      fromTs: bars[0]!.ts, toTs: bars[2]!.ts,
      timeframe: '1d', adjust: 'qfq', cached: false,
      stale: false, warnings: [],
    },
    chart: {
      version: 'chart/v1', calculationBarsCount: 5,
      warmupBarsCount: 2, historyLimited: true,
      volumeUnit: 'lot', volumeUnitSize: 100, amountCurrency: 'CNY',
      effectiveAdjust: 'qfq', returnBasis: 'adjacent_close',
      indicatorConvention: 'aragon-chart-v1',
      stats: bars.map((bar, index) => ({
        ts: bar.ts, periodStart: `2026-11-${17 + index}`,
        periodEnd: `2026-11-${17 + index}`, leadingPartial: false,
        previousClose: index ? bars[index - 1]!.c : 99,
        change: index ? bar.c - bars[index - 1]!.c : 1,
        changePct: index === 1 ? 5 : null, amplitudePct: 10,
      })),
    },
  };
}



describe('图表CSV及数值精度', () => {
  it('导出全指标、元数据、原精度、空值及注入转义', () => {
    const response = chartResponse();
    response.instrument.symbol = '=HYPERLINK("bad")';
    response.bars[0]!.c = 0.0005123456;
    const csv = encodeChartCsv(buildChartDataset(response, 1));
    expect(csv.startsWith('\uFEFF')).toBe(true);
    expect(csv).toContain('warmupBarsCount');
    expect(csv).toContain('macdHistogram');
    expect(csv).toContain('volumeUnitSize');
    expect(csv).toContain('0.0005123456');
    expect(csv).toContain("'=HYPERLINK");
    expect(csv).toContain('\r\n');
    expect(chartExportFilename('../TEST\n', response.meta, 'csv'))
      .not.toMatch(/[\r\n/\\]/);
  });

  it('微价、负价、零量、空成交额格式一致', () => {
    expect(pricePrecision([null, -0.0005, 0])).toBe(6);
    expect(formatPrice(-0.0005)).toBe('-0.000500');
    expect(formatVolume(0, 'lot')).toBe('0手');
    expect(formatAmount(null, 'CNY')).toBe('—');
    expect(formatPercent(0)).toBe('0.00%');
  });
});

describe('PNG像素与失败出口', () => {
  it('按CSS尺寸和统一DPR拼合，不混用源bitmap像素', () => {
    const context = {
      scale: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(),
      drawImage: vi.fn(), font: '', fillStyle: '',
    };
    const getter = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(context as unknown as CanvasRenderingContext2D);
    const source = document.createElement('canvas');
    source.width = 1200;
    source.height = 600;
    const result = composeChartPng({
      title: 'TEST', subtitle: '日线', source: 'synthetic',
      panes: [{ canvas: source, width: 600, height: 300, label: '价格' }],
      pixelRatio: 3,
    });
    expect(result.width).toBe(1200);
    expect(result.height).toBeGreaterThan(600);
    expect(context.scale).toHaveBeenCalledWith(2, 2);
    expect(context.drawImage.mock.calls[0]!.slice(-2)).toEqual([600, 300]);
    getter.mockRestore();
  });

  it('布局等待后版本改变取消任务且不捕获canvas', async () => {
    const capture = vi.fn();
    const frame = vi.spyOn(globalThis, 'requestAnimationFrame')
      .mockImplementation(callback => { callback(0); return 1; });
    await expect(downloadChartPng({
      capture, filename: 'test.png', isCurrent: () => false,
    })).rejects.toThrow(/重试/);
    expect(capture).not.toHaveBeenCalled();
    frame.mockRestore();
  });

  it('尊重浏览器缩放后低于1的有效DPR', () => {
    const context = {
      scale: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(),
      drawImage: vi.fn(), font: '', fillStyle: '',
    };
    const getter = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(context as unknown as CanvasRenderingContext2D);
    try {
      const result = composeChartPng({
        title: 'TEST', subtitle: '', source: 'synthetic',
        panes: [{
          canvas: document.createElement('canvas'),
          width: 600, height: 300, label: '价格',
        }],
        pixelRatio: 0.75,
      });
      expect(result.width).toBe(450);
      expect(context.scale).toHaveBeenCalledWith(0.75, 0.75);
    } finally {
      getter.mockRestore();
    }
  });

  it('画布不可用或编码无Blob时明确失败', async () => {
    const frame = vi.spyOn(globalThis, 'requestAnimationFrame')
      .mockImplementation(callback => { callback(0); return 1; });
    const context = {
      scale: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(),
      drawImage: vi.fn(), font: '', fillStyle: '',
    };
    const getter = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(null);
    const options = {
      title: 'TEST', subtitle: '', source: 'synthetic',
      panes: [{
        canvas: document.createElement('canvas'),
        width: 600, height: 300, label: '价格',
      }],
    };
    try {
      expect(() => composeChartPng(options)).toThrow(/画布不可用/);
      getter.mockReturnValue(context as unknown as CanvasRenderingContext2D);
      const encoder = vi.spyOn(HTMLCanvasElement.prototype, 'toBlob')
        .mockImplementation(callback => callback(null));
      await expect(downloadChartPng({
        capture: () => options, filename: 'test.png', isCurrent: () => true,
      })).rejects.toThrow(/编码失败/);
      encoder.mockRestore();
    } finally {
      getter.mockRestore();
      frame.mockRestore();
    }
  });

  it('下载异常仍释放创建的Blob URL', async () => {
    const frame = vi.spyOn(globalThis, 'requestAnimationFrame')
      .mockImplementation(callback => { callback(0); return 1; });
    const context = {
      scale: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(),
      drawImage: vi.fn(), font: '', fillStyle: '',
    };
    const getter = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(context as unknown as CanvasRenderingContext2D);
    const encoder = vi.spyOn(HTMLCanvasElement.prototype, 'toBlob')
      .mockImplementation(callback => callback(new Blob(['png'])));
    const trigger = vi.spyOn(download, 'triggerDownload')
      .mockImplementation(() => { throw new Error('下载失败'); });
    const create = vi.fn().mockReturnValue('blob:chart');
    const revoke = vi.fn();
    Object.defineProperties(URL, {
      createObjectURL: { value: create, configurable: true },
      revokeObjectURL: { value: revoke, configurable: true },
    });
    try {
      await expect(downloadChartPng({
        capture: () => ({
          title: 'TEST', subtitle: '', source: 'synthetic',
          panes: [{
            canvas: document.createElement('canvas'),
            width: 600, height: 300, label: '价格',
          }],
        }),
        filename: 'test.png', isCurrent: () => true,
      })).rejects.toThrow('下载失败');
      expect(revoke).toHaveBeenCalledWith('blob:chart');
    } finally {
      getter.mockRestore();
      encoder.mockRestore();
      trigger.mockRestore();
      frame.mockRestore();
    }
  });
});
