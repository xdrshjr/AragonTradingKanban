'use client';

import { useEffect, useMemo, useRef } from 'react';
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';

import { epochToDate } from '@/lib/format';
import { useUiStore } from '@/lib/store';
import type { Bar, Indicators } from '@/lib/types';

/** Below this the axis labels and volume bars stop being legible. */
const MIN_CHART_HEIGHT = 220;

export interface CandleChartProps {
  bars: Bar[];
  indicators?: Partial<Indicators>;
  timezone?: string;
  height?: number;
  showVolume?: boolean;
  showBoll?: boolean;
  showMa?: { ma5: boolean; ma10: boolean; ma20: boolean; ma60: boolean };
}

const MA_KEYS = ['ma5', 'ma10', 'ma20', 'ma60'] as const;
const MA_COLORS: Record<(typeof MA_KEYS)[number], string> = {
  ma5: '#f5a623',
  ma10: '#4c9aff',
  ma20: '#a78bfa',
  ma60: '#22d3ee',
};

/**
 * TradingView-lightweight-charts wrapper (spec §6.3).
 *
 * Lifecycle is explicit because this is the one component with real native state:
 * created once per mount, resized by `ResizeObserver`, removed on unmount. Data
 * updates use `setData` (bulk) rather than per-point updates, which is what keeps
 * range switching under the 120ms budget (§11.2).
 */
