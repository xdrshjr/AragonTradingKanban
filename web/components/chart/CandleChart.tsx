'use client';

import {
  forwardRef, useEffect, useImperativeHandle, useLayoutEffect,
  useRef, useState,
} from 'react';
import { createChart, ColorType, type UTCTimestamp } from 'lightweight-charts';

import type {
  ChartDataset, ChartSelection, ChartTool, Measurement,
} from '@/lib/chartDataset';
import type { ChartPreferences } from '@/lib/chartPreferences';
import { formatVolume } from '@/lib/chartFormat';
import { useUiStore } from '@/lib/store';
import type { Bar, Indicators } from '@/lib/types';
import {
  createController, type ChartControllerApi, type ChartRuntime,
} from './ChartController';
import { ChartInteraction } from './ChartInteraction';
import { ChartPane } from './ChartPane';
import {
  formatIndicatorValue, getIndicatorLabel, getVisibleIndicatorKeys,
} from './ChartReadout';

export interface CandleChartHandle {
  controller: ChartControllerApi | null;
  consumeEscape: (event: KeyboardEvent) => boolean;
  getRuntime: () => ChartRuntime | null;
  capturePanes: () => ReturnType<ChartControllerApi['capturePanes']>;
}
export interface CandleChartProps {
  bars: Bar[];
  indicators?: Partial<Indicators>;
  dataset?: ChartDataset | null;
  preferences: ChartPreferences;
  selection: ChartSelection;
  tool: ChartTool;
  rangeQueryEnabled?: boolean;
  onSelection: (selection: ChartSelection) => void;
  onToolChange: (tool: ChartTool) => void;
  onMeasurement: (measurement: Measurement) => void;
  onBrushRange?: (range: { from: string; to: string } | null) => void;
  onMessage: (message: string) => void;
  runtimeRef: { current: ChartRuntime | null };
  onReady?: () => void;
}

/** Dynamic boundary: native chart code never enters the initial page chunk. */
export const CandleChart = forwardRef<CandleChartHandle, CandleChartProps>(
  function CandleChart(props, ref) {
    if (!props.dataset) return <LegacyChart bars={props.bars}
      indicators={props.indicators} />;
    return <ChartGroup {...props} dataset={props.dataset} ref={ref} />;
  },
);

interface GroupProps extends CandleChartProps { dataset: ChartDataset }

function useNativeController(props: GroupProps) {
  const main = useRef<HTMLDivElement>(null);
  const volume = useRef<HTMLDivElement>(null);
  const oscillator = useRef<HTMLDivElement>(null);
  const native = useRef<ChartControllerApi | null>(null);
  const callbacks = useRef(props);
  callbacks.current = props;
  const escape = useRef<((event: KeyboardEvent) => boolean) | null>(null);
  const [controller, setController] = useState<ChartControllerApi | null>(null);
  const theme = useUiStore(state => state.resolvedTheme);

  useLayoutEffect(() => {
    const instance = createController({
      containers: { main: main.current!, volume: volume.current!,
        oscillator: oscillator.current! },
      initialSettings: callbacks.current.preferences,
      onSelection: selection => callbacks.current.onSelection(selection),
      onMessage: message => callbacks.current.onMessage(message),
    });
    native.current = instance;
    instance.setDataset(callbacks.current.dataset);
    const runtime = callbacks.current.runtimeRef.current;
    if (runtime) instance.restoreRuntime(runtime);
    setController(instance);
    return () => {
      callbacks.current.runtimeRef.current = instance.snapshotRuntime();
      native.current = null; instance.destroy();
    };
  }, []);

  useEffect(() => { controller?.setDataset(props.dataset); },
    [controller, props.dataset]);
  useEffect(() => { controller?.applySettings(props.preferences); },
    [controller, props.preferences]);
  useEffect(() => { controller?.setTool(props.tool); },
    [controller, props.tool]);
  useEffect(() => { controller?.refreshTheme(); }, [controller, theme]);
  useEffect(() => {
    if (controller) callbacks.current.onReady?.();
  }, [controller]);
  return { main, volume, oscillator, native, escape, controller };
}
type NativeState = ReturnType<typeof useNativeController>;

