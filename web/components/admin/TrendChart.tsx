'use client';

import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { useEffect, useRef } from 'react';

import { copyAdmin } from '@/lib/copy.admin';

// 按需注册（不 import 整个 `echarts`）：只有后台概览这一个 async chunk 为它付费。
echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

export interface TrendPoint {
  day: string;
  count: number;
}

export interface TrendChartProps {
  signups: TrendPoint[];
  runs: TrendPoint[];
  height?: number;
}

function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function shortDay(day: string): string {
  // `2026-11-20` → `11-20`
  return day.length >= 10 ? day.slice(5, 10) : day;
}

/**
 * 近 14 日「注册 & 生成」双折线。
 *
 * * 颜色读自 `globals.css` 的 token（accent / info / 文字 / 网格），主题切换时重新取色；
 * * 两条线用**不同的点形与线型**（实线圆点 / 虚线方点）区分，不靠颜色单独传达信息；
 * * 图表本身是 `<canvas>`，读屏器看不到——调用方在旁边放了同数据的 `sr-only` 表格。
 */
export default function TrendChart({ signups, runs, height = 220 }: TrendChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = echarts.init(host, undefined, { renderer: 'canvas' });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const apply = () => {
      const accent = cssVar('--accent', '#3ecf8e');
      const info = cssVar('--info', '#4c9aff');
      const text = cssVar('--text-secondary', '#a0a5ad');
      const faint = cssVar('--text-tertiary', '#6c7178');
      const grid = cssVar('--chart-grid', 'rgba(255,255,255,0.05)');
      const surface = cssVar('--bg-surface-3', '#1e2025');
      const border = cssVar('--border-strong', 'rgba(255,255,255,0.12)');
      const days = signups.length >= runs.length ? signups.map((p) => p.day) : runs.map((p) => p.day);
      const byDay = (series: TrendPoint[]) => {
        const map = new Map(series.map((p) => [p.day, p.count]));
        return days.map((day) => map.get(day) ?? 0);
      };

      chart.setOption(
        {
          animationDuration: 280,
          grid: { left: 8, right: 12, top: 34, bottom: 4, containLabel: true },
          legend: {
            top: 0,
            left: 0,
            icon: 'roundRect',
            itemWidth: 12,
            itemHeight: 3,
            textStyle: { color: text, fontSize: 11 },
          },
          tooltip: {
            trigger: 'axis',
            backgroundColor: surface,
            borderColor: border,
            textStyle: { color: cssVar('--text-primary', '#edeef0'), fontSize: 12 },
          },
          xAxis: {
            type: 'category',
            data: days.map(shortDay),
            boundaryGap: false,
            axisLine: { lineStyle: { color: grid } },
            axisTick: { show: false },
            axisLabel: { color: faint, fontSize: 10.5 },
          },
          yAxis: {
            type: 'value',
            minInterval: 1,
            splitLine: { lineStyle: { color: grid } },
            axisLabel: { color: faint, fontSize: 10.5 },
          },
          series: [
            {
              name: copyAdmin.overview.chart.signups,
              type: 'line',
              data: byDay(signups),
              symbol: 'circle',
              symbolSize: 6,
              smooth: 0.2,
              lineStyle: { color: accent, width: 2 },
              itemStyle: { color: accent },
            },
            {
              name: copyAdmin.overview.chart.runs,
              type: 'line',
              data: byDay(runs),
              symbol: 'rect',
              symbolSize: 6,
              smooth: 0.2,
              lineStyle: { color: info, width: 2, type: 'dashed' },
              itemStyle: { color: info },
            },
          ],
        },
        true,
      );
    };

    apply();
    // 主题（`data-theme`）切换时重新取色。
    const mutation = new MutationObserver(apply);
    mutation.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => mutation.disconnect();
  }, [signups, runs]);

  return <div ref={hostRef} aria-hidden="true" data-testid="trend-chart" style={{ height }} className="w-full" />;
}
