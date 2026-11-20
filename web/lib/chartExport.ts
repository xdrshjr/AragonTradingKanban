import { INDICATOR_KEYS, type ChartDataset } from './chartDataset';
import { chartCopy } from './copy.chart';
import { triggerDownload } from './download';
import type { BarsMeta } from './types';

const STAT_COLUMNS = [
  'ts', 'periodStart', 'periodEnd', 'leadingPartial',
  'o', 'h', 'l', 'c', 'v', 'amount',
  'previousClose', 'change', 'changePct', 'amplitudePct',
] as const;

const METADATA_COLUMNS = [
  'symbol', 'currency', 'source', 'requestedAdjust', 'effectiveAdjust',
  'timeframe', 'volumeUnit', 'volumeUnitSize', 'amountCurrency',
  'returnBasis', 'indicatorConvention', 'historyLimited',
  'calculationBarsCount', 'warmupBarsCount', 'cached', 'stale',
  'reasonCode', 'warnings',
] as const;

function encodeCell(value: unknown): string {
  if (value == null) return '';
  let text = String(value);
  if (typeof value === 'string' && /^[\s]*[=+@\-\t\r]/.test(text)) {
    text = `'${text}`;
  }
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function exportMetadata(dataset: ChartDataset): Record<string, unknown> {
  const { chart, meta, instrument } = dataset;
  return {
    symbol: instrument.symbol,
    currency: instrument.currency,
    source: meta.source,
    requestedAdjust: meta.adjust,
    effectiveAdjust: chart.effectiveAdjust,
    timeframe: meta.timeframe,
    volumeUnit: chart.volumeUnit,
    volumeUnitSize: chart.volumeUnitSize,
    amountCurrency: chart.amountCurrency,
    returnBasis: chart.returnBasis,
    indicatorConvention: chart.indicatorConvention,
    historyLimited: chart.historyLimited,
    calculationBarsCount: chart.calculationBarsCount,
    warmupBarsCount: chart.warmupBarsCount,
    cached: meta.cached,
    stale: meta.stale,
    reasonCode: meta.reasonCode ?? '',
    warnings: JSON.stringify(meta.warnings),
  };
}

/** Freeze the complete loaded window as RFC4180 CSV, independent of zoom. */
export function encodeChartCsv(dataset: ChartDataset): string {
  const columns = [...STAT_COLUMNS, ...INDICATOR_KEYS, ...METADATA_COLUMNS];
  const metadata = exportMetadata(dataset);
  const rows = dataset.bars.map((bar, index) => {
    const values: Record<string, unknown> = {
      ...bar, ...dataset.stats[index], ...metadata,
    };
    for (const key of INDICATOR_KEYS) {
      values[key] = dataset.indicators[key][index];
    }
    return columns.map(key => encodeCell(values[key])).join(',');
  });
  return `\uFEFF${[columns.join(','), ...rows].join('\r\n')}\r\n`;
}

/** Generate a portable filename without path or control characters. */
export function chartExportFilename(
  symbol: string,
  meta: Pick<BarsMeta, 'timeframe' | 'adjust'>,
  extension: 'csv' | 'png',
  date = new Date(),
): string {
  const safe = symbol.replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '-')
    .replace(/^[.\s-]+|[.\s-]+$/g, '').slice(0, 80) || 'chart';
  const suffix = `${meta.timeframe}_${meta.adjust}`
    .replace(/[^a-zA-Z0-9_-]/g, '-');
  return `${safe}_${suffix}_${date.toISOString().slice(0, 10)}.${extension}`;
}

function downloadBlob(blob: Blob, filename: string): void {
  let url: string | undefined;
  try {
    url = URL.createObjectURL(blob);
    triggerDownload(url, filename);
  } finally {
    if (url) URL.revokeObjectURL(url);
  }
}

/** Encode at click time, preserving the selected response's raw precision. */
export function downloadChartCsv(
  dataset: ChartDataset,
  filename = chartExportFilename(
    dataset.instrument.symbol, dataset.meta, 'csv',
  ),
): void {
  const csv = encodeChartCsv(dataset);
  downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8' }), filename);
}

export interface ChartPaneCapture {
  canvas: HTMLCanvasElement;
  width: number;
  height: number;
  label: string;
}

export interface ChartPngOptions {
  panes: ChartPaneCapture[];
  title: string;
  subtitle: string;
  source: string;
  watermark?: string;
  pixelRatio?: number;
  background?: string;
  textColor?: string;
}

/** Compose same-task snapshots using CSS dimensions and one bounded DPR. */
export function composeChartPng(options: ChartPngOptions): HTMLCanvasElement {
  if (!options.panes.length) throw new Error('当前没有可导出的图形');
  const requestedRatio = options.pixelRatio ?? globalThis.devicePixelRatio ?? 1;
  const ratio = Number.isFinite(requestedRatio) && requestedRatio > 0
    ? Math.min(requestedRatio, 2) : 1;
  const width = Math.max(...options.panes.map(pane => pane.width));
  const height = 100 + options.panes.reduce((sum, pane) => (
    sum + pane.height + 24
  ), 0);
  if (!(width > 0) || !Number.isFinite(width) || !Number.isFinite(height)) {
    throw new Error('图形尺寸不可用，请重试');
  }
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('浏览器画布不可用，无法导出PNG');
  context.scale(ratio, ratio);
  context.fillStyle = options.background ?? '#11161d';
  context.fillRect(0, 0, width, height);
  context.fillStyle = options.textColor ?? '#e6edf3';
  context.font = 'bold 15px sans-serif';
  context.fillText(options.title, 12, 24, width - 24);
  context.font = '11px sans-serif';
  context.fillText(options.subtitle, 12, 46, width - 24);
  let top = 64;
  for (const pane of options.panes) {
    context.fillText(pane.label, 12, top + 14, width - 24);
    context.drawImage(pane.canvas, 0, top + 24, pane.width, pane.height);
    top += pane.height + 24;
  }
  const watermark = options.watermark
    ?? (options.source === 'synthetic' ? '合成行情 · 仅供演示' : '');
  context.fillText(
    `来源：${options.source} ${watermark}`, 12, height - 16, width - 24,
  );
  return canvas;
}

export interface ChartPngDownloadOptions {
  capture: () => ChartPngOptions;
  filename: string;
  isCurrent: () => boolean;
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error('PNG编码失败，请重试'));
    }, 'image/png');
  });
}

/** Cancel changed/destroyed exports and release every created object URL. */
export async function downloadChartPng(
  options: ChartPngDownloadOptions,
): Promise<void> {
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  if (!options.isCurrent()) throw new Error(chartCopy.pngChanged);
  const canvas = composeChartPng(options.capture());
  const blob = await canvasBlob(canvas);
  if (!options.isCurrent()) throw new Error(chartCopy.pngChanged);
  downloadBlob(blob, options.filename);
}