const ChartGroup = forwardRef<CandleChartHandle, GroupProps>(
  function ChartGroup(props, ref) {
  const state = useNativeController(props);
  const { native, escape, controller } = state;

  useImperativeHandle(ref, () => ({
    controller,
    consumeEscape: event => escape.current?.(event) ?? false,
    getRuntime: () => native.current?.snapshotRuntime() ?? null,
    capturePanes: () => {
      if (!native.current) throw new Error('图表尚未就绪，请重试');
      return native.current.capturePanes();
    },
  }), [controller, escape, native]);
  return <ChartInteraction dataset={props.dataset} controller={controller}
    selection={props.selection} tool={props.tool}
    rangeQueryEnabled={props.rangeQueryEnabled ?? false}
    onToolChange={props.onToolChange} onMeasurement={props.onMeasurement}
    onBrushRange={props.onBrushRange} escapeRef={escape}>
    <ChartPanes props={props} native={state} />
  </ChartInteraction>;
});

function ChartPanes({ props, native }: {
  props: GroupProps; native: NativeState;
}) {
  const index = props.selection.index ?? props.dataset.bars.length - 1;
  const keys = getVisibleIndicatorKeys(props.preferences);
  const legend = (names: string[]) => names.map((name) => {
    const key = name as keyof ChartDataset['indicators'];
    return <span key={name} title={getIndicatorLabel(key)}>
      {getIndicatorLabel(key)} {formatIndicatorValue(props.dataset, key, index)}
      {props.dataset.indicators[key]?.[index] == null
        ? props.dataset.stats[index]?.leadingPartial
          ? '（计算边界周期不完整）' : '（历史不足）' : ''}
    </span>;
  });
  return (
      <div className="chart-group"
        data-dataset-version={props.dataset.datasetVersion}>
        <ChartPane name="main" label="价格" ref={native.main}>
          {legend(keys.filter(key => /^(ma\d|ema|boll)/.test(key)))}
        </ChartPane>
        <div hidden={!props.preferences.volume}>
          <ChartPane name="volume" label="成交量" ref={native.volume}>
            <span>VOL {formatVolume(props.dataset.bars[index]?.v,
              props.dataset.chart)}</span>
            {legend(['volumeMa5', 'volumeMa10'])}
          </ChartPane>
        </div>
        <div hidden={props.preferences.oscillator === 'none'}>
          <ChartPane name="oscillator" label="技术指标" ref={native.oscillator}>
            {legend(keys.filter(key => /^(macd|rsi|kdj|atr)/.test(key)))}
          </ChartPane>
        </div>
      </div>
  );
}

/** Old servers keep candles without inventing missing statistics or tools. */
function LegacyChart({ bars, indicators }: {
  bars: Bar[]; indicators?: Partial<Indicators>;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      height: 320, width: Math.max(1, container.current.clientWidth),
      layout: { background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#89919d' },
    });
    const candles = chart.addCandlestickSeries({ upColor: '#f2564d',
      downColor: '#2fbf71', wickUpColor: '#f2564d',
      wickDownColor: '#2fbf71', borderVisible: false });
    candles.setData(bars.map(b => ({ time: b.ts as UTCTimestamp,
      open: b.o, high: b.h, low: b.l, close: b.c })));
    (['ma5', 'ma20', 'ma60'] as const).forEach((key, index) => {
      if (!indicators?.[key]?.length) return;
      const line = chart.addLineSeries({ lineWidth: 1,
        color: ['#eab308', '#a78bfa', '#22d3ee'][index] });
      line.setData(bars.map((b, i) => indicators[key]?.[i] == null
        ? { time: b.ts as UTCTimestamp }
        : { time: b.ts as UTCTimestamp, value: indicators[key]![i]! }));
    });
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => chart.applyOptions({
      width: Math.max(1, container.current?.clientWidth ?? 1),
    }));
    observer.observe(container.current);
    return () => { observer.disconnect(); chart.remove(); };
  }, [bars, indicators]);
  return <div ref={container} className="chart-legacy" aria-label="简化 K 线图" />;
}
