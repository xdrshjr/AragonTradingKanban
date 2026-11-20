import type {
  Bar, BarStats, BarsMeta, BarsResponse, ChartDetails,
  ChartView, Indicators, Instrument,
} from './types';

export const INDICATOR_KEYS = [
  'ma5', 'ma10', 'ma20', 'ma60', 'ema12', 'ema26',
  'bollUpper', 'bollMiddle', 'bollLower',
  'macd', 'macdSignal', 'macdHistogram', 'rsi14',
  'kdjK', 'kdjD', 'kdjJ', 'atr14', 'volumeMa5', 'volumeMa10',
] as const;

export type IndicatorKey = typeof INDICATOR_KEYS[number];
export type ChartTool = 'pan' | 'report-range' | 'measure';

export interface ChartSelection {
  index: number | null;
  origin: 'latest' | 'pointer' | 'keyboard' | 'touch';
}

export interface ChartDataset {
  datasetVersion: number;
  instrument: Instrument;
  bars: Bar[];
  indicators: Required<Indicators>;
  stats: BarStats[];
  meta: BarsMeta;
  chart: ChartDetails;
  indexByTimestamp: Map<number, number>;
}

export interface Measurement {
  startIndex: number;
  endIndex: number;
  from: string;
  to: string;
  count: number;
  returnPct: number | null;
  high: number;
  low: number;
  volume: number;
  amount: number | null;
}

export interface ChartRuntimeSnapshot {
  viewport: { from: number; to: number } | null;
  selection: ChartSelection;
  tool: ChartTool;
  measurement: Measurement | null;
}

export interface ChartRuntimeBridge {
  key: string;
  revision: number;
  lastResponse: BarsResponse | null;
  snapshot: ChartRuntimeSnapshot | null;
}

export interface ChartRequestIdentity extends ChartView {
  instrumentId: string;
}

/** A bad new contract must not masquerade as an older server. */
export class ChartContractError extends Error {
  constructor(reason: string) {
    super(`图表数据契约错误：${reason}，请重试`);
    this.name = 'ChartContractError';
  }
}

function requireCondition(value: unknown, reason: string): asserts value {
  if (!value) throw new ChartContractError(reason);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isNullableNumber(value: unknown): boolean {
  return value === null || isFiniteNumber(value);
}

function isDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime())
    && parsed.toISOString().slice(0, 10) === value;
}

function validateBar(bar: Bar, previous?: Bar): void {
  requireCondition(bar && typeof bar === 'object', 'K线结构无效');
  requireCondition(
    Number.isSafeInteger(bar.ts)
      && Number.isFinite(new Date(bar.ts * 1000).getTime()),
    '时间戳无效',
  );
  requireCondition(!previous || bar.ts > previous.ts, '时间戳未严格递增');
  requireCondition([bar.o, bar.h, bar.l, bar.c, bar.v].every(
    isFiniteNumber,
  ), 'OHLCV含非有限值');
  requireCondition(bar.l <= Math.min(bar.o, bar.c)
    && Math.max(bar.o, bar.c) <= bar.h, 'OHLC包络无效');
  requireCondition(bar.v >= 0, '成交量为负');
  requireCondition(bar.amount == null || (
    isFiniteNumber(bar.amount) && bar.amount >= 0
  ), '成交额无效');
}

function validateBasics(
  response: BarsResponse,
  expected: ChartRequestIdentity,
): void {
  requireCondition(response && typeof response === 'object', '响应结构无效');
  requireCondition(response.instrument?.id === expected.instrumentId,
    '标的与请求不一致');
  requireCondition(response.meta?.timeframe === expected.timeframe
    && response.meta?.adjust === expected.adjust, '周期或复权与请求不一致');
  requireCondition(Array.isArray(response.bars), '缺少K线数组');
  requireCondition(response.meta.barsCount === response.bars.length,
    'K线数量与元数据不一致');
  const { bars, meta } = response;
  requireCondition((meta.fromTs ?? null) === (bars[0]?.ts ?? null)
    && (meta.toTs ?? null) === (bars.at(-1)?.ts ?? null), 'K线端点不一致');
  requireCondition(typeof meta.source === 'string'
    && typeof meta.cached === 'boolean'
    && typeof meta.stale === 'boolean', '来源元数据无效');
  requireCondition(Array.isArray(meta.warnings)
    && meta.warnings.every(warning => typeof warning === 'string'),
  '来源警告无效');
  requireCondition(response.indicators
    && typeof response.indicators === 'object', '指标结构无效');
  bars.forEach((bar, index) => validateBar(bar, bars[index - 1]));
}

function validateIndicators(response: BarsResponse): void {
  for (const key of INDICATOR_KEYS) {
    const values = response.indicators[key];
    requireCondition(Array.isArray(values)
      && values.length === response.bars.length, `指标${key}长度不一致`);
    requireCondition(values.every(isNullableNumber), `指标${key}含非有限值`);
  }
}

function validateChartMetadata(chart: ChartDetails): void {
  requireCondition(chart && typeof chart === 'object'
    && chart.version === 'chart/v1', '不支持的图表版本');
  requireCondition(chart.returnBasis === 'adjacent_close'
    && chart.indicatorConvention === 'aragon-chart-v1', '指标约定无效');
  requireCondition(['share', 'lot', 'unit', 'unknown'].includes(
    chart.volumeUnit,
  ), '成交量单位无效');
  const sizes = { share: 1, lot: 100, unit: null, unknown: null };
  requireCondition(chart.volumeUnitSize === sizes[chart.volumeUnit],
    '成交量单位大小无效');
  requireCondition(['none', 'qfq', 'hfq', 'auto', 'synthetic'].includes(
    chart.effectiveAdjust,
  ), '实际复权口径无效');
  requireCondition(typeof chart.amountCurrency === 'string'
    && typeof chart.historyLimited === 'boolean', '图表元数据无效');
  requireCondition(Number.isSafeInteger(chart.calculationBarsCount)
    && chart.calculationBarsCount >= 0
    && Number.isSafeInteger(chart.warmupBarsCount)
    && chart.warmupBarsCount >= 0, '预热计数无效');
}