export function CandleChart({
  bars,
  indicators,
  timezone = 'Asia/Shanghai',
  height = 360,
  showVolume = true,
  showBoll = false,
  showMa = { ma5: true, ma10: false, ma20: true, ma60: false },
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const bollRefs = useRef<{ upper: ISeriesApi<'Line'> | null; lower: ISeriesApi<'Line'> | null }>({
    upper: null,
    lower: null,
  });
  const maRefs = useRef<Record<string, ISeriesApi<'Line'> | null>>({});
  const chartFocus = useUiStore((state) => state.chartFocus);

  const readStyle = () => {
    if (typeof window === 'undefined') {
      return { text: '#a0a5ad', grid: 'rgba(255,255,255,.05)', up: '#f2564d', down: '#2fbf71', border: 'rgba(255,255,255,.12)' };
    }
    const style = getComputedStyle(document.documentElement);
    return {
      text: style.getPropertyValue('--text-tertiary').trim() || '#6c7178',
      grid: style.getPropertyValue('--chart-grid').trim() || 'rgba(255,255,255,.05)',
      up: style.getPropertyValue('--chart-up').trim() || '#f2564d',
      down: style.getPropertyValue('--chart-down').trim() || '#2fbf71',
      border: style.getPropertyValue('--border-strong').trim() || 'rgba(255,255,255,.12)',
    };
  };

  // Create once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const style = readStyle();
    const chart = createChart(container, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: style.text,
        fontSize: 11,
        fontFamily: getComputedStyle(document.body).fontFamily,
      },
      grid: {
        vertLines: { color: style.grid, style: LineStyle.Solid },
        horzLines: { color: style.grid, style: LineStyle.Solid },
      },
      rightPriceScale: { borderColor: style.border, scaleMargins: { top: 0.08, bottom: 0.26 } },
      timeScale: { borderColor: style.border, rightOffset: 3, barSpacing: 6, timeVisible: false },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: style.text, width: 1, style: LineStyle.Dashed, labelBackgroundColor: style.border },
        horzLine: { color: style.text, width: 1, style: LineStyle.Dashed, labelBackgroundColor: style.border },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      localization: {
        locale: 'zh-CN',
        dateFormat: 'yyyy-MM-dd',
      },
    });

    const candles = chart.addCandlestickSeries({
      upColor: style.up,
      downColor: style.down,
      wickUpColor: style.up,
      wickDownColor: style.down,
      borderVisible: false,
      priceLineVisible: false,
    });

    // Volume is always created and merely hidden when toggled off: creating it
    // inside the mount effect (and reading `showVolume` there) meant the toggle
    // did nothing after the first render.
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      priceLineVisible: false,
      lastValueVisible: false,
      visible: showVolume,
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      borderVisible: false,
    });

    const maSeries: Record<string, ISeriesApi<'Line'> | null> = {};
    for (const key of MA_KEYS) {
      maSeries[key] = chart.addLineSeries({
        color: MA_COLORS[key],
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
    }

    // BOLL bands (§6.3): two dashed lines, hidden until the toggle is on.
    const bollSeries = {
      upper: chart.addLineSeries({
        color: '#f472b6',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: showBoll,
      }),
      lower: chart.addLineSeries({
        color: '#f472b6',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        visible: showBoll,
      }),
    };

    chartRef.current = chart;
    candleRef.current = candles;
    volumeRef.current = volume;
    bollRefs.current = bollSeries;
    maRefs.current = maSeries;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const width = Math.floor(entry.contentRect.width);
      // Follow the container in both axes: a fixed height left dead space in the
      // panel on tall viewports.
      const measured = Math.floor(entry.contentRect.height);
      if (width > 0 && measured > MIN_CHART_HEIGHT) {
        chart.applyOptions({ width, height: measured });
      } else if (width > 0) {
        chart.applyOptions({ width });
      }
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      bollRefs.current = { upper: null, lower: null };
      maRefs.current = {};
    };
    // `height`/`showVolume`/`showBoll` intentionally excluded: they are applied
    // by the effect below, which is the only thing that reacts to a toggle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions({ height });
  }, [height]);

  // Volume visibility follows the toggle without recreating the series.
  useEffect(() => {
    volumeRef.current?.applyOptions({ visible: showVolume });
    chartRef.current?.priceScale('volume').applyOptions({ visible: showVolume });
  }, [showVolume]);

  // Bulk data updates.
  useEffect(() => {
    const candles = candleRef.current;
    if (!candles) return;
    const series = bars.map((bar) => ({
      time: bar.ts as UTCTimestamp,
      open: bar.o,
      high: bar.h,
      low: bar.l,
      close: bar.c,
    }));
    candles.setData(series);

    if (volumeRef.current) {
      volumeRef.current.setData(
        bars.map((bar) => ({
          time: bar.ts as UTCTimestamp,
          value: bar.v,
          color: bar.c >= bar.o ? 'rgba(242,86,77,.45)' : 'rgba(47,191,113,.45)',
        })),
      );
    }
  }, [bars]);

  useEffect(() => {
    for (const key of MA_KEYS) {
      const series = maRefs.current[key];
      if (!series) continue;
      const enabled = showMa[key];
      const values = indicators?.[key];
      series.applyOptions({ visible: enabled });
      if (!enabled || !values) {
        series.setData([]);
        continue;
      }
      const points = values
        .map((value, index) => {
          const bar = bars[index];
          if (value === null || !bar) return null;
          return { time: bar.ts as UTCTimestamp, value };
        })
        .filter((point): point is { time: UTCTimestamp; value: number } => point !== null);
      series.setData(points);
    }
  }, [bars, indicators, showMa]);

  // BOLL upper/lower: same contract as the MA lines (§6.3).
  useEffect(() => {
    const upper = bollRefs.current.upper;
    const lower = bollRefs.current.lower;
    if (!upper || !lower) return;
    upper.applyOptions({ visible: showBoll });
    lower.applyOptions({ visible: showBoll });
    for (const [series, values] of [
      [upper, indicators?.bollUpper],
      [lower, indicators?.bollLower],
    ] as const) {
      if (!showBoll || !values) {
        series.setData([]);
        continue;
      }
      series.setData(
        values
          .map((value, index) => {
            const bar = bars[index];
            if (value === null || !bar) return null;
            return { time: bar.ts as UTCTimestamp, value };
          })
          .filter((point): point is { time: UTCTimestamp; value: number } => point !== null),
      );
    }
  }, [bars, indicators, showBoll]);

  // `chart:focus` from a report date click (§3.3).
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !chartFocus || bars.length === 0) return;
    const fromTs = Math.floor(new Date(`${chartFocus.from}T00:00:00Z`).getTime() / 1000);
    const toTs = Math.floor(new Date(`${chartFocus.to}T23:59:59Z`).getTime() / 1000);
    const first = bars.find((bar) => bar.ts >= fromTs);
    const last = [...bars].reverse().find((bar) => bar.ts <= toTs);
    if (first && last) {
      chart.timeScale().setVisibleRange({
        from: first.ts as UTCTimestamp,
        to: last.ts as UTCTimestamp,
      });
    }
  }, [chartFocus, bars]);

  const summary = useMemo(() => bars.slice(-5), [bars]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={containerRef} className="min-h-[220px] w-full flex-1" data-testid="candle-chart" />

      {/* Chart has a text alternative (§11.1): last five bars as a table. */}
      <details className="border-t border-[var(--border-subtle)] px-2 py-1 text-[10.5px] text-[var(--text-tertiary)]">
        <summary className="cursor-pointer select-none">最近 5 根 K 线（文本替代）</summary>
        <table className="mt-1 w-full text-[10.5px]">
          <thead>
            <tr className="text-[var(--text-tertiary)]">
              <th className="py-0.5 text-left font-normal">日期</th>
              <th className="py-0.5 text-right font-normal">开</th>
              <th className="py-0.5 text-right font-normal">高</th>
              <th className="py-0.5 text-right font-normal">低</th>
              <th className="py-0.5 text-right font-normal">收</th>
            </tr>
          </thead>
          <tbody>
            {summary.map((bar) => (
              <tr key={bar.ts}>
                <td className="py-0.5 text-left">{epochToDate(bar.ts, timezone)}</td>
                <td className="num py-0.5 text-right">{bar.o.toFixed(2)}</td>
                <td className="num py-0.5 text-right">{bar.h.toFixed(2)}</td>
                <td className="num py-0.5 text-right">{bar.l.toFixed(2)}</td>
                <td className="num py-0.5 text-right">{bar.c.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
