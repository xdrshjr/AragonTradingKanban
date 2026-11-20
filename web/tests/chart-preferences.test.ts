import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_CHART_PREFERENCES,
  loadChartPreferences,
  saveChartPreferences,
} from '@/lib/chartPreferences';

beforeEach(() => localStorage.clear());

describe('图表偏好迁移', () => {
  it('默认开启MA5/20/60、成交量和MACD', () => {
    expect(loadChartPreferences()).toEqual(DEFAULT_CHART_PREFERENCES);
    expect(DEFAULT_CHART_PREFERENCES.main.ma60).toBe(true);
  });

  it('迁移旧指标且保留旧存储', () => {
    const old = { ma5: false, ma20: true, volume: false, boll: true };
    localStorage.setItem('aragon.indicators.v1', JSON.stringify(old));
    const next = loadChartPreferences();
    expect(next.main.ma5).toBe(false);
    expect(next.main.boll).toBe(true);
    expect(next.volume).toBe(false);
    expect(next.oscillator).toBe('macd');
    expect(localStorage.getItem('aragon.indicators.v1')).not.toBeNull();
    expect(JSON.parse(localStorage.getItem('aragon.chart.v2')!).version)
      .toBe(2);
  });

  it('v2优先，逐字段拒绝损坏值', () => {
    localStorage.setItem('aragon.indicators.v1', '{"ma5":false}');
    localStorage.setItem('aragon.chart.v2', JSON.stringify({
      version: 2, main: { ma5: true, ma10: 'yes' },
      oscillator: 'kdj', style: 'bogus', scale: 'log', volume: 0,
    }));
    const result = loadChartPreferences();
    expect(result.main.ma5).toBe(true);
    expect(result.main.ma10).toBe(false);
    expect(result.volume).toBe(true);
    expect(result.oscillator).toBe('kdj');
    expect(result.style).toBe('candlestick');
    expect(result.scale).toBe('log');
  });

  it('未知版本、坏JSON和禁止存储不会阻断图表', () => {
    localStorage.setItem('aragon.chart.v2', '{"version":7}');
    expect(loadChartPreferences()).toEqual(DEFAULT_CHART_PREFERENCES);
    localStorage.setItem('aragon.chart.v2', '{broken');
    expect(loadChartPreferences()).toEqual(DEFAULT_CHART_PREFERENCES);
    const getter = vi.spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => { throw new Error('denied'); });
    expect(loadChartPreferences()).toEqual(DEFAULT_CHART_PREFERENCES);
    getter.mockRestore();
    const setter = vi.spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => { throw new Error('quota'); });
    expect(() => saveChartPreferences(DEFAULT_CHART_PREFERENCES))
      .not.toThrow();
    setter.mockRestore();
  });

  it('读取默认值不抢先写入存储', () => {
    loadChartPreferences();
    expect(localStorage.getItem('aragon.chart.v2')).toBeNull();
  });

  it('未知v2版本采用默认，不倒退迁移旧v1', () => {
    localStorage.setItem('aragon.chart.v2', '{"version":99}');
    localStorage.setItem('aragon.indicators.v1', '{"ma5":false}');
    expect(loadChartPreferences()).toEqual(DEFAULT_CHART_PREFERENCES);
    expect(localStorage.getItem('aragon.chart.v2')).toBe('{"version":99}');
  });
});
