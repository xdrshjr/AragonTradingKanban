import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  ChartContractError,
  validateChartResponse,
} from '@/lib/chartDataset';
import { chartBarsQuery } from '@/lib/chartQuery';
import fixture from '../../contracts/chart-indicators.fixture.json';
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

const REQUEST = {
  instrumentId: 'CN:TEST', timeframe: '1d' as const,
  range: '1y' as const, adjust: 'qfq' as const,
};



describe('完整图表契约边界', () => {
  it('共同读取后端黄金fixture，不在浏览器重新计算指标', () => {
    expect(fixture.version).toBe('aragon-chart-v1');
    for (const sample of fixture.cases) {
      const response = chartResponse();
      response.bars = sample.rows;
      response.meta.barsCount = sample.rows.length;
      response.meta.fromTs = sample.rows[0]!.ts;
      response.meta.toTs = sample.rows.at(-1)!.ts;
      response.indicators = Object.fromEntries(INDICATOR_KEYS.map(
        key => [key, sample.rows.map(() => null)],
      ));
      response.chart!.stats = sample.rows.map(bar => ({
        ts: bar.ts,
        periodStart: new Date(bar.ts * 1000).toISOString().slice(0, 10),
        periodEnd: new Date(bar.ts * 1000).toISOString().slice(0, 10),
        leadingPartial: false, previousClose: null,
        change: null, changePct: null, amplitudePct: null,
      }));
      response.chart!.calculationBarsCount = sample.rows.length;
      response.chart!.warmupBarsCount = 0;
      for (const expected of sample.expected) {
        const key = expected.field as typeof INDICATOR_KEYS[number];
        response.indicators[key]![expected.index] = expected.value;
      }
      expect(validateChartResponse(response, REQUEST)).toBe(response);
    }
  });

  it('保留有效数据包和旧服务简化包', () => {
    const response = chartResponse();
    expect(validateChartResponse(response, REQUEST)).toBe(response);
    delete response.chart;
    response.indicators = {};
    expect(validateChartResponse(response, REQUEST)).toBe(response);
  });

  it.each([
    ['错误标的', (r: ReturnType<typeof chartResponse>) => {
      r.instrument.id = 'US:OTHER';
    }],
    ['错误周期', (r: ReturnType<typeof chartResponse>) => {
      r.meta.timeframe = '1w';
    }],
    ['未知版本', (r: ReturnType<typeof chartResponse>) => {
      Object.assign(r.chart!, { version: 'chart/v2' });
    }],
    ['错位数组', (r: ReturnType<typeof chartResponse>) => {
      r.indicators.ma5 = [];
    }],
    ['非有限指标', (r: ReturnType<typeof chartResponse>) => {
      r.indicators.ma5![0] = Number.NaN;
    }],
    ['重复时间戳', (r: ReturnType<typeof chartResponse>) => {
      r.bars[1]!.ts = r.bars[0]!.ts;
    }],
    ['错误端点', (r: ReturnType<typeof chartResponse>) => {
      r.meta.fromTs = 1;
    }],
    ['非法日期', (r: ReturnType<typeof chartResponse>) => {
      r.chart!.stats[0]!.periodEnd = '2026-02-30';
    }],
    ['重叠日期', (r: ReturnType<typeof chartResponse>) => {
      r.chart!.stats[1]!.periodStart = '2026-11-17';
    }],
    ['负成交量', (r: ReturnType<typeof chartResponse>) => {
      r.bars[0]!.v = -1;
    }],
    ['非法OHLC', (r: ReturnType<typeof chartResponse>) => {
      r.bars[0]!.l = 999;
    }],
    ['错误单位', (r: ReturnType<typeof chartResponse>) => {
      Object.assign(r.chart!, { volumeUnit: 'coin' });
    }],
    ['预热计数', (r: ReturnType<typeof chartResponse>) => {
      r.chart!.warmupBarsCount = 9;
    }],
    ['边界桶伪指标', (r: ReturnType<typeof chartResponse>) => {
      r.chart!.stats[0]!.leadingPartial = true;
    }],
  ])('拒绝%s', (_, change) => {
    const response = chartResponse();
    change(response);
    expect(() => validateChartResponse(response, REQUEST))
      .toThrow(ChartContractError);
  });

  it('旧包仍校验身份和OHLC，不能掩盖损坏数据', () => {
    const response = chartResponse();
    response.chart = null;
    response.bars[0]!.c = Number.POSITIVE_INFINITY;
    expect(() => validateChartResponse(response, REQUEST)).toThrow();
  });

  it('查询工厂共用key与禁用焦点/重连刷新', () => {
    const query = chartBarsQuery(REQUEST.instrumentId, REQUEST);
    expect(query.queryKey).toEqual([
      'bars', 'CN:TEST', '1d', '1y', 'qfq', 'chart-v1',
    ]);
    expect(query.refetchOnWindowFocus).toBe(false);
    expect(query.refetchOnReconnect).toBe(false);
    expect(query.staleTime).toBe(60_000);
  });

  it('只请求chart profile，坏刷新不替换成功缓存', async () => {
    const response = chartResponse();
    const call = vi.spyOn(api, 'bars').mockResolvedValue(response);
    const client = new QueryClient();
    const query = chartBarsQuery(REQUEST.instrumentId, REQUEST);
    try {
      await client.fetchQuery({ ...query, retry: false });
      expect(call).toHaveBeenCalledWith(
        'CN:TEST', '1d', '1y', 'qfq', { profile: 'chart' },
      );
      const invalid = chartResponse();
      invalid.chart!.stats[0]!.ts = 0;
      call.mockResolvedValue(invalid);
      await expect(client.fetchQuery({
        ...query, retry: false, staleTime: 0,
      })).rejects.toThrow(ChartContractError);
      expect(client.getQueryData(query.queryKey)).toBe(response);
    } finally {
      client.clear();
      call.mockRestore();
    }
  });
});