function validateStats(response: BarsResponse, chart: ChartDetails): void {
  const { stats } = chart;
  requireCondition(Array.isArray(stats)
    && stats.length === response.bars.length, '统计数组长度不一致');
  const numericKeys = [
    'previousClose', 'change', 'changePct', 'amplitudePct',
  ] as const;
  stats.forEach((stat, index) => {
    requireCondition(stat && stat.ts === response.bars[index]!.ts,
      '统计时间戳错位');
    requireCondition(isDate(stat.periodStart) && isDate(stat.periodEnd)
      && stat.periodStart <= stat.periodEnd, '交易日期区间无效');
    if (response.meta.timeframe === '1d') {
      requireCondition(stat.periodStart === stat.periodEnd, '日线区间无效');
    }
    requireCondition(!index || stats[index - 1]!.periodEnd
      < stat.periodStart, '交易周期日期重叠');
    requireCondition(typeof stat.leadingPartial === 'boolean',
      '周期边界标记无效');
    requireCondition(numericKeys.every(key => isNullableNumber(stat[key])),
      '统计包含非有限值');
    if (stat.leadingPartial) {
      requireCondition(index === 0 && numericKeys.every(
        key => stat[key] === null,
      ), '不完整周期不能参与前收统计');
      requireCondition(INDICATOR_KEYS.every(
        key => response.indicators[key]![index] === null,
      ), '不完整周期不能参与指标计算');
    }
  });
  const completeCount = stats.filter(stat => !stat.leadingPartial).length;
  requireCondition(chart.calculationBarsCount
    === chart.warmupBarsCount + completeCount, '预热计数与显示周期不一致');
}

/** Validate once in the shared query before a response can enter its cache. */
export function validateChartResponse(
  response: BarsResponse,
  expected: ChartRequestIdentity,
): BarsResponse {
  validateBasics(response, expected);
  if (response.chart == null) {
    for (const [key, values] of Object.entries(response.indicators)) {
      requireCondition(Array.isArray(values)
        && (values.length === 0 || values.length === response.bars.length)
        && values.every(isNullableNumber), `旧版指标${key}无效`);
    }
    return response;
  }
  validateChartMetadata(response.chart);
  validateIndicators(response);
  validateStats(response, response.chart);
  return response;
}

/** Build indexes for a response already checked by validateChartResponse. */
export function buildChartDataset(
  response: BarsResponse,
  revision: number,
): ChartDataset {
  if (!response.chart) throw new ChartContractError('旧服务无完整数据包');
  return {
    datasetVersion: revision,
    instrument: response.instrument,
    bars: response.bars,
    indicators: response.indicators as Required<Indicators>,
    stats: response.chart.stats,
    chart: response.chart,
    meta: response.meta,
    indexByTimestamp: new Map(response.bars.map((bar, index) => [
      bar.ts, index,
    ])),
  };
}

/** Full packages use server stats; legacy returns keep the prior valid basis. */
export function getLatestChartChange(
  response: BarsResponse | undefined,
): number | null {
  if (response?.chart) return response.chart.stats.at(-1)?.changePct ?? null;
  const close = response?.bars.at(-1)?.c;
  const previous = response?.bars.at(-2)?.c;
  if (close == null || previous == null || previous <= 0) return null;
  const change = (close / previous - 1) * 100;
  return Number.isFinite(change) ? change : null;
}

/** Locate the containing/next trading period, clamping outside the window. */
export function findDateIndex(dataset: ChartDataset, date: string): number {
  if (!isDate(date)) throw new ChartContractError('定位日期无效');
  let left = 0;
  let right = dataset.stats.length;
  while (left < right) {
    const middle = Math.floor((left + right) / 2);
    if (dataset.stats[middle]!.periodEnd < date) left = middle + 1;
    else right = middle;
  }
  return Math.min(left, dataset.stats.length - 1);
}

/** Inclusive local measurement; no report URL or query side effects. */
export function calculateMeasurement(
  dataset: ChartDataset,
  start: number,
  end: number,
): Measurement {
  const startIndex = Math.min(start, end);
  const endIndex = Math.max(start, end);
  requireCondition(Number.isInteger(start) && Number.isInteger(end)
    && startIndex >= 0 && endIndex < dataset.bars.length, '测量索引无效');
  const bars = dataset.bars.slice(startIndex, endIndex + 1);
  const first = bars[0]!;
  const last = bars.at(-1)!;
  const amount = bars.some(bar => bar.amount == null)
    ? null : bars.reduce((sum, bar) => sum + bar.amount!, 0);
  const volume = bars.reduce((sum, bar) => sum + bar.v, 0);
  requireCondition(Number.isFinite(volume)
    && (amount === null || Number.isFinite(amount)), '测量累计值超出范围');
  const returnPct = first.c <= 0 ? null : (last.c / first.c - 1) * 100;
  return {
    startIndex, endIndex,
    from: dataset.stats[startIndex]!.periodStart,
    to: dataset.stats[endIndex]!.periodEnd,
    count: bars.length,
    returnPct: returnPct !== null && Number.isFinite(returnPct)
      ? returnPct : null,
    high: Math.max(...bars.map(bar => bar.h)),
    low: Math.min(...bars.map(bar => bar.l)),
    volume, amount,
  };
}
