import { describe, expect, it } from 'vitest';
import {
  buildChartDataset,
  calculateMeasurement,
  getLatestChartChange,
  findDateIndex,
  INDICATOR_KEYS,
  validateChartResponse,
} from '@/lib/chartDataset';
import type { BarsResponse } from '@/lib/types';

export function chartResponse(): BarsResponse {
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

export const REQUEST = {
  instrumentId: 'CN:TEST', timeframe: '1d' as const,
  range: '1y' as const, adjust: 'qfq' as const,
};

describe('索引、日期与区间测量', () => {
  it('建立O(1)时间索引和日期二分索引', () => {
    const response = validateChartResponse(chartResponse(), REQUEST);
    const dataset = buildChartDataset(response, 7);
    expect(dataset.datasetVersion).toBe(7);
    expect(dataset.indexByTimestamp.get(dataset.bars[1]!.ts)).toBe(1);
    expect(findDateIndex(dataset, '2026-11-18')).toBe(1);
    expect(findDateIndex(dataset, '2026-01-01')).toBe(0);
    expect(findDateIndex(dataset, '2027-01-01')).toBe(2);
  });

  it('逆向测量包含端点，缺少任一成交额即为null', () => {
    const dataset = buildChartDataset(chartResponse(), 1);
    const measurement = calculateMeasurement(dataset, 2, 0);
    expect(measurement).toMatchObject({
      startIndex: 0, endIndex: 2, count: 3,
      high: 110, low: 95, volume: 200, amount: null,
    });
    expect(measurement.returnPct).toBeCloseTo(3);
    expect(calculateMeasurement(dataset, 1, 1).returnPct).toBe(0);
  });

  it('零或负起点不能伪造收益；非法索引明确失败', () => {
    const response = chartResponse();
    response.bars[0]!.c = 0;
    const dataset = buildChartDataset(response, 1);
    expect(calculateMeasurement(dataset, 0, 0).returnPct).toBeNull();
    expect(calculateMeasurement(dataset, 0, 1).returnPct).toBeNull();
    expect(() => calculateMeasurement(dataset, -1, 1)).toThrow();
  });

  it('日期定位包含周月区间内日期', () => {
    const response = chartResponse();
    response.chart!.stats[0]!.periodStart = '2026-11-12';
    const dataset = buildChartDataset(response, 1);
    expect(findDateIndex(dataset, '2026-11-15')).toBe(0);
  });

  it('简化服务只在有效前收时提供旧涨幅，新包直接读后端统计', () => {
    const response = chartResponse();
    response.chart!.stats.at(-1)!.changePct = 7;
    expect(getLatestChartChange(response)).toBe(7);
    response.chart = null;
    expect(getLatestChartChange(response)).toBeCloseTo((103 / 105 - 1) * 100);
    response.bars[1]!.c = 0;
    expect(getLatestChartChange(response)).toBeNull();
  });
});
